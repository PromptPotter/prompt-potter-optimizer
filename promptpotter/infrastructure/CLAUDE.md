# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleEventLog` (`ledger.py`, `.runtime/ledger.jsonl`).
Forks via `CycleEventLog.inherit_from(parent, offset)`. The writer-side API
above the ledger is `RunCallbacks` (`application/run_observers.py`) — a
typed event constructor over `CycleEventLog.append`. Orchestration uses
`RunCallbacks`; the ledger is the only thing that touches disk for the
campaign event stream.

Per-call telemetry that fires from deep inside the dispatch chain (today:
`TokenUsageRecord` from both the optimizer LLM-call site and the backend
per-step site in `application/scoring/sample_measurement.py`) uses the
`emit_*` shape instead of `RunCallbacks`: a kwargs-only helper in
`infrastructure/llm/models.py` reads the active ledger from a per-cycle
`ContextVar` (`_CYCLE_LEDGER`, set by `build_run_observers`, reset by
`drain_all`) and appends a typed `*Record`. Same canonical ledger, no
process global, no sink-installation indirection. New per-call surfaces
follow the same pattern.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardView` | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round (the origin scored) emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for both `backend` and `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundFile`) only when an operator drills into a specific round. |
| `PoBBStreamView` | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |

The **Profile A outbound SSE highway is NOT a projection/subscriber** — it is
served by *tailing* the on-disk ledger (`projections/event_stream.py::CycleLedgerTail`)
over `GET /campaigns/{c}/cycles/{cy}/events:subscribe`, **cross-process**: any
reader (the API server, the CLI, a future MCP client) tails the cycle's
`.runtime/ledger.jsonl` directly, so the stream no longer depends on the run
living in the reader's own process. Snapshot-then-tail (leading `stream_snapshot`
from `dashboard.json`, read as-is) + 15 s heartbeat; the ledger line index is the
`ProjectionEnvelope.sequence`. The route 404s only for an unknown cycle. Certified
contract: [`docs/developer/event-stream.md`](../../docs/developer/event-stream.md).

`LiveDashboardView` writes into the **cycle's own dir**
(`cycles/{cycle_id}/dashboard.json`) — every cycle (root, fork, sweep, diag)
owns its live stream, stamped with its own `cycle_id`. A fork's view can't
surface the parent's id; a fork seeds its prior trajectory from the parent's
on-disk `dashboard.json` (via `for_session(seed_from_cycle_id=…)`). The write
target is the `CycleDir` newtype (`domain/cycle_paths.py`); the read sites
(the per-cycle `dashboard` route, the SSE ledger-tail's snapshot frame) serve
the viewed cycle's own file — no `root_cycle_id` collapse. Run-state rides
`dashboard.json::run_phase` (declared by the runner, projected by
`LiveDashboardView`); the old non-cached `/runstate` probe is gone — its
freshness-based "running" was the symptom that run-state was never owned state.

`DerivedView.on_record` (`projections/base.py`) owns the
`isinstance(record, …)` dispatch; subclasses override hooks. There's no
second dispatch path because the base class is the only one. Subscribers
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
`emit_origin_round`, so it carries `(phase="round", event="complete", round=0)`. The pure ledger scan
lives in `scan_ledger_max_round_complete` (`store/campaign_store/ledger_scan.py`)
and never instantiates `CycleEventLog`, so no subscribers fire during
admissibility checks.

## Stores — composite over leaves

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity,
*, projects_root=…, datasets_root=…, shared_root=…)` builder.
`shared_root` roots the two CONTENT-ADDRESSED caches (`archive`,
`optimizer_calls`) and equals `projects_root` everywhere except an L4
inner sandbox, which isolates campaign state but must NOT isolate a
cache keyed by content hash. `identity` is the
Stage-0 `IdentityContext` (`shared/identity.py`); `Stores.identity` is
the sole source of tenant scope, with `Stores.tenant_id` a derived
`@property` returning the `TenantId` newtype (identity-foundation
no-drift gate #4 — never an independent field). Composite over ten focused
leaf stores: `backends` (`BackendStore`), `tenant_datasets`, `sessions`,
`campaigns` (`store/campaign_store/`), `checkin` (`CheckinDraftStore`),
`sweeps`, `archive` (`MeasurementArchive`), `optimizer_calls`
(`OptimizerCallCache`), `diagnostic_runs`, `users`. Shared I/O in
`store/io.py`; path helpers in `store/layout.py`; the per-tenant
active-session pointer in `store/session_pointer.py`; derived reads are free
functions in view modules (`store/archive_views.py` is the template — it is
also the archive's single-writer facade). **`store/__init__.py` re-exports
nothing** — import each leaf directly. It aggregated all ten eagerly, so any
leaf import dragged in `CampaignStore` and cycled back through `runtime_flags`
/ `ledger`; three back-edges were cut to dodge that before the aggregator
itself went. The
`CycleDir` / `WorkspaceDir` write-target newtypes live in
`domain/cycle_paths.py` — projections and stores accept these newtypes,
not raw `str`/`Path`. `archive/` is cross-cycle/cross-tenant;
`MeasurementArchive` is the DB core.

`CampaignStore` (`store/campaign_store/store.py`) exposes
`write_cycle_seed`/`read_cycle_seed`, which append/scan the **read-once** cycle
seed as a `CycleSeedRecord` on the cycle's ledger (a steered fork's or
campaign-origin's typed `CycleSeed`, written by `_mint_fork` / the mint seam,
read once at the runner seam; the pure scan lives in `ledger_scan.py`, no
subscribers fire). The seed rides the replayable spine — a fork inherits the
parent's seed record virtually then appends its own, so a scan of the cycle's
own ledger returns that cycle's seed. Distinct from `.runtime/{skip,pause,spend_cap}`
(the **polled** per-checkpoint flags — consumed at the next sample boundary, NOT
held to the round close; a `pause.flag` written mid-candidate pauses within seconds,
`runtime_flags.py`): one is a durable ledger fact, the others are transient flags.

## LLM client

`llm/openai_compat.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter
as instances (no subclasses) parameterized by a `ProviderSpec` registry.
`llm/anthropic.py::AnthropicClient` is its peer. SDK `max_retries` handles 503/429 +
Retry-After.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Tracing — fan-out only

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only.
