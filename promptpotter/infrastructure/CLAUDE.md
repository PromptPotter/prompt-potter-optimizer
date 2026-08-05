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

Per-call telemetry firing from deep inside the dispatch chain uses the `emit_*`
shape instead: a kwargs-only helper in `infrastructure/llm/telemetry.py` reads the
active ledger off the per-cycle `_CYCLE_LEDGER` ContextVar (set by
`build_run_observers`, reset by `drain_all`) and appends a typed `*Record` — same
canonical ledger, no process global, no sink-installation indirection. **Which
shape a new surface takes, and the full add-a-surface recipe** — owned by
[`../application/CLAUDE.md`](../application/CLAUDE.md) § Conventions.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardView` | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round (the origin scored) emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for both `backend` and `loop` buckets via `_handle_token_usage`; halt probe reads `spend_total_used_usd` accessor). Sole webapp source for the chart, lineage tree, trend sparkline. |
| `AuditTrailView` | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — full LLM I/O, per-sample results, scoreboard with `per_sample`. Fetched lazily by the webapp (`useRoundFile`) only when an operator drills into a specific round. |
| `PoBBStreamView` | per cycle | `.runtime/streams/round_NNNN_p_best.jsonl` | Per-sample P(best) trajectory for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |

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
file — no `root_cycle_id` collapse. Run-state rides `dashboard.json::run_phase`, declared
by the runner: the old `/runstate` probe inferred "running" from freshness, which was the
symptom that run-state had never been owned state.

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
*, projects_root=…, benchmarks_root=…, shared_root=…)` builder.
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
`store/io.py` — **format follows authorship**: `write_json`/`read_json*` for what
code writes and only code reads (manifests, `dashboard.json`, `cache.json`,
measurements), `write_yaml`/`read_yaml*` for the operator-authored config tier
under `datasets/`, whose block-scalar emitter lives beside them. There is
deliberately no `read_yaml_tolerant` — a corrupt config that degrades to "not
there" attributes a measurement to the wrong fingerprint.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or
`unlink_robust`, its by-arity sibling); **a bare `shutil.rmtree` in this package is a
bug.** It cannot remove the trees this package writes, and with `ignore_errors=True` it
fails *silently* — leaving a half-deleted cycle that later reads as a real one. The
function's docstring carries the measurement and the failure mode; do not restate them
here.

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

## Stores — the rest of the layout

Path helpers in
`store/layout.py`; the per-tenant
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

**`LLMResponse.reasoning` is a core field with no code reader — by design.** It captures
the model's own thinking channel; a model with nowhere to put its internal process
answers without one, so the slot is part of the ask. It rides the ledger payload to
`nodes[*].output.reasoning` (audit twin + live dashboard) and the operator's node-detail
"Thinking" pane, and is **strictly analytical** — never a gate, metric, validator, scorer
or cache key. Do not delete it as write-only surface; read its field note first.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Tracing — fan-out only

`tracing/` exposes no read API. State reaches the optimizer via the
ledger; tracing is fan-out only.
