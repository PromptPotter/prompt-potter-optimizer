# Adding a surface — golden-path recipes

Expansion in PromptPotter is **fill-in-the-blank, CI-guarded**. Each surface
below has a fixed set of edits and a contract test that fails the build when you
wire only half of it. The pre-flight gate (root `CLAUDE.md`) and the six
per-layer `CLAUDE.md` files say *what* the rules are; this page says *where you
type* and *which test catches you* if you miss a half.

The pattern every recipe shares: **one registry is the source of truth, an
import-time or AST contract proves nothing fell out of it.** A capability can't
silently disappear because the registry is code-derived and the test walks it.

| You want to add… | Recipe | CI guard |
|---|---|---|
| A telemetry event / ledger record | [§1](#1-a-ledger-record--telemetry-event) | `test_every_cycle_record_is_dispatched_or_control_plane` |
| A prompt injection (`{{slot}}`) | [§2](#2-a-prompt-injection) | `test_every_injection_renderer_is_wired` |
| A dashboard / view field | [§3](#3-a-dashboard--view-field) | `test_round_complete_view_roundtrip` |
| A resume / decision checkpoint | [§4](#4-a-resumedecision-checkpoint-kind) | `test_every_decision_kind_has_a_gating_entry` |
| A connector (backend) | [§5](#5-a-connector-backend) | `test_every_connector_implements_protocol` |
| An optimizer node | [§6](#6-an-optimizer-node) | `validate_template()` at module load |
| A measurement field | [developer README §4](README.md#4-cross-run-memory) | `MEASUREMENTS_SCHEMA_VERSION` bump |

---

## 1. A ledger record / telemetry event

**First, the one decision** (this is the rule that used to be tribal): there are
two writer shapes, and they are *not* interchangeable — pick by whether the call
site holds an explicit ledger handle.

| If the fact originates… | Use | Why |
|---|---|---|
| in the **runner**, which owns the observers and threads per-cycle `ViewContext` state across events (phase enter/exit, round complete, per-candidate / per-sample snapshots) | **`RunCallbacks`** method (`application/run_observers.py`) | The runner has the ledger as an explicit dependency and the phase path is **stateful** — `from_phase_event` mutates a `ViewContext` round-over-round. Owned state, explicit injection. |
| **deep in the async LLM / dispatch chain**, with no ledger handle in scope (token usage, an LLM-call marker, a command ack, a crash, a self-healed round warning) | **`emit_*`** helper (`infrastructure/llm/models.py`) | Stateless: kwargs in, append out. Reads the ledger from the `_CYCLE_LEDGER` ContextVar (set by `build_run_observers`, reset by `drain_all`) — the ContextVar exists *because* these sites can't be handed a handle. |

Do **not** fold one into the other: routing the runner's `RunCallbacks` through
`emit_*` would force its explicit `ViewContext` into an ambient ContextVar
(implicit mutable global), and routing `emit_*` through `RunCallbacks` is
impossible (the deep sites have nothing to call it on).

**Recipe (either shape):**

1. Define `XxxRecord` in `domain/run_records.py` and add it to the `CycleRecord`
   discriminated union.
2. Add an `isinstance(record, XxxRecord)` arm + matching `_handle_xxx` hook
   (default no-op) to `DerivedView.on_record` (`infrastructure/projections/base.py`).
3. **Writer:** either add a typed method on `RunCallbacks`, **or** a kwargs-only
   `emit_xxx` helper in `infrastructure/llm/models.py` that calls `_append_record`.
4. Override `_handle_xxx` on each projection that surfaces the fact
   (`LiveDashboardView` for `dashboard.json`, `AuditTrailView` for `round_NNNN.json`,
   `LiveDisplay` for the CLI). Unhandled = silently dropped — which is exactly
   what the guard prevents.

**Guard (no standing test — the structural suite was cut, see
[`tests/CLAUDE.md`](../../tests/CLAUDE.md)):** a union member with no `on_record`
arm is silently dropped from every projection, so check the arm exists when you
add the record (control-plane records `CommandRecord` / `CommandAckRecord` are
the allowlisted exception — applied by `CommandDispatcher`, not projected). A
missing arm breaks loud in use (the fact never reaches `dashboard.json`).

Contract: [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) §
"Per-call telemetry", [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md)
§ "Persistence — one ingress".

---

## 2. A prompt injection

A `{{slot}}` the optimizer LLM sees. The registry is `INJECTIONS`
(`application/optimization/dispatch/hub/injections/registry.py`); every renderer
is a pure `(InjectionBundle) -> str`.

**Recipe:**

1. Write a `_r_<name>(bundle) -> str` renderer in `dispatch/hub/injections/`
   (returns `""` when its source field is empty — empty injections are skipped).
2. Decorate it with `@signal("<name>", kind=…, description=…)` — registration
   happens at the definition site; key and body are co-located, no separate
   `INJECTIONS` edit.
3. To make it reachable, add it to the node's `NODE_LAYOUTS[node].possible`
   (and `.floor` to put it on by default — for `l1_generate` these alias
   `L1_POSSIBLE`/`L1_MANDATORY`), or use `{{<name>}}` directly in a template.

**Guard (import-time, no standing test):** the registry guard in `registry.py`
fails loud at import if a `possible` name has no registered renderer, and
`validate_template()` (at `load_optimizer_prompt`) raises at module load on any
`{{slot}}` not in `INJECTIONS` — typos fail loud.

Contract: [`developer/dispatch-hub.md`](dispatch-hub.md) § L1 layout,
[`developer/dispatch-hub.md`](dispatch-hub.md).

---

## 3. A dashboard / view field

A field on a phase view (the live CLI render + `dashboard.json`). Since the
typed-view roundtrip collapsed (the producer hands the **typed** view onto the
ledger fan-out and Pydantic serializes it for disk/SSE), there is **no
reconstructor to keep in sync** — that synchronized third edit is gone.

**Recipe:**

1. Add the field to the `*View` frozen dataclass in
   `application/views/view_models.py`.
2. Set it in the live builder `_<phase>_<event>` in
   `application/views/ingress.py` (`from_phase_event`).
3. Render it in `presentation/views/render/text.py` (`to_text`) /
   `application/views/render/` (`to_markdown`) and/or
   read it where the fact is surfaced — `LiveDashboardView._apply_phase` reads
   the typed view by attribute (`getattr`, presentation-agnostic).
4. If the field also appears in post-hoc `log.md`, set it in `from_disk_round` /
   `from_disk_log` (`application/output/writers.py`) — this builder reads on-disk
   `round_NNNN.json` for **cross-cycle** rendering and is a genuinely separate
   source, not a roundtrip shim.

**Guard:** the two-factories-onto-one-View correctness invariant — the live
builder and the disk builder must produce an equal `RoundCompleteView`. No
standing test (a broken round-trip surfaces as a wrong/empty dashboard; the
structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md).

---

## 4. A resume / decision checkpoint kind

A replayable or archival decision (`ResumeCheckpointKind` + its gating mode).

**Recipe:**

1. Add the kind to `ResumeCheckpointKind` and a gating entry to
   `RESUME_CHECKPOINT_GATING` (`domain/run_records.py`) — import-time
   exhaustiveness raises before the module loads if you add one without the other.
2. If replayable, add it to the replayer; if archival, leave it out (the two
   guards below enforce the split).
3. Emit it through `record_decision` with the typed kind, never a bare string.

**CI guards:** `test_every_decision_kind_has_a_gating_entry`,
`test_replayed_kinds_have_a_replayer` / `test_archival_kinds_have_no_replayer`,
`test_no_bare_string_decision_kinds`, `test_divergence_hint_lists_every_decision_kind`.

---

## 5. A connector (backend)

A new backend kind. Intentionally local — **no edits to `application/config.py`
or `infrastructure/backend.py`.**

**Recipe:**

1. Write `connectors/<name>.py` exporting a `CONNECTOR = Connector(...)` binding
   (`connectors/protocol.py`): wire adapter `(query, pipeline_params) -> dict`,
   `extract_experiment -> (queries, index_terms)`, session hooks (noop for
   in-process backends).
2. Declare `execution` — `remote_http` (default, posts to `/matches`) or
   `in_process` (runs an inner cycle, L4). `BackendClient.run_query` dispatches
   on this **declared mode, never the connector name**, so transport stays a
   capability rather than a core-loop branch.
3. Add the import + a row to the `CONNECTORS` dict in `connectors/__init__.py`.
   No `register()` call, no import side effects.
4. Optional: set `expected_revision` + a `version_check` hook for cross-repo
   drift warnings at bootstrap.

**CI guard:** `test_every_connector_implements_protocol` walks `CONNECTORS` and
fails the build if any row is half-wired — registry key ≠ `name`, a non-callable
hook, a session factory that doesn't build a `SessionProtocol`, or an `execution`
outside `ConnectorExecution`. Beyond that, an unknown connector raises `KeyError`
at `get()`, and an `in_process` connector raises a pointed `NotImplementedError`
at `run_query`. The execution-mode *declaration* + dispatch seam + completeness
guard are in place; what's deferred is only the inner-cycle **run** itself
(the decided in-process recursion + the three proxy metrics) — Lane C3 /
[`specs/l4-outer-loop.md`](../specs/l4-outer-loop.md).

Contract: [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md).

---

## 6. An optimizer node

One of the five LLM nodes (`l1_generate`, `l1_critique`, `l2_context`,
`l3_plan`, `checkin`). The JSON declaration format and registry live in
[`developer/node-standard.md`](node-standard.md); response models in
`dispatch/schemas.py::OPTIMIZER_RESPONSE_MODELS`. A node renders a
`PromptTemplate` through the same `DispatchHub` fill path as every other node —
adding a slot it needs is §2.

**CI guard:** `validate_template()` at module load rejects any `{{slot}}` the
node's template references that isn't in `INJECTIONS`; `test_llm_calls_funnel_through_dispatch`
keeps every LLM call on the one `dispatch/llm_call/call.py` path.

---

## The shared discipline

Three invariants hold across all of the above and have their own guards:

- **One ingress.** Observers are built only via `build_run_observers`
  (`test_observers_built_via_shared_helper`); campaign artifacts are written only
  through the canonical I/O seams (`test_no_hand_rolled_io_seam_bypass`).
- **Layers don't reach backward.** `domain↛anything`, `intelligence↛optimization`,
  `infrastructure↛{application,intelligence,optimization}`
  (`test_no_unexpected_runtime_layer_violations`).
- **Registries are code-derived.** If a capability isn't in its registry, it
  doesn't exist — and the matching contract test proves the registry is whole.
