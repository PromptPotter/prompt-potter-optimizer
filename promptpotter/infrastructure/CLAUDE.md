# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleEventLog` (`ledger.py`, `.runtime/ledger.jsonl`; workspace variant uses `.workspace/events.jsonl`).
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
| `LiveDashboardView` | per cycle | `dashboard.json`, `output.log` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = origin**, a one-candidate round emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for both `backend` and `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundFile`) only when an operator drills into a specific round. |
| `PoBBStreamView` | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |
| `EventStreamView` | per cycle | SSE frames over `GET /campaigns/{c}/cycles/{cy}/events:subscribe` | **Profile A outbound highway** — sole writer of `ProjectionEnvelope` frames (security box 4). Per-cycle ledger subscriber; broadcasts to N HTTP subscribers via per-subscriber asyncio queues bridged from the ledger thread via `loop.call_soon_threadsafe`. Snapshot-then-tail + 15 s heartbeat + sequence-gap detection. Certified contract: [`docs/developer/event-stream.md`](../../docs/developer/event-stream.md). Lookup via process-wide registry (`event_stream/registry.py`); `register_event_stream` at `build_run_observers`, `deregister_event_stream` at `drain_all`. |

`LiveDashboardView` writes into the **cycle's own dir**
(`cycles/{cycle_id}/dashboard.json`) — every cycle (root, fork, sweep, diag)
owns its live stream, stamped with its own `cycle_id`. A fork's view can't
surface the parent's id; a fork seeds its prior trajectory from the parent's
on-disk `dashboard.json` (via `for_session(seed_from_cycle_id=…)`). The write
target is the `CycleDir` newtype (`domain/cycle_paths.py`); the three read sites
(`/api/v1/sessions/active/live-state`, the per-cycle `dashboard` route, `EventStreamView` snapshot)
serve the viewed cycle's own file — no `root_cycle_id` collapse. Run-state rides
`dashboard.json::run_phase` (declared by the runner, projected by
`LiveDashboardView`); the old non-cached `/runstate` probe is gone — its
freshness-based "running" was the symptom that run-state was never owned state.

`DerivedView.on_record` (`projections/base.py`) owns the
`isinstance(record, …)` dispatch; subclasses override hooks. There's no
second dispatch path because the base class is the only one. Subscribers
MUST NOT write campaign artifacts beyond their declared allowlist.

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
contains a closing PhaseRecord for round N (`(phase="round", event="complete")`
— round 0 now closes through the same path as any round via `emit_origin_round`,
so it carries `(phase="round", event="complete", round=0)`; the legacy
`(phase="origin", event="exit")` close is still accepted by the scan). The pure ledger scan
lives in `scan_ledger_max_round_complete` (`store/campaign_store/ledger_scan.py`)
and never instantiates `CycleEventLog`, so no subscribers fire during
admissibility checks.

## Stores — composite over leaves

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity,
*, projects_root=…, datasets_root=…)` builder. `identity` is the
Stage-0 `IdentityContext` (`shared/identity.py`); `Stores.identity` is
the sole source of tenant scope, with `Stores.tenant_id` a derived
`@property` returning the `TenantId` newtype (identity-foundation
no-drift gate #4 — never an independent field). Composite over focused
leaf stores (`BackendStore`, `CampaignStore` (`store/campaign_store/`),
`TenantDatasetStore`, `SessionStore`, `SweepStore`, `MeasurementArchive`,
`OptimizerCallCache`, `DiagnosticRunStore`, `UserStore`). Shared I/O +
`EntityStore` in `store/base.py`. Path helpers in `store/paths.py`; the
`CycleDir` / `WorkspaceDir` write-target newtypes in
`domain/cycle_paths.py` — projections and stores accept these newtypes,
not raw `str`/`Path`. `archive/` is cross-cycle/session/tenant;
`MeasurementArchive` is the DB core.

`CampaignStore` mixes in `CycleOverrideMixin` (`store/campaign_store/overrides.py`):
`write_cycle_seed`/`read_cycle_seed` over `cycles/{id}/.overrides/seed.json` —
the **read-once** per-cycle override home (a steered fork's or campaign-origin's
typed `CycleSeed`, written by `_mint_fork` / the mint seam, read once at the runner seam). Distinct
from `.runtime/{stop,pause,spend_cap}` (the **polled** per-round flags,
`runtime_flags.py`): the dir name encodes read-cadence, so the two never
share a cache path.

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
