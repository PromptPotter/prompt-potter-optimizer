# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. Everything
upstream consumes the surfaces declared here — no use case writes to disk
or talks to a network without going through one of these seams.

## Persistence — one ingress, two projections

**Sole ingress:** per-cycle `CycleEventLog` (`ledger.py`, `events.jsonl`).
Forks via `CycleEventLog.inherit_from(parent, offset)`. The writer-side API
above the ledger is `RunCallbacks` (`application/run_callbacks.py`) — a
typed event constructor over `CycleEventLog.append`. Orchestration uses
`RunCallbacks`; the ledger is the only thing that touches disk for the
campaign event stream.

**Two newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes |
|---|---|---|
| `LiveDashboardView` | root cycle only | `dashboard.json`, `output.log` |
| `AuditTrailView` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` |
| `LiveStateProjection` | per cycle | derived live state snapshot |
| `PoBBStreamView` | per cycle | PoBB elimination event stream |

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
lives in `_scan_ledger_max_round_complete` and never instantiates
`CycleEventLog`, so no subscribers fire during admissibility checks.

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
