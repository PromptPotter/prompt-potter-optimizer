# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleEventLog` (`ledger.py`, `events.jsonl`).
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
| `LiveDashboardView` | session-family root cycle | `dashboard.json`, `output.log` | **Display surface** — origin + completed-round summaries (`dash.rounds[]`) + in-flight `current_round` block + `spend` rollup (sole writer for both `backend` and `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundFile`) only when an operator drills into a specific round. |
| `PoBBStreamView` | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |
| `EventStreamView` | per cycle | SSE frames over `GET /campaigns/{c}/cycles/{cy}/events:subscribe` | **Profile A outbound highway** — sole writer of `ProjectionEnvelope` frames (security box 4). Per-cycle ledger subscriber; broadcasts to N HTTP subscribers via per-subscriber asyncio queues bridged from the ledger thread via `loop.call_soon_threadsafe`. Snapshot-then-tail + 15 s heartbeat + sequence-gap detection. Certified contract: [`docs/developer/event-stream.md`](../../docs/developer/event-stream.md). Lookup via process-wide registry (`event_stream/registry.py`); `register_event_stream` at `build_run_observers`, `deregister_event_stream` at `drain_all`. |

`LiveDashboardView` writes into the **session-family root cycle dir**
(`cycles/{session_root}/dashboard.json`) — a session's forks share their
session root's stream. A campaign therefore carries N live `dashboard.json`
streams, one per session, never one shared at the campaign root. The write
target is the `SessionFamilyDir` newtype (`domain/cycle_paths.py`).

`DerivedView.on_record` (`projections/base.py`) owns the
`isinstance(record, …)` dispatch; subclasses override hooks. There's no
second dispatch path because the base class is the only one. Subscribers
MUST NOT write campaign artifacts beyond their declared allowlist (guarded
by `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores`
+ `test_artifact_sets_are_disjoint_and_well_formed`).

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
or, for round 0, `(phase="origin", event="exit")`). The pure ledger scan
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
`DatasetRunStore`, `PlanStore`, `SessionStore`). Shared I/O +
`EntityStore` in `store/base.py`. Path helpers in `store/paths.py`; the
`CycleDir` / `SessionFamilyDir` write-target newtypes in
`domain/cycle_paths.py` — projections and stores accept these newtypes,
not raw `str`/`Path`. `archive/` is cross-cycle/session/tenant;
`MeasurementArchive` is the DB core.

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
