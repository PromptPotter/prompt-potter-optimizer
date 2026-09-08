# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** the per-cycle `CycleEventLog` (`ledger.py`, `.runtime/ledger.jsonl`). The ledger is the only thing that touches disk for the campaign event stream, and **there is no second ingress, ever.** The writer-side API above it is `RunCallbacks` (`application/run_observers.py`), a typed event constructor over `CycleEventLog.append`; fork mechanics and the crash-atomicity rule are that module's own header.

Per-call telemetry firing from deep inside the dispatch chain uses the `emit_*` shape instead: a kwargs-only helper in `llm/telemetry.py` reads the active ledger off the per-cycle `_CYCLE_LEDGER` ContextVar and appends a typed `*Record` — same canonical ledger, no process global, no sink-installation indirection. **Which shape a new surface takes** — owned by [`../application/CLAUDE.md`](../application/CLAUDE.md) § Conventions.

**The ledger is a CHRONOLOGY, and a payload earns its place only by needing one.** It answers
which round, which candidate, in what order, against which rival — nothing else can. So the test
for a field is not "is it useful?" but *is the ordering what makes it findable?* A value the
archive holds keyed `(dataset_name, node_configs, sample_id)`, or that `rounds/round_NNNN.json`
carries per candidate, is already addressable without it. Two shapes are declared, not optional:
the projection at the writer (`RunCallbacks` → `domain/scoring.py::ledger_sample_view`,
`ViewContext.ledger_anchors`) keeps a record to the union of what its subscribers RENDER, and a
field that is live-only rides `Field(exclude=True)` (`PhaseRecord.data`, `.live_round_result`, and
`ElectionRecord.live_round_result`, which is how the round's own readings reach `current_round` at
the election) so nothing decides per-key at the seam what serializes. Measured before the rule existed: one L2
prompt stored three times, twice in the same file, and 37 of 39 MB of `pipeline_data` was the
archive's own bytes — 102.6 MB of ledger, 56.6% of it duplication.

**A resume-critical fact must be a declared field on the persisted half.** `EscalationFSM.fold` read its L2/L3 counters out of `payload["data"]`, which never reached disk, so every resume rebuilt both layers as never-fired and re-spent budget already spent — no error, just zeros. When a shape moves like that, `application/restamp.py::compact_cycle_ledgers` is where already-written data is lifted across, and it CALLS the writer's projections rather than restating them.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardView` (`projections/live_dashboard/view.py`) | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round (the origin scored) emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for every bucket via `_handle_token_usage`, which picks one through `domain/spend.py::TOKEN_KIND_BUCKET` and folds the totals over `SpendRollup.buckets` — never a hand-named pair, or a new spend kind is money the cap cannot see; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` (`projections/audit_trail.py`) | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundAudit`) only when an operator drills into a specific round; `useRoundFile` is the peer hook for the PUBLIC `rounds/` tree. |
| `PoBBStreamView` (`projections/pobb_stream.py`) | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |

**`dashboard.json` is an operator surface, not a cache, and three guarantees hold at the writer.**
Someone alt-tabbing to the file tree mid-run has to see the truth, so before deferring or skipping
any write, answer whether they still can — and a SERVED read now rests on the same guarantees:
`archive_views::cycle_measurement_series` reads the round in flight off this file, because a round
file lands only at the close and the round being measured has none. It is **always on disk and always swapped atomically**
(tmp + rename — never a partial write or a torn read), present after any ledger event in the cycle.
It **settles within `_DASHBOARD_DEBOUNCE_S` of the last event**: the writer coalesces high-frequency
bursts (sample-scored, token-usage, LLM-call progress) but converges behind real-time by no more
than that constant (`view.py::_schedule_persist`). And it flushes **immediately, with no debounce,
at round boundaries** — `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` go
through `view.py::_flush_pending_persist`, so a round's file is current before the next begins.
Do not relax the swap, remove those flushes, or add a path that lets the file lag past a completed
round. The public round file carries the same atomicity, with `CampaignStore.save_round_file` its
sole writer, persisting `RoundResult.model_dump()` — the model **is** the round document.

**`LiveDashboardView` RESOLVES; it does not hand the browser scalars to join.** `current_round` was `dict[str, Any]` inside an otherwise strict model, and being untyped is why it never had to answer the two questions its only consumer asks — so the webapp inferred both by joining facts written on different ledger events. Five rules follow, each a field or a filter rather than a convention:

- **`active_node` is served**, over a `_STATE_TO_NODE` map TOTAL over `DashboardState` with an import-time exhaustiveness raise. A partial map does not fail loudly; it means "nothing is running", which is a lie for every state it omits.
- **`current_round.round` is `state.round`, always**, so a reader selects this block over the audit twin by equality. There is deliberately no `live` flag beside it.
- **`current_round.nodes` holds only THIS round's blocks.** `_sticky_llm_calls` is most-recent-fire-per-slot and survives round transitions, so it is filtered by each block's own `round`: presence in the served map is the client's whole definition of "this node has fired".
- **A measurement at `NO_ROUND_SLOT` moves the RUN's scalars and not the ROUND's population** — it counts as queries scored and drives the in-flight markers, but skips `_buffer.append_sample` (`shared/instrument.py::NO_ROUND_SLOT`).
- **A live row is the same shape as a closed one** — `DashboardCandidate` and `DashboardSample`, both `domain/dashboard_rows.py`. Two shapes for one entity force the client to merge them field by field, which put a bar and its error whisker on two different polls. **Each field lands at the moment its FACT exists, and none of them is the round close:** the value and its band ride the scoring gateway's own fold (`search_point_scorer::_composite`) on every sample, so the whisker widens with the bar, while the crown, θ and the matched-parent lift ride `ElectionRecord`. θ cannot come sooner and its nullness before the election is a fact rather than a delay — `calibrate_ruler` extends the δ scale onto the round's cells first, and `fit_theta_given_delta` raises on a cell it does not carry.

The **outbound SSE highway is NOT a projection/subscriber** — it *tails* the on-disk
ledger (`projections/event_stream.py::CycleLedgerTail`), **cross-process**: any reader
(API server, CLI, a future MCP client) tails the cycle's `.runtime/ledger.jsonl`
directly, so the stream does not depend on the run living in the reader's own process.
Snapshot-then-tail plus a heartbeat; the ledger line index IS the
`ProjectionEnvelope.sequence`. Certified contract:
[`docs/developer/event-stream.md`](../../docs/developer/event-stream.md).

**Every cycle — root, fork, sweep, diag — owns its live stream** at
`cycles/{cycle_id}/dashboard.json`, stamped with its own id; a fork's view can never
surface the parent's, though it seeds its prior trajectory from the parent's file.
Write target is the `CycleDir` newtype, and the read sites serve the viewed cycle's own
file — no `root_cycle_id` collapse. **`dashboard.json::declared_phase` is the runner's
DECLARATION, never the served answer** — one input to `runtime_flags.py::derive_run_phase`, the
ONE function every surface is served from; the two holding no `index.json` (the dashboard route
and the SSE snapshot) pass neither optional input and let it read both. The file's only writer
lives in the process that dies, so serving it raw read `running` forever after a kill — hence the
key is NAMED as the declaration, and `run_phase` stays on the model `exclude=True`: wire-only, so
the browser's generated type names the field it reads while nothing writes one to disk. The
`running` → `detached` edge moves with the CLOCK, not with a write, so it is expressed once
(`_detached_after`) and the conditional-GET validator reads it from there rather than restating
it — a 304 computed off a second copy outlives the answer it stands for.

**Seeding from that file may not be able to fail the run.** `resolve_resume_state` is the one
reader that turns `dashboard.json` back into state, and the model is `extra="forbid"` — so a file
an earlier build wrote fails on the one field that has since moved. Uncaught, that took down the
resume whose whole job was to not lose the cycle, and then took down `write_launch_stop` as it
tried to stamp why. The prior state is dropped WHOLE and loudly, never salvaged field by field: a
partial read is a compatibility shim, the ledger is the truth, and everything this file carries on
top of it is re-derived forward. The SSE snapshot already answers the same question the same way —
`dashboard_unreadable` is a served reason, not an exception.

`DerivedView.on_record` (`projections/base.py`) owns the dispatch, and that file's header states how. **Subscribers MUST NOT write campaign artifacts beyond their declared allowlist** — it fails loud, since an out-of-allowlist write shows up in the file tree ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

**`--from N` admissibility is a LEDGER question, not a `rounds/` tree question** — and round 0 closes twice, so the scan must take a max. Both rules, and why, are `store/campaign_store/ledger_scan.py`'s header.

## The lineage tree — one timeline per campaign

`store/lineage_views.py` serves the genealogy; nodes alternate `Course -> Candidate -> Course`
at any depth, so L5+ needs no new tier. **A fork is not a node** — its candidates mount onto
the parent's ONE timeline and are renumbered into it, because `C{round}.{n}` is a position in
a course's PRIVATE counter and every course mints its own `C1.1`. **Unless the fork corrects
the parent** (a `supersede` cut): then its attempts take the positions they replaced, and the
retired tail keeps its numbers under `superseded_by`.

Three bounds, each a bug before it was a rule — get them wrong and the tree lies without
erroring:

- **A cut retires only as far as the branch actually GOT.** Retirement is a replacement, not
  a position; a cut is taken before its consequence is known, so `_retired_by` reads the
  branch's own reach back rather than asserting the future. A branch that died retires nothing.
- **Identity outranks direction.** A repair re-measures without re-minting, so a
  `candidate_id` already on the timeline is corrected in place (`equivalent` — the common
  outcome; folding it as a peer rendered a two-candidate round as four) or kept beside its
  withdrawn twin (`supersede` — two measurements of one individual, both facts), never given
  a fresh index. The RUNS are single-homed on the live row: they measured the INDIVIDUAL, and
  a fork's inner runs land in the PARENT's sandbox, so all of them arrive filed under the row
  being replaced. ⑂ marks an OFFSHOOT only — except on a branch that minted nothing, whose
  row is the course as a stand-in and keeps its own provenance.
- **Whichever cut moved the POINTER answers for run-state** — `supersede` and `equivalent`
  both do, `offshoot` alone leaves the parent running. Separate from what a cut retires.

**A round fact lands on the record that OWNS it, and the split is not cosmetic.** Everything
`elect_round_winner` stamps rides `ElectionRecord` — the crown, each arm's θ and its matched-parent
lift (`ElectionRecord.fit`, keyed by MINTING label because a resume re-mints ids) — so the tree
carries the whole verdict a `l1_critique` call before the round closes, and `election_held` is what
separates a round that HELD from one still scoring (`is_winner: false` reads identically for both).
The FRONTIER θ is the exception and stays on `round:complete`: it is RESTAMPED when the ruler warms,
which round 0 reaches twice for exactly that reason, so `LedgerRoundClose.abilities` wins over the
election's copy wherever it answers. Move either half to the other record and nothing raises: round
0 silently reverts to its cold θ, or the served verdict goes late again. The lift is the one that
was got wrong before: `l1_score` stamps it AFTER the ledger's `candidate_scored` snapshot, so
`LedgerCandidate` cannot carry it — declaring it there, which that model's docstring invites, yields
an all-null column on every live run. It rode a fold of the course's own `dashboard.json` rounds
until the election grew a chronology to put it on.

**It is a READ MODEL and decides nothing.** The decision genealogy (`application/mask/`, the
resume replayers) rides positional `(cycle_id, round)` and must not move onto it. What a cut
writes, and why — [`docs/operations/persistence-and-state.md`](../../docs/operations/persistence-and-state.md).

## Stores

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity, *, projects_root=…, benchmarks_root=…, shared_root=…)`. `shared_root` roots every CONTENT-ADDRESSED cache and equals `projects_root` everywhere except an L4 inner sandbox, which isolates campaign state but must NOT isolate a cache keyed by content hash. **`store/layout.py::SHARED_CACHE_DIRS` is the sole enumeration of that set**, because three surfaces have to agree on it and each had authored its own copy — `build_stores` roots them, `cli/commands/reset.py` preserves them, and the workspace storage report counts them as shared. A cache named in one list and not the others is destroyed by `reset` or double-counted, silently, and one of those costs money.

`Stores.identity` is the sole source of tenant scope, with `Stores.tenant_id` a derived `@property` returning the `TenantId` newtype — never an independent field (identity-foundation no-drift gate #4). Composite over the leaf stores `Stores` declares as its own fields, one class per `store/*.py`, except `optimizer_reuse` and `judge_reuse` — two instances of the one `LLMReuseCache` differing only in namespace directory. Separate attributes rather than a shared instance: a grader able to read the loop's cached answers would be a ruler fed by what it measures. **Cite one as attribute → class → file.**

**`store/__init__.py` re-exports nothing** — import each leaf directly. It aggregated all ten eagerly, so any leaf import dragged in `CampaignStore` and cycled back through `runtime_flags` / `ledger`.

Shared I/O in `store/io.py`, and **format follows authorship**: `write_json`/`read_json*` for what code writes and only code reads, `write_yaml`/`read_yaml*` for the operator-authored config tier under `datasets/`. There is deliberately no `read_yaml_tolerant` — a corrupt config degrading to "not there" attributes a measurement to the wrong fingerprint.

Path helpers live in `store/layout.py`, the per-tenant active-session pointer in `store/session_pointer.py`, and derived reads are free functions in view modules (`store/archive_views.py` is the template). `measurements/` is cross-cycle and cross-tenant; `MeasurementArchive` is the DB core and `store/archive_views.py` its single-writer facade — a write not going through that facade is the bug.

The `CycleDir` / `WorkspaceDir` write-target newtypes live in `domain/cycle_paths.py` — projections and stores accept these, not raw `str`/`Path` — as does `CycleHop`, which every per-cycle `CampaignStore` method takes in place of a `(campaign_id, cycle_id)` pair (both `str`, so a swapped call read as "no data" rather than raising). Build it from the carrier that owns both, never by re-pairing.

**`store/account_spend.py` banks what a subject still HOLDS, not what its rows say.** It sums an account's lifetime spend and banks it as a `SpendTombstoneRecord` before a delete takes the rows carrying it. It sits in `infrastructure/` rather than `application/` for exactly that reason: the three destroyers (`delete_campaign`, `try_delete_stub_cycle`, `delete_inner_sandbox`) call it themselves, so no caller can destroy a ledger and skip the bank. An L4 inner cycle forwards onto its outer ledger as it runs and records how far it got in `index.json::forwarded_spend`, so banking the rows whole would bill that money twice; absent mark ⇒ nothing forwarded, which is every cycle outside a sandbox.

**Two read-once ledger records ride `CampaignStore`.** `write_cycle_seed`/`read_cycle_seed` append and scan the cycle seed as a `CycleSeedRecord` (a steered fork's or campaign-origin's typed `CycleSeed`, written by `_mint_fork` or the mint seam, read once at the runner seam; the pure scan is in `ledger_scan.py`, no subscribers fire). A fork inherits the parent's seed record virtually then appends its own, so a scan of the cycle's own ledger returns that cycle's seed.

`write_ruler`/`read_ruler` ride the same shape for a δ ruler (`RulerRecord`, last-wins PER `dataset_name`, appended at lock and after every extension) — **WHOLE each time rather than as a delta**, because `append` is not crash-atomic and a torn line must fall back to a smaller-but-valid scale rather than lose cells silently. It lands BEFORE the round document naming it, since a ruler with unmentioned cells is harmless and a round whose θ nothing can reproduce is the state it exists to end. **One ledger carries more than one**: δ keys are sample ids, which name a sample only within one dataset, and an L4 outer cycle owns a scale over its own cells plus the shared inner one every cell it spawns reads on (`application/runner/inner/ruler.py`). So `copy_rulers` is what a fork lifts, never one of them.

Both are distinct from `.runtime/{skip,pause,spend_cap}`, the **polled** per-checkpoint flags consumed at the next sample boundary rather than held to the round close: one is a durable ledger fact, the others transient.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or
`unlink_robust`, its by-arity sibling); **a bare `shutil.rmtree` in this package is a
bug.** It cannot remove the trees this package writes — an L4 inner sandbox nests langfuse
observation dirs past Windows `MAX_PATH=260` (measured at 668 chars) — and with
`ignore_errors=True` it fails *silently*, leaving a half-deleted cycle that later reads as a
real one. That is how `.inner/` reached 343 MB with no code path able to reclaim it.

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per workspace, and it outlives the campaigns that filled it.** Three consequences, each of which has already been read backwards:

- **A row is filed under the dataset it MEASURED, never under the campaign that paid for it.** On the recursion that is the *inner* benchmark (`datasets/{name}/inner_tasks.yaml::inner_benchmark`) — an inner sandbox isolates campaign state but deliberately shares `shared_root`, so **`promptpotter-self`'s bytes are almost all filed under the inner dataset's name.** Scoping anything by `--dataset promptpotter-self` reaches the outer cells and essentially nothing L4 actually cost. Count before concluding: `compact-archive compact --dataset <name>` dry-runs and prints the split by label.
- **Nothing on a run names a campaign.** The index entry is content, provenance and a label — no `campaign_id`, no `cycle_id`, because a cache hit is supposed to cross campaigns. So "what did this campaign cost on disk" is not a question the archive answers, and the join a surface needs is `LineageNode.sp_hash` → the row's `prompt_fields_id` (`docs/developer/README.md` § Cross-run memory).
- **Cycle state is disposable and the rows are not**, so the rows routinely outlive every campaign that could select them: an emptied `.inner/` leaves its measurements addressable only by dataset. Selecting a family and acting on "what it produced" is therefore a claim about *surviving* state — say so, rather than reporting a smaller number as if it were the whole.

Reversibility is what makes the first two survivable: `compact` keeps every field the δ ruler re-grades from and the replay cache needs, so compacting rows another campaign replays from costs it nothing. Only `purge-cold` needs the attribution, and only it is irreversible.

## Dataset content has two tiers, and only one is writable

**A dataset DEFINITION is install content; everything DERIVED from it is the operator's.**
The definition ships read-only in the wheel (`config/paths.py::benchmark_datasets_root`); the
rows a benchmark materializes and the `task_context.yaml` an LLM decomposed are measurement
inputs the operator paid for, so they land in the tenant tree as flat keyed files
(`benchmark-rows/{name}.json`, `task-context/{name}.yaml`) — never as
`datasets/{name}/cache.json`, which satisfies the resolver's tenant-first rule and shadows the
definition it was fetched for. Both halves resolve on one ladder (`store/dataset_access.py`);
sharing a directory is the only thing that would make the install tier need to be writable.

## Picking a JSON reader is a decision

**`read_json_optional` vs `read_json_tolerant` is a decision, not a preference.**
Tolerant collapses *absent* and *corrupt* into one answer; optional lets corrupt
raise. Use **tolerant** for a cross-cycle SURVEY — walking siblings, building the
lineage tree, sizing a storage report — where one unreadable neighbour must not
fail the whole read. Use **optional** wherever the caller acts differently on the
two, and say which in a comment: `try_delete_stub_cycle` (absent = a stub to
delete, corrupt = a cycle we cannot vouch for), the SSE snapshot (corrupt serves
a `dashboard_unreadable` reason), and the three identity readers, where absent
and malformed are opposite security answers (`check_allowlist` allows on absent
and denies on malformed — collapsing them would fail OPEN). Hand-rolling
`json.loads(path.read_text())` in a `try` is the bug; picking the stricter helper
on purpose is not.

## LLM client

`llm/openai_compat.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter
as instances (no subclasses) parameterized by a `ProviderSpec` registry.
`llm/anthropic.py::AnthropicClient` is its peer. SDK `max_retries` handles 503/429 +
Retry-After.

**Provider selection is always EXPLICIT** — the caller passes it to
`registry.get_llm_client`, sourced from the optimizer node's `config.provider`. No
auto-detection and no env-var fallback: either would make a finished run's provider
unrecoverable from the config that declared it.

**`provider` is the GATEWAY; `route_order` is the HOST behind it, and they are two decisions.**
A gateway fans one model out over many upstream hosts and picks per call, so a prefix cache — which
is per-replica — pays only where one route is hit repeatedly, and a host that does not cache at all
is indistinguishable from a cold one until you read `served_by`. `chat(route_order=[...])` names the
hosts to try in order (`extra_body.provider`, `allow_fallbacks` on, so a dead endpoint degrades the
route rather than failing the run). It is in `hash_call` because it changes WHO answers, not what is
asked, and hosts of one model disagree systematically; and in `PARAM_FORBIDDEN_KEYS` because that
makes it an operator cost lever and never a search axis.

**What a model ACCEPTS and what we MEASURED about it are two tables, kept apart.** `llm/registry.py::_MODEL_PROFILES` is evidence — a reasoning model's `max_tokens` floor, observed from real `reasoning_budget_exhausted` failures — so it ships in the wheel. `llm/capabilities.py` is the provider's own claim, which goes stale on their schedule: resolved operator-override → per-tenant snapshot of OpenRouter's `supported_parameters` → **unknown**, and cached per TENANT because which models an operator asks about is theirs. Merging the two would make one file both evidence and cache with no way to say which layer answered. Two rules bind every reader: an absent answer is `None` and must render as UNKNOWN, never as unsupported — reading it as "no" silently deletes a real search axis; and where the model DOES answer, its ladder REPLACES the node's rather than intersecting with it. A node's list is a default authored before anyone knew which model would run there, so intersecting hid real search positions one way and offered dead ones the other (the measured cases sit on `_EFFORT_PARAM`). The ladder is one case of the general answer — `unsupported_params`, over exactly the keys `openai_compat.chat` puts on the wire — and every producer resolves its menu through `resolve_schema_menu`, never `available_models`: a model the OPERATOR typed rides `param_allowed_values` and by construction reaches no admin catalogue, so asking that one draws a blank card on the surface where the spend is committed.

**`LLMResponse.reasoning` is a core field with no code reader — by design.** It captures
the model's own thinking channel; a model with nowhere to put its internal process
answers without one, so the slot is part of the ask. It rides the ledger payload to
`nodes[*].output.reasoning` (audit twin + live dashboard) and the operator's node-detail
"Thinking" pane, and is **strictly analytical** — never a gate, metric, validator, scorer
or cache key. Do not delete it as write-only surface; read its field note first.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Tracing — fan-out only, and DORMANT ON PURPOSE

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only.

**It has no in-repo reader by design, and that is not evidence it is dead — do not propose deleting it.** The Langfuse and MLflow sinks are held for a live integration the operator is bringing up; `LANGFUSE_*` defaulting to `""` and `MLFLOW_ENABLED=False` are an integration not switched on, not a feature nobody wanted. That `events.jsonl` is never read back for state reconstruction is what a trace sink IS — resume and fork are driven by the round files.

This note sits here rather than only in `mlflow_sink.py`'s docstring because the subtree reads as ~2,300 lines of dead code to every sweep that measures deadness by counting readers, and has been proposed for deletion repeatedly. If it is *badly written*, refactor it; absence of a reader is not the reason.

## Identity — the OIDC foundation

`identity/` holds the sign-in machinery: provider config + the two issuers
(`google.py`, `github.py`), `verifier.py`/`jwks.py`, `allowlist.py`, `grants.py`,
browser `session.py`, `user.py`, and `migration.py` (the first web sign-in RENAMES
`projects/default/` to `projects/{user_id}/`). It builds the Stage-0 `IdentityContext`
that `build_stores` takes; the capability vocabulary that reads it lives one layer out
in `shared/identity.py`. **The access model itself is a constitution, not a layer
note** — boundaries, capabilities and enforcement are owned by
[`docs/adr/0002-identity-foundation.md`](../../docs/adr/0002-identity-foundation.md) and
[`docs/operations/access-model.md`](../../docs/operations/access-model.md).
