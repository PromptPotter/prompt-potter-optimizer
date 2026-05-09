# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleLedger` (`ledger.py`, `events.jsonl`).
Forks via `CycleLedger.inherit_from(parent, offset)`. The writer-side API
above the ledger is `RunCallbacks` (`application/run_callbacks.py`) — a
typed event constructor over `CycleLedger.append`. Orchestration uses
`RunCallbacks`; the ledger is the only thing that touches disk for the
campaign event stream.

**Two newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes |
|---|---|---|
| `LiveDashboardProjection` | root cycle only | `dashboard.json`, `output.log` |
| `AuditTrailProjection` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` |
| `LiveStateProjection` | per cycle | derived live state snapshot |
| `PoBBStreamProjection` | per cycle | PoBB elimination event stream |

`ProjectionBase.on_record` (`projections/base.py`) owns the
`isinstance(record, …)` dispatch; subclasses override hooks. There's no
second dispatch path because the base class is the only one. Subscribers
MUST NOT write campaign artifacts beyond their declared allowlist (guarded
by `tests/test_artifact_parity.py`).

## Stores — composite over leaves

`store/stores.py`: `Stores` frozen dataclass + `build_stores(base_dir)`
builder. Composite over focused leaf stores (`BackendStore`,
`CampaignStore`, `DatasetRunStore`, `PlanStore`, `SessionStore`). Shared
I/O + `EntityStore` in `store/base.py`. Path helpers + `CycleDir` /
`RootCycleDir` newtypes in `store/paths.py` — projections and stores
accept these newtypes, not raw `str`/`Path`. `archive/` is
cross-cycle/session/tenant; `MeasurementArchive` is the DB core.

## LLM client

`llm/client.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter as
instances (no subclasses) parameterized by a `ProviderSpec` registry.
`AnthropicClient` is its peer. SDK `max_retries` handles 503/429 +
Retry-After.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Tracing — fan-out only

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only.
