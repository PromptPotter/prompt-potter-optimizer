# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleEventLog` (`ledger.py`, `.runtime/ledger.jsonl`).
Forks via `CycleEventLog.inherit_from(parent, offset)`, and the cut is STAMPED —
`index.json::forked_at_offset`, read back by `ledger.py::branch_offset`. It wrote
nothing until then, so where a fork's history began was known only inside the
forking process; `forked_from_round` is a round and `forked_at` a clock, and
neither addresses a ledger. A fork's own FILE still holds only its own appends —
the parent's prefix is walked, not copied. Anything a fork must answer for ITSELF is appended to it: a
repair's corrected rounds reach the branch via `repair.py::_rebank_on_branch`,
because a round file written with no ingress behind it is invisible to every
scan and readers silently fall back to the parent. The writer-side API
above the ledger is `RunCallbacks` (`application/run_observers.py`) — a
typed event constructor over `CycleEventLog.append`. Orchestration uses
`RunCallbacks`; the ledger is the only thing that touches disk for the
campaign event stream.

Per-call telemetry firing from deep inside the dispatch chain uses the `emit_*`
shape instead: a kwargs-only helper in `infrastructure/llm/telemetry.py` reads the
active ledger off the per-cycle `_CYCLE_LEDGER` ContextVar (set by
`build_run_observers`, reset by `drain_all`) and appends a typed `*Record` — same
canonical ledger, no process global, no sink-installation indirection. **Which
shape a new surface takes, and the full add-a-surface recipe** — owned by
[`../application/CLAUDE.md`](../application/CLAUDE.md) § Conventions.

**The ledger is a CHRONOLOGY, and a payload earns its place only by needing one.** It answers
which round, which candidate, in what order, against which rival — nothing else can. So the test
for a field is not "is it useful?" but *is the ordering what makes it findable?* A value the
archive holds keyed `(dataset_name, node_configs, sample_id)`, or that `rounds/round_NNNN.json`
carries per candidate, is already addressable without it. Two shapes are declared, not optional:
the projection at the writer (`RunCallbacks` → `domain/scoring.py::ledger_sample_view`,
`ViewContext.ledger_anchors`) keeps a record to the union of what its subscribers RENDER, and a
field that is live-only rides `Field(exclude=True)` (`PhaseRecord.data`, `.live_round_result`) so
nothing decides per-key at the seam what serializes. Measured before the rule existed: one L2
prompt stored three times, twice in the same file, and 37 of 39 MB of `pipeline_data` was the
archive's own bytes — 102.6 MB of ledger, 56.6% of it duplication.

**A resume-critical fact must be a declared field on the persisted half.** `EscalationFSM.fold`
read its L2/L3 counters out of `payload["data"]`, which never reached disk, so every resume
rebuilt both layers as never-fired and re-spent budget already spent — no error, just zeros.
They are fields on `L2RefineExitView` / `PlanExitView` now. When a shape moves like that,
`application/restamp.py::compact_cycle_ledgers` is where already-written data is lifted across:
it CALLS the writer's projections rather than restating them, preserves the line count (the line
index IS `sequence`), tmp + `os.replace` (`append` is not crash-atomic), and compacts only
`_COMPACTABLE_PHASES` — a fresh producer and a pre-loop check-in are both skipped, and
counted apart, because only the first one clears on its own.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardView` (`projections/live_dashboard/view.py`) | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round (the origin scored) emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for both `backend` and `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` (`projections/audit_trail.py`) | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundAudit`) only when an operator drills into a specific round; `useRoundFile` is the peer hook for the PUBLIC `rounds/` tree. |
| `PoBBStreamView` (`projections/pobb_stream.py`) | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |

**`dashboard.json` is an operator surface, not a cache, and three guarantees hold at the writer.**
Someone alt-tabbing to the file tree mid-run has to see the truth, so before deferring or skipping
any write, answer whether they still can. It is **always on disk and always swapped atomically**
(tmp + rename — never a partial write or a torn read), present after any ledger event in the cycle.
It **settles within `_DASHBOARD_DEBOUNCE_S` of the last event**: the writer coalesces high-frequency
bursts (sample-scored, token-usage, LLM-call progress) but converges behind real-time by no more
than that constant (`view.py::_schedule_persist`). And it flushes **immediately, with no debounce,
at round boundaries** — `PhaseRecord("round"|"origin", "complete"|"exit")` and `mark_stopped` go
through `view.py::_flush_pending_persist`, so a round's file is current before the next begins.
Do not relax the swap, remove those flushes, or add a path that lets the file lag past a completed
round. The public round file carries the same atomicity, with `CampaignStore.save_round_file` its
sole writer, persisting `RoundResult.model_dump()` — the model **is** the round document.

**`LiveDashboardView` RESOLVES; it does not hand the browser scalars to join.** `current_round`
was `dict[str, Any]` inside an otherwise strict model, and being untyped is why it never had to
answer the two questions its only consumer asks — so the webapp inferred both, each by joining
facts written on different ledger events. Four rules follow, each a field or a filter rather than
a convention:

- **`active_node` is served**, over a `_STATE_TO_NODE` map that is TOTAL over `DashboardState`
  with an import-time exhaustiveness raise. A partial map does not fail loudly; it means "nothing
  is running", which is a lie for every state it omits.
- **`current_round.round` is `state.round`, always** — so a reader selects this block over the
  audit twin by equality. There is deliberately no `live` flag beside it.
- **`current_round.nodes` holds only THIS round's blocks.** `_sticky_llm_calls` is
  most-recent-fire-per-slot and survives round transitions, so it is filtered by each block's own
  `round`: presence in the served map is the client's whole definition of "this node has fired".
- **A live row is the same shape as a closed one** — candidates (`DashboardCandidate`) and
  samples (`DashboardSample`), both `domain/dashboard_rows.py`. Two shapes for one entity force
  the client to merge them field by field, which put a bar and its error whisker on two
  different polls, and made it regex a rendered tape to recover the row the other branch of the
  same key already held. **Each field lands at the moment its FACT exists, and none of them is the
  round close:** the value and its band at `candidate_scored`, the crown on its own
  `ElectionRecord`, written where the election runs. (θ stays null on a live row — it needs the round's joint fit,
  which is a different fact, not a late one.) A field held back to `round:display` surfaces
  whenever the next node happens to finish, which is not a time the operator can read anything into.

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

`DerivedView.on_record` (`projections/base.py`) owns the dispatch, off a `_ROUTES`
table checked against the `CycleRecord` union at import — so an arm that names no
hook is a DECLARED silence carrying its reason, never one that fell off the end of a
chain. Subclasses override hooks. There's no second dispatch path because the base
class is the only one. Subscribers
MUST NOT write campaign artifacts beyond their declared allowlist (fails
loud — an out-of-allowlist write shows up in the file tree; see
[`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

`DerivedView.drain()` is the runner's teardown seam: `_finalize_run` calls
`RunObservers.drain_all()` on every stop reason so buffered projection
state is flushed to disk without faking a `round:complete`. `AuditTrailView`
is the only projection that buffers — its `drain()` writes the partial
`round_NNNN.json` with `"interrupted": true` at top level when the cycle
was torn down on Ctrl+C. The public `rounds/` tree stays empty for
interrupted rounds by design (a partial round is not a complete round);
the audit cache under `.runtime/cache/rounds/` carries the partial so
post-mortem readers can see what the ledger has.

`CampaignStore.rewind_to_round` consults the ledger (not the public
`rounds/` tree) for admissibility: `--from N` is valid iff the ledger
contains a closing PhaseRecord for round N — `(phase="round", event="complete")`,
the one closing signature. Round 0 closes through the same path as any round via
`emit_origin_round`, so it carries `(phase="round", event="complete", round=0)` — **twice**: it
closes again when the ruler warms at round 1 (`runner/loop.py`), since the origin's θ cannot be fit
before a second arm exists, and only that SECOND record carries the usable θ. A max-scan is safe; a
count, a first-match, or a reader that updates on `display` alone is not. `max()` over
`scan_ledger_round_closes` (`store/campaign_store/ledger_scan.py`) answers it — a second scan
asking only for the maximum was the same pass under another name. It never instantiates
`CycleEventLog`, so no subscribers fire during admissibility checks.

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

**A round fact lands on the record that OWNS it, and the split is not cosmetic.** The crown is
stamped once, by `elect_round_winner`, and rides its own `ElectionRecord` — so the tree crowns a
whole `l1_critique` call before the round closes, and `election_held` is what separates a round
that HELD from one still scoring (`is_winner: false` reads identically for both). θ and the
frontier are RESTAMPED when the ruler warms, so they stay on `round:complete`, which round 0
reaches twice for exactly that reason. Move either half to the other record and nothing raises:
round 0 silently reverts to its cold θ, or the served crown goes late again. The election's
**lift** is the third case and the one with a trap: `l1_score` stamps it AFTER the ledger's
`candidate_scored` snapshot is written, so `LedgerCandidate` cannot carry it — declaring it
there, which that model's docstring invites, yields an all-null column on every live run and a
value only on repaired rounds. It is folded from the course's own `dashboard.json` rounds,
joined on the MINTING label (`_lifts`).

**It is a READ MODEL and decides nothing.** The decision genealogy (`application/mask/`, the
resume replayers) rides positional `(cycle_id, round)` and must not move onto it. What a cut
writes, and why — [`docs/operations/persistence-and-state.md`](../../docs/operations/persistence-and-state.md).

## Stores — composite over leaves

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity,
*, projects_root=…, benchmarks_root=…, shared_root=…)` builder.
`shared_root` roots the two CONTENT-ADDRESSED caches (`measurements/`,
`optimizer_reuse/`) and equals `projects_root` everywhere except an L4
inner sandbox, which isolates campaign state but must NOT isolate a
cache keyed by content hash. `identity` is the
Stage-0 `IdentityContext` (`shared/identity.py`); `Stores.identity` is
the sole source of tenant scope, with `Stores.tenant_id` a derived
`@property` returning the `TenantId` newtype (identity-foundation
no-drift gate #4 — never an independent field). Composite over the leaf stores
`Stores` declares as its own fields — one attribute each, one class per
`store/*.py`, except `optimizer_reuse`, which `stores.py` defines inline.
**Cite one as attribute → class → file**: the attribute is what a call site
shows you, the file is what you have to open. Shared I/O in
`store/io.py` — **format follows authorship**: `write_json`/`read_json*` for what
code writes and only code reads (manifests, `dashboard.json`, `cache.json`,
measurements), `write_yaml`/`read_yaml*` for the operator-authored config tier
under `datasets/`, whose block-scalar emitter lives beside them. There is
deliberately no `read_yaml_tolerant` — a corrupt config that degrades to "not
there" attributes a measurement to the wrong fingerprint.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or
`unlink_robust`, its by-arity sibling); **a bare `shutil.rmtree` in this package is a
bug.** It cannot remove the trees this package writes — an L4 inner sandbox nests langfuse
observation dirs past Windows `MAX_PATH=260` (measured at 668 chars) — and with
`ignore_errors=True` it fails *silently*, leaving a half-deleted cycle that later reads as a
real one. That is how `.inner/` reached 343 MB with no code path able to reclaim it.

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

## Stores — path helpers, spend banking, the cycle seed

Path helpers in
`store/layout.py`; the per-tenant
active-session pointer in `store/session_pointer.py`; derived reads are free
functions in view modules (`store/archive_views.py` is the template).
`store/account_spend.py` is the
same shape over the ledgers: it sums an account's lifetime spend, and it
BANKS that spend as a `SpendTombstoneRecord` before a delete takes the rows
carrying it. It sits here rather than in `application/` for exactly that
reason — the three destroyers (`delete_campaign`, `try_delete_stub_cycle`,
`delete_inner_sandbox`) call it themselves, so no caller can destroy a ledger
and skip the bank. **`bank_spend` banks what a subject still HOLDS, not what
its rows say:** an L4 inner cycle forwards onto its outer ledger as it runs and
records how far it got in `index.json::forwarded_spend`, so banking the rows
whole would bill that money twice. Absent mark ⇒ nothing forwarded, which is
every cycle outside a sandbox.
**`store/__init__.py` re-exports
nothing** — import each leaf directly. It aggregated all ten eagerly, so any
leaf import dragged in `CampaignStore` and cycled back through `runtime_flags`
/ `ledger`; three back-edges were cut to dodge that before the aggregator
itself went. The
`CycleDir` / `WorkspaceDir` write-target newtypes live in
`domain/cycle_paths.py` — projections and stores accept these newtypes,
not raw `str`/`Path` — as does `CycleHop`, which every per-cycle
`CampaignStore` method takes in place of a `(campaign_id, cycle_id)`
pair (both `str`, so a swapped call read as "no data" rather than
raising). Build it from the carrier that owns both, never by re-pairing. `measurements/` is cross-cycle/cross-tenant;
`MeasurementArchive` (`store/measurement_archive.py`) is the DB core, and
`store/archive_views.py` is its single-writer facade — a write that does not
go through that facade is the bug.

`CampaignStore` (`store/campaign_store/store.py`) exposes
`write_cycle_seed`/`read_cycle_seed`, which append/scan the **read-once** cycle
seed as a `CycleSeedRecord` on the cycle's ledger (a steered fork's or
campaign-origin's typed `CycleSeed`, written by `_mint_fork` / the mint seam,
read once at the runner seam; the pure scan lives in `ledger_scan.py`, no
subscribers fire). The seed rides the replayable spine — a fork inherits the
parent's seed record virtually then appends its own, so a scan of the cycle's
own ledger returns that cycle's seed. `write_ruler`/`read_ruler` ride the same shape for the
cycle's δ ruler (`RulerRecord`, last-wins, appended at lock and after every extension) — WHOLE
each time rather than as a delta, because `append` is not crash-atomic and a torn line must fall
back to a smaller-but-valid scale rather than lose cells silently; it lands BEFORE the round
document naming it, since a ruler with unmentioned cells is harmless and a round whose θ nothing
can reproduce is the state it exists to end. Distinct from `.runtime/{skip,pause,spend_cap}`
(the **polled** per-checkpoint flags — consumed at the next sample boundary, NOT
held to the round close; a `pause.flag` written mid-candidate pauses within seconds,
`runtime_flags.py`): one is a durable ledger fact, the others are transient flags.

## LLM client

`llm/openai_compat.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter
as instances (no subclasses) parameterized by a `ProviderSpec` registry.
`llm/anthropic.py::AnthropicClient` is its peer. SDK `max_retries` handles 503/429 +
Retry-After.

**Provider selection is always EXPLICIT** — the caller passes it to
`registry.get_llm_client`, sourced from the optimizer node's `config.provider`. No
auto-detection and no env-var fallback: either would make a finished run's provider
unrecoverable from the config that declared it.

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

**It has no in-repo reader by design, and that is not evidence it is dead — do not propose deleting
it.** The Langfuse and MLflow sinks are held for a live integration the operator is bringing up;
`LANGFUSE_*` defaulting to `""` and `MLFLOW_ENABLED=False` are an integration not yet switched on,
not a feature nobody wanted. `file_sink.py` says `events.jsonl` is never read back for state
reconstruction, which is true and is what a trace sink IS — resume and fork are driven by the round
files, and that separation is the design.

This note exists because the subtree reads as ~2,300 lines of dead code to every sweep that measures
deadness by counting readers, and has been proposed for deletion repeatedly. The intent was already
written down in `mlflow_sink.py`'s module docstring ("Kept on purpose even when off") — inside a
63-line file no sweep opens. Progressive disclosure only works where the reader actually lands.
`docs/specs/code-debt-cleanup.md` carries the matching scar ("a 'dead' field that mlflow reads").
If it is *badly written*, refactor it; absence of a reader is not the reason.

## Identity — the OIDC foundation

`identity/` holds the sign-in machinery: provider config + the two issuers
(`google.py`, `github.py`), `verifier.py`/`jwks.py`, `allowlist.py`, `grants.py`,
browser `session.py`, `user.py`, and `migration.py` (the first web sign-in RENAMES
`projects/default/` to `projects/{user_id}/`). It builds the Stage-0 `IdentityContext`
that `build_stores` takes; the capability vocabulary that reads it lives one layer out
in `shared/identity.py`. **The access model itself is a constitution, not a layer
note** — tiers, boundaries and enforcement are owned by
[`docs/adr/0002-identity-foundation.md`](../../docs/adr/0002-identity-foundation.md) and
[`docs/operations/access-model.md`](../../docs/operations/access-model.md).
