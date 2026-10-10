# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. No use case writes to disk
or talks to a network except through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** the per-cycle `CycleEventLog` (`ledger.py`, `cycles/{cycle_id}/.runtime/ledger.jsonl`). **There is no second ingress, ever.** The writer-side API above it is `RunCallbacks` (`application/run_observers.py`), a typed event constructor over `CycleEventLog.append`. A fork's file holds only its own appends: the parent's prefix is WALKED from the cut its `CycleMintedRecord` names, never copied. `append` is not crash-atomic, and a rewrite goes tmp + `os.replace` keeping the line count, since the line index IS `sequence`.

Per-call telemetry firing from deep inside the dispatch chain uses the `emit_*` shape instead (`llm/telemetry.py`) — same canonical ledger. **Which shape a new surface takes** — owned by [`../application/CLAUDE.md`](../application/CLAUDE.md) § Conventions.

**The ledger is a CHRONOLOGY, and a payload earns its place only by needing one.** The test
for a field is *is the ordering what makes it findable?* A value the
archive holds addressed by `(node_configs, sample_key)` is already addressable without it —
which is why a round's close carries each row's ADDRESS and never the row. The projection at the writer
(`RunCallbacks` → `domain/scoring.py::GradedCell.ledger_wire`, `ViewContext.anchors`) keeps a record to
the union of what its subscribers RENDER.

**A record has no live-only half.** Every field is declared and persisted, so a subscriber folding live and a reader folding off disk hold the same value at every offset; a subscriber that renders a whole round folds its `RoundClosedRecord`, and `RoundStandingRecord` after it carries what only the close's aftermath knows. A fact one fold needs and the record lacks is added to the record — never handed past it in memory, where a resume sees nothing: no error, just zeros, and budget re-spent. `application/maintenance/restamp.py::compact_cycle_ledgers` re-projects written records by CALLING the writer's projections, and lifts no retired shape across.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardProjection` (`projections/live_dashboard/projection.py`) | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for every bucket via `_handle_token_usage` → `SpendRollup.bank`, which keys one by the record's kind and folds the totals over `SpendRollup.by_kind` — never a hand-named pair, or a new spend kind is money the cap cannot see; a run's spend book is seeded off the `spend_metered` accessor, in the units its ceiling meters, when it is armed). Sole webapp source for the chart and trend sparkline; the lineage tree reads the ledger instead. |
| `AuditTrailProjection` (`projections/audit_trail.py`) | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — each optimizer node's full LLM I/O and the round's warnings; the file IS `domain/round_audit.py::RoundAudit` dumped, and every reader loads it through that model. Fetched lazily by the webapp (`useRoundNodes`) only when an operator drills into a specific round; `useRound` is the peer hook for the round itself, served typed off the ledger (`GET …/cycles/{cycle_id}/rounds/{round_num}`) — never the round-file checkout. |
| `RacingStreamProjection` (`projections/racing_stream.py`) | per cycle | `.runtime/streams/round_NNNN_{member}.jsonl` | Per-sample race standing under the eliminator `member` names, for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |
| `ReadoutProjection` (`application/views/readout.py` — it renders through `views/render/`, so it sits a layer up) | per cycle / fork | `readout.log` | **The run readout** — the ledger stream as lines, ANSI-stripped, appended per launch. Bound at every entry point; one with a terminal hands in a line sink and sees the styled line too. |

**`dashboard.json` is an operator surface, not a cache, and three guarantees hold at the writer.**
An operator opening the file tree mid-run must see the truth: before deferring or skipping
a write, answer whether they still can — and a SERVED read rests on the same guarantees
(a served dashboard is this file, validated; a cell never is). It is **always on disk and always swapped atomically**
(tmp + rename — never a partial write or a torn read), present after any ledger event in the cycle.
It **settles within `_DASHBOARD_DEBOUNCE_S` of the last event** (`projection.py::_schedule_persist`
coalesces bursts). And it flushes **immediately, with no debounce, at round boundaries**
(`projection.py::_flush_pending_persist`), so a round's file is current before the next begins.
Do not relax the swap, remove those flushes, or add a path that lets the file lag past a completed
round. The public round file carries the same atomicity, with
`application/scoring/cells.py::RoundFileProjection` the tree's sole writer — a ledger subscriber,
so a close, a restated state and a rewind each move it from the record. The model **is** the
round file, and it has no reader in this package or above it: the file is the operator's.

**A round IS its last `RoundClosedRecord`, and the round file is a checkout of it.** The close
carries the whole outcome and the archive address of every row; a resume, a fork, the index and
every served or rendered read take the rounds that STAND off the ledger chain (`ledger_scan.py::scan_standing_rounds`, rows
through `application/scoring/cells.py::closed_rounds`), never off the round files. A second writer of round truth — a file a
resume reads, a copy a fork carries — is the bug.

**`LiveDashboardProjection` RESOLVES; it does not hand the browser scalars to join** from facts written on different ledger events. Five rules, each a field or a filter rather than a convention:

- **`active_node` is served**, by a `match` TOTAL over `DashboardState` (`assert_never`), naming the nodes the ledger recorded at `propose:enter` / `measure:enter` / an optimizer step's enter rather than any optimizer's literal. A partial answer does not fail loudly; it means "nothing is running", which is a lie for every state it omits.
- **`current_round.round` is `state.round`, always**, so a reader selects this block over the audit twin by equality. There is deliberately no `live` flag beside it.
- **`current_round.nodes` holds only THIS round's optimizer calls.** `_sticky_llm_calls` is most-recent-fire-per-slot and survives round transitions, so it is filtered by the round each block fired in: presence in the served map is the client's whole definition of "this node has fired". The measurement is not a node block: its tape and searchpoints ride `current_round.candidates`.
- **A measurement at `NO_ROUND_SLOT` moves the RUN's scalars and not the ROUND's population** — it counts as queries scored and drives the in-flight markers, but skips `_buffer.append_sample` (`shared/measurement_context.py::NO_ROUND_SLOT`).
- **A live row is the same shape as a closed one** — `DashboardCandidate` and `DashboardSample`, both `domain/dashboard_rows.py`; `LiveCandidate` only adds what a closed round keeps in its round file. Two shapes for one entity force the client to merge them field by field. **Each field lands at the moment its FACT exists, and none of them is the round close:** the value and its band ride the scoring gateway's own fold (`search_point_scorer::_composite`) on every sample, so the whisker widens with the bar, while the crown, θ and the matched-parent lift ride `ElectionRecord`. θ cannot come sooner and its nullness before the election is a fact rather than a delay — `calibrate_ruler` extends the δ scale onto the round's cells first, and `fit_theta_given_delta` raises on a cell it does not carry.

The **outbound SSE highway is NOT a projection/subscriber** — it *tails* the on-disk
ledger (`projections/event_stream.py::CycleLedgerTail`), **cross-process**, so the stream
does not depend on the run living in the reader's own process. Contract:
[`docs/developer/event-stream.md`](../../docs/developer/event-stream.md).

**Every cycle — root, fork, diag — owns its live stream** at
`cycles/{cycle_id}/dashboard.json`, stamped with its own id; a fork's view can never
surface the parent's; read sites serve the viewed cycle's own file — no `root_cycle_id` collapse. **`dashboard.json::declared_phase` is a MIRROR of the runner's
declaration and no served read parses it.** The declaration is a ledger record (`RunPhaseRecord`),
and `runtime_flags.py::derive_run_state` — the ONE function every surface and every guard is
answered from, phase and producer (`ProducerReading`) together — reads
the last one off the ledger index. Its only writer lives in the process that dies, so served raw
it reads `running` forever after a kill; `run_phase` is `ServedDashboard`'s
(`application/served_dashboard.py`) — the fold's served facts (`DashboardFacts`; what
`LiveDashboardState` declares beside them is an input to a served reading and is not served) plus
what only a read can say, built by one function for the head and a replay alike, and never on disk.
**Whether a producer is attached is its lock on the cycle (`producer_lock.py::cycle_held`) and
nothing else**; file time only grades an attached one. That grade's edge moves with the CLOCK, not
with a write, so it is expressed once (`_beat_stale_after`) and the conditional-GET validator
reads it from there — a 304 computed off a second copy outlives the answer it stands for.

**Seeding from that file may not be able to fail the run, so nothing seeds from it.**
`resolve_resume_state` folds the seed cycle's LEDGER — a projection is never an input to itself,
and no field on this model is on-disk shape anyone must migrate. The one reader that DOES parse the
file (`served_dashboard.py::_head_state`; the model is `extra="forbid"`) drops a state that does
not read as this build's WHOLE and loudly and refolds the ledger (`projection.py::fold_at`) —
never salvaged field by field: a partial read is a compatibility shim, and the ledger is the
truth. **What a cycle runs under is a ledger record too** (`RunWiringRecord`, declared at the
mint that freezes its config and again by each launch — `run_observers.py::declare_run_wiring`), so the
fold needs nothing a prior file carried, and **a state exists only from that record on**: a
cycle with none (a check-in) folds to `None` and serves `WarmingDashboard`.

`Projection.on_record` (`projections/base.py`) owns the dispatch. **Subscribers MUST NOT write campaign artifacts beyond their declared allowlist** — it fails loud, since an out-of-allowlist write shows up in the file tree ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

**`--from N` admissibility is that same LEDGER question, never a `cycles/{cycle_id}/rounds/` tree question** — and round 0 closes twice, so a scan takes the LAST close. **Every `store/campaign_store/ledger_scan.py::scan_*` lets an `OSError` past an absent file raise** — "unreadable" answering "nothing stands" has a resume or rewind act on an empty history — and none instantiates `CycleEventLog`, so no subscriber fires during a check.

## The lineage tree — one timeline per campaign

`store/lineage_queries.py` serves the genealogy; nodes alternate `Course -> Candidate -> Course`
at any depth, so L5+ needs no new tier. **A fork is not a node** — its candidates mount onto
the parent's ONE timeline and are renumbered into it, because `C{round}.{n}` is a position in
a course's PRIVATE counter and every course mints its own `C1.1`. **Unless the fork corrects
the parent** (a `supersede` cut): then its attempts take the positions they replaced, and the
retired tail keeps its numbers under `superseded_by`.

Three bounds — get them wrong and the tree lies without erroring:

- **A cut retires only as far as the branch actually GOT** — `_retired_by` reads the branch's
  own reach back rather than asserting the future. A branch that died retires nothing.
- **Identity outranks direction.** A repair re-measures without re-minting, so a
  `candidate_id` already on the timeline is corrected in place (`equivalent` — the common
  outcome) or kept beside its
  withdrawn twin (`supersede` — two measurements of one individual, both facts), never given
  a fresh index. The RUNS are single-homed on the live row: they measured the INDIVIDUAL, and
  a fork's inner runs land in the PARENT's sandbox, so all of them arrive filed under the row
  being replaced. ⑂ marks an OFFSHOOT only — except on a branch that minted nothing, whose
  row is the course as a stand-in and keeps its own provenance.
- **Whichever cut moved the POINTER answers for run-state** — `supersede` and `equivalent`
  both do, `offshoot` alone leaves the parent running. Separate from what a cut retires.

**A round fact lands on the record that OWNS it.** Everything
`elect_round_winner` stamps rides `ElectionRecord` — the crown, each arm's θ and its matched-parent
lift (`ElectionRecord.fit`, keyed by MINTING label) — so the tree
carries the whole verdict before the adapters run and the round closes, and `ArmElection.held` is what
separates a round that HELD from one still scoring (`selected: false` reads identically for both).
The FRONTIER θ is the exception and stays on `RoundClosedRecord`: it is RESTAMPED when the ruler warms,
which round 0 reaches twice for exactly that reason, so the close's `candidate_scores` win over the
election's copy wherever they answer. Move either half to the other record and nothing raises: round
0 silently reverts to its cold θ, or the served verdict goes late. **The lift may not go on
`LedgerCandidate`**: the election stamps it AFTER the ledger's
`candidate_scored` record, so it would be an all-null column on every live run.

**It is a READ MODEL and decides nothing.** The decision genealogy (`application/mask/`, the
resume replayers) rides positional `(cycle_id, round)` and must not move onto it. What a cut
writes, and why — [`docs/operations/persistence-and-state.md`](../../docs/operations/persistence-and-state.md).

## Stores

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity, *, projects_root=…, benchmarks_root=…, shared_root=…)`. `shared_root` roots every CONTENT-ADDRESSED cache and equals `projects_root` everywhere except an L4 inner sandbox, which isolates campaign state but must NOT isolate a cache keyed by content hash, nor the dataset tier (`tenant_datasets`) its inner benchmark resolves through. **`store/layout.py::SHARED_CACHE_DIRS` is the sole enumeration of that set**, because three surfaces must agree on it — `build_stores` roots them, `application/maintenance/reset.py` preserves them, and the workspace storage report counts them as shared. A cache named in one list and not the others is destroyed by `reset` or double-counted, silently, and one of those costs money.

`Stores.identity` is the sole source of tenant scope, with `Stores.tenant_id` a derived `@property` returning the `TenantId` newtype — never an independent field (identity-foundation no-drift gate #4). Composite over the leaf stores `Stores` declares as its own fields, one class per `store/*.py`, except `optimizer_reuse` and `judge_reuse` — two instances of the one `LLMReuseCache` (`store/llm_reuse_cache.py`, beside `hash_call`, its key) differing only in namespace directory. Separate attributes rather than a shared instance: a grader able to read the loop's cached answers would be a ruler fed by what it measures. **Cite one as attribute → class → file.**

**`store/__init__.py` re-exports nothing** — import each leaf directly, so a leaf import never drags in `CampaignStore` and cycles back through `runtime_flags` / `ledger`.

Shared I/O in `store/io.py`, and **format follows authorship**: `write_json`/`read_json*` for what code writes and only code reads, `write_yaml`/`read_yaml*` for the operator-authored config tier under `datasets/`. There is deliberately no `read_yaml_tolerant` — a corrupt config degrading to "not there" attributes a measurement to the wrong fingerprint.

Path helpers live in `store/layout.py`, the per-tenant active-session pointer in `store/session_pointer.py`, and derived reads are free functions in query modules (`store/archive_queries.py` is the template). **`store/read_model.py::append_row` / `fold_jsonl*` / `LedgerIndex` are the only derived-index persistence primitives**; a second mechanism is the bug. `measurements/` is cross-cycle and cross-campaign **within one tenant** — `build_stores` roots it and every `SHARED_CACHE_DIRS` peer at `shared_root / identity.tenant_id`, so content-addressing makes a row shareable across campaigns and into an L4 sandbox, never across accounts. `MeasurementArchive` is the DB core — ONE instance per `base_dir` per process (`MeasurementArchive.at`), so readers share its index tail — and `store/archive_queries.py` the facade a run files through and where a controlled line's memory scope is applied — a MEMORY read past it is the bug; `application/maintenance/` rewrites the archive itself.

The `CycleDir` / `WorkspaceDir` write-target newtypes live in `domain/cycle_paths.py` — projections and stores accept these, not raw `str`/`Path` — as does `CycleHop`, which every per-cycle `CampaignStore` method takes in place of a `(campaign_id, cycle_id)` pair (both `str`, so a swapped call read as "no data" rather than raising). Build it from the carrier that owns both, never by re-pairing.

**`store/account_spend.py` banks what a subject still HOLDS, not what its rows say.** It sums an account's lifetime spend and banks it as a `SpendTombstoneRecord` before a delete takes the rows carrying it. It sits in `infrastructure/` rather than `application/` for exactly that reason: the three destroyers (`delete_campaign`, `try_delete_stub_cycle`, `delete_inner_sandbox`) call it themselves, so no caller can destroy a ledger and skip the bank. An L4 inner cycle's calls are carried onto its outer ledger as they settle, and its own copies are flagged `mirrored`, which the sum skips — banking them would bill that money twice.

**Three read-once ledger records ride `CampaignStore`.** `write_cycle_seed`/`read_cycle_seed` append and scan the cycle seed as a `CycleSeedRecord`, read once at the runner seam. A fork inherits the parent's seed record virtually then appends its own, so a scan of the cycle's own ledger returns that cycle's seed.

`write_ruler`/`read_ruler` ride the same shape for a δ ruler (`RulerRecord`, last-wins PER `dataset_name`) — **WHOLE each time rather than as a delta**, because `append` is not crash-atomic and a torn line must fall back to a smaller-but-valid scale, and appended BEFORE the round file naming it. **One ledger carries more than one** (δ keys name a sample only within one dataset, and an L4 outer cycle also carries the shared inner scale — `application/runner/inner/ruler.py`), and a fork copies none: `read_ruler` scans the chain the fork continues (`ledger.py::continued_chain`), so the parent's scales are the fork's until it appends its own.

`write_run_limits`/`read_run_limits` carry the operator's standing ceiling (`RunLimitsRecord`, last-wins), scanned physically so a fork never inherits it. The record is the ONE store of the ceiling and of the job's reservation beside it: a launch reads it and so does a run in flight (`runtime_flags.py::standing_run_limits`, an incremental fold), so there is no mirror file. **A command for a running loop is read the same way** — `runtime_flags.py::standing_controls`, the fold of the cycle's own `CommandRecord`s and acks (`ledger_scan.py::Controls`) — so a cycle's `.runtime` holds no control file and no lifecycle flag.

**A cycle's own facts are ledger records too, and `index.json` is their fold.** Its mint (`CycleMintedRecord` — parent, cut offset and `ForkSpec` for a fork, and whether it was minted in check-in, which `CheckinClosedRecord` ends), a launch's claim on it and that claim's release, its ending (the last `RunPhaseRecord` `terminal`), its result (`CycleFinalRecord`), a supersede, a measured fork direction, an operator intervention and an L4 spawn are each one append; `projections/cycle_index.py::read_cycle_index` is the ONE reading of them (`CampaignStore.load`), and `write_cycle_index` the file's one writer. Nothing reads the file, and `ledger_chain` finds a fork's parent on the mint record, never in it.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or
`unlink_robust`, its by-arity sibling); **a bare `shutil.rmtree` in this package is a
bug.** It cannot remove the trees this package writes — an L4 inner sandbox nests paths past
Windows `MAX_PATH=260` — and with `ignore_errors=True` it fails *silently*, leaving a
half-deleted cycle that later reads as a real one.

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per workspace, and it outlives the campaigns that filled it.** Three consequences:

- **A row is filed under the dataset it MEASURED, never under the campaign that paid for it.** On the recursion that is the *inner* benchmark (`datasets/{name}/inner_tasks.yaml::inner_benchmark`) — an inner sandbox isolates campaign state but deliberately shares `shared_root`, so **`promptpotter-self`'s bytes are almost all filed under the inner dataset's name.** Scoping anything by `--dataset promptpotter-self` reaches the outer cells and essentially nothing L4 actually cost. Count before concluding: `compact-archive inventory --dataset <name>` prints configurations, answers and bytes by dataset, role and age.
- **Nothing on an answer names a campaign.** It carries its configuration, its dataset, its role and its grade — no `campaign_id`, no `cycle_id`, no run, because a cache hit is supposed to cross campaigns; which walk took which answer is the ledger's to say. So "what did this campaign cost on disk" is not a question the archive answers, and the join a surface needs is `ArmNode.reading.sp_hash` → the row's `prompt_fields_id` (`docs/developer/README.md` § Cross-run memory).
- **Cycle state is disposable and the rows are not**, so the rows routinely outlive every campaign that could select them: an emptied `.promptpotter/.inner/` leaves its measurements addressable only by dataset. Selecting a family and acting on "what it produced" is therefore a claim about *surviving* state — say so, rather than reporting a smaller number as if it were the whole.

Reversibility is what makes the first two survivable: `compact` keeps every field the δ ruler re-grades from and the replay cache needs, so compacting rows another campaign replays from costs it nothing. Only `purge-cold` needs the attribution, and only it is irreversible.

## Dataset content has two tiers, and only one is writable

**A dataset DEFINITION is install content; everything DERIVED from it is the operator's.**
The definition ships read-only in the wheel (`config/paths.py::benchmark_datasets_root`); the
rows a benchmark materializes and the `task_context.yaml` an LLM decomposed are measurement
inputs the operator paid for, so they land in the tenant tree as flat keyed files
(`benchmark-rows/{name}.json`, `task-context/{name}.yaml`) — never as
`datasets/{name}/cache.json`, which satisfies the resolver's tenant-first rule and shadows the
definition it was fetched for. Both halves resolve on one ladder (`store/dataset_access.py::readable_dataset_rows`; `TenantDatasetStore.save_benchmark_rows` writes the rows);
sharing a directory is the only thing that would make the install tier need to be writable.

## Picking a JSON reader is a decision

`read_json_tolerant` collapses *absent* and *corrupt* into one answer; `read_json_optional` lets corrupt
raise. Use **tolerant** for a cross-cycle SURVEY — walking siblings, building the
lineage tree, sizing a storage report — where one unreadable neighbour must not
fail the whole read. Use **optional** wherever the caller acts differently on the
two, and say which in a comment: `try_delete_stub_cycle` (absent = a stub to
delete, corrupt = a cycle we cannot vouch for), the served dashboard (absent folds the
ledger in silence, corrupt folds it with a warning), and the three identity readers, where absent
and malformed are opposite security answers (`check_blocklist` admits on absent
and blocks everyone on malformed — collapsing them would fail OPEN). Hand-rolling
`json.loads(path.read_text())` in a `try` is the bug.

## LLM client

`llm/openai_compat.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter
as instances (no subclasses) parameterized by a `ProviderSpec` registry.
`llm/anthropic.py::AnthropicClient` is its peer.

**No paid request is sent unadmitted, and `cost_usd` holds only what a provider REPORTED** — a
call it reported nothing for is priced at our rate in `rate_priced_usd`, which a ceiling counts
and no surface calls spent; the hold / bill / unreported rules are [ADR-0003](../../docs/adr/0003-spend-and-tenancy.md)'s. Every paid path goes through it:
`LLMClientBase._admitted_send`, Harbor's in-process agent send by send (`llm/litellm_sends.py`, so
its cell only RESERVES the run it DECLARES), and `BackendClient.run_query` for a remote cell. **Every
attempt, ours or one a backend or library made for us, ends as ONE `SendOutcome` settled by
`Admission.close`** — a path reading a status or an exception itself is a second classifier. SDK
retries are off — each resend is admitted anew, and drawn from the ONE `llm/send_pacing.py::SendBudget`
its unit of work's OWNER sized and opened (a cell, an optimizer call, a grading, a probe): a loop
counting its own attempts multiplies under every loop around it, so a send under no budget raises
(`drawn_budget`). A caller meters
only a cache replay; a diagnostic verb binds its ledger through `loop_start.py::diagnostic_trace`.

**Provider selection is always EXPLICIT** — the caller passes it to
`registry.get_llm_client`, sourced from the optimizer node's `config.provider`. No
auto-detection and no env-var fallback: either would make a finished run's provider
unrecoverable from the config that declared it.

**`provider` is the GATEWAY; `route_order` is the HOST behind it, and they are two decisions.**
A per-replica prefix cache pays only where one route is hit repeatedly, and a host that does not
cache is indistinguishable from a cold one until you read `served_by`. `ChatRequest.route_order`
names the hosts to try in order, with fallbacks on — each the gateway's `provider_name` as read off `served_by`, never its catalogue, whose `supports_implicit_caching` misreports caching hosts. It is in `hash_call` because it changes WHO answers, not what is
asked, and hosts of one model disagree systematically; and in `PARAM_FORBIDDEN_KEYS` because that
makes it an operator cost lever and never a search axis.

**What a model ACCEPTS and what we MEASURED about it are two tables, kept apart and COMPOSED, never merged.** `llm/registry.py::_MODEL_PROFILES` is evidence and ships in the wheel; `llm/capabilities.py` is the provider's own claim, resolved highest first — the tenant's hand-authored `model_capabilities.yaml`, `_MODEL_PROFILES`, the fetched per-tenant snapshot, then nothing — in which **the measured evidence narrows the claim and can never widen it**. **A price is no layer of it**: the card's two prices are `llm/pricing.py::lookup_rate` for the node's provider, which is why `model_capabilities` is keyed `provider -> model`.

Two rules bind every reader: an absent answer is `None` and must render as UNKNOWN, never as unsupported — reading it as "no" silently deletes a real search axis; and where the model DOES answer, its ladder REPLACES the node's rather than intersecting with it. A node's list is a default authored before anyone knew which model would run there. **A CAMPAIGN narrowing is the exception and intersects** — that one is a deliberate closing an ADR-0005 capability gates, so the model may still strike a rung it refuses but may never hand back one the operator took away; `PipelineNode.param_values_narrowed` is what tells the two apart once `narrow()` has merged them into one dict. An axis left with NO legal value is `[]`, never `None`: a reader testing it falsy turns an over-narrowed axis into an unbounded one. **`PipelineSchema.param_options` is the ONE applier for the ENGINE** — the L1 wire enum, the param catalogue and `overlay_failures` all read it, and `model` stops being a special case at each. **The SCREEN must not apply it**: `NodeConfigParam.permitted` is what this campaign DECLARED, because the config editor emits it straight back as the narrowing (`nodeConfig.ts::nodeNarrowing`) — serve the resolved space there and a repaint bakes one model's refusals into the operator's own declaration, while an axis resolving to nothing returns as a closed one. The browser shows the two facts apart instead: the ticks are the declaration, and a rung the model refuses renders ticked AND struck off `model_capabilities`. The ladder is one case of `unsupported_params` (over exactly the keys `openai_compat.chat` puts on the wire; a REFUSED key leaves the axis only the value it is running), and every producer resolves its menu through `resolve_schema_menu`, never `available_models`: a model the OPERATOR typed rides `param_allowed_values` and by construction reaches no admin catalogue, so asking that one draws a blank card on the surface where the spend is committed.

**`LLMResponse.reasoning` is a core field with no code reader — by design.** It captures
the model's own thinking channel for the operator's node-detail "Thinking" pane, and is **strictly analytical** — never a gate, metric, validator, scorer
or cache key. Do not delete it as write-only surface; read its field note first.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Docker host

`docker_host.py` is the ONE `docker` CLI call, daemon probe, package cache, labelled cell
container, producer scratch and dead-producer sweep. **Taking a machine slot claims the machine**
(`backend.py::MachineSlots.hold`), so a connector calls no claim: it declares `compose_overlay`,
keeps scratch under `PRODUCER_SCRATCH` and starts a container of its own only through
`run_cell_container`.

## Tracing — fan-out only, and DORMANT ON PURPOSE

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only — and it is fanned out FROM the ledger:
`tracing/bridge.py::TracingProjection` is a subscriber like the projections above, each sink a
`tracing/events.py::TraceSink` taking ledger records. Nothing in the loop emits to a sink, so a
span the bench's records imply exists for every optimizer.

**It has no in-repo reader by design, and that is not evidence it is dead — do not propose deleting it.** The Langfuse and MLflow sinks are held for a live integration the operator is bringing up; `LANGFUSE_*` defaulting to `""` and `MLFLOW_ENABLED=False` are an integration not switched on, not a feature nobody wanted. If it is *badly written*, refactor it; absence of a reader is not the reason.

## Identity — the OIDC foundation

`identity/` holds the sign-in machinery (`migration.py`: the first web sign-in RENAMES
`.promptpotter/projects/default/` to `.promptpotter/projects/{user_id}/`). It builds the Stage-0 `IdentityContext`
that `build_stores` takes; the capability vocabulary that reads it lives one layer out
in `shared/identity.py`. **The access model itself is a constitution, not a layer
note** — boundaries, capabilities and enforcement are owned by
[`docs/adr/0002-identity-foundation.md`](../../docs/adr/0002-identity-foundation.md) and
[`docs/operations/access-model.md`](../../docs/operations/access-model.md).
