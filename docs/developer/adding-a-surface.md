# Adding a surface — golden-path recipes

Expansion in PromptPotter is **fill-in-the-blank, CI-guarded**. Each surface
below has a fixed set of edits and a contract test that fails the build when you
wire only half of it. The pre-flight gate (root `CLAUDE.md`) and the six
per-layer `CLAUDE.md` files say *what* the rules are; this page says *where you
type* and *which test catches you* if you miss a half.

The pattern every recipe shares: **one registry is the source of truth, and an
import-time assert proves nothing fell out of it.** A capability can't silently
disappear because the registry is code-derived and the assert walks it.

**Where the guard lives.** Per [`tests/CLAUDE.md`](../../tests/CLAUDE.md) a test
earns its place only if it catches *silent* harm; the structural / wire / shape
suites were deliberately cut because those failures break loud. So most guards
below are **import-time asserts beside the registry they validate**, not standing
tests. Add new ones the same way — never as a `test_structure` scan.

| You want to add… | Recipe | What actually catches you |
|---|---|---|
| A telemetry event / ledger record | [§1](#1-a-ledger-record--telemetry-event) | Breaks loud in use — a union member with no `on_record` arm never reaches `dashboard.json` |
| A prompt injection (`{{slot}}`) | [§2](#2-a-prompt-injection) | Import-time: the `registry.py` guard + `validate_template()` |
| A dashboard / view field | [§3](#3-a-dashboard--view-field) | Breaks loud — a wrong/empty dashboard |
| A resume / decision checkpoint | [§4](#4-a-resumedecision-checkpoint-kind) | Import-time: `decisions.py` + `replayers.py` asserts |
| A connector (backend) | [§5](#5-a-connector-backend) | Import-time: the `CONNECTORS` registry guard |
| An optimizer node | [§6](#6-an-optimizer-node) | Import-time: `validate_template()` at prompt load |
| A CLI verb | [§7](#7-a-cli-verb) | Breaks loud — an unknown verb exits non-zero |
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
(`application/optimization/dispatch/injections/registry.py`); every renderer
is a pure `(InjectionBundle) -> str`.

**Recipe:**

1. Write a `_r_<name>(bundle) -> str` renderer in `dispatch/injections/`
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
3. Render it in `presentation/views/render.py` (`to_text`) /
   `application/views/render/` (`to_markdown`) and/or
   read it where the fact is surfaced — `LiveDashboardView._apply_phase` reads
   the typed view by attribute (`getattr`, presentation-agnostic).
4. If the field also appears in post-hoc `log.md`, set it in `from_disk_log`
   (`application/output.py`) — that builder reads on-disk `index.json` for
   **cross-cycle** rendering and is a genuinely separate source, not a roundtrip shim.

**A field on the ROUND document is not this recipe — it is one edit.** Declare it on
`RoundResult` (`domain/results.py`) and it reaches `rounds/round_NNNN.json` and every
reader of that file, because the model IS the document (`save_round_file` persists
`model_dump()`; `load_round_file` validates it back). There is no payload builder to
mirror it into — the one that existed hand-wrote 24 of the model's fields and silently
dropped the other twelve.

**But the webapp does not read round files** — it reads `dashboard.json`. To land a round
field there too, mirror it onto `RoundSummary` and add the one line to
`projections/live_dashboard/round_summary.py`. Two models, one projection line; the round
document alone reaches disk, not the browser.

**Guard:** the two-factories-onto-one-View correctness invariant — the live
builder and the disk builder must produce an equal `RoundCompleteView`. No
standing test (a broken round-trip surfaces as a wrong/empty dashboard; the
structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md).

---

## 4. A resume / decision checkpoint kind

A replayable or archival decision (`ResumeCheckpointKind` + its gating mode).

**Recipe:**

1. Add the kind to `ResumeCheckpointKind` (`domain/run_records.py`) **and** a gating
   entry to `RESUME_CHECKPOINT_GATING` (`application/optimization/resume_and_fork/
   decisions.py` — the gating SoT; the enum and the table live in different files).
2. If replayable, add it to the replayer; if archival, leave it out.
3. Emit it through `record_decision` with the typed kind, never a bare string.

**Guards (all import-time, no standing test):** `decisions.py` raises on a
`ResumeCheckpointKind` member missing from `RESUME_CHECKPOINT_GATING`;
`replayers.py` raises when a `REPLAYED` kind has no replayer or an `ARCHIVAL` kind
has one; `cli/commands/_shared.py` asserts the divergence hint lists every kind.

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
5. If the backend needs a credential, declare an `auth_token: () -> str | None` hook.
   **A credential is a per-backend fact and belongs on the connector**, never read at
   the construction site — `build_backend_client` (`infrastructure/backend.py`) is the
   one place a `BackendClient` is built, and it takes the token off the connector it
   was handed. Four sites once passed `settings.TERMNORM_TOKEN` to whatever connector
   had been resolved, so registering a second `remote_http` backend would have POSTed
   TermNorm's bearer token to that third-party host.

**Guard (import-time, no standing test):** the registry guard at the bottom of
`connectors/__init__.py` raises at import if any row is half-wired — registry key ≠
`name`, a non-callable hook, an `execution` outside `ConnectorExecution`, an
`in_process` connector without `in_process_run` (or a `remote_http` one with it), an
`in_process` connector carrying an `auth_token` (no wire to send it over), or a
`DEFAULT_CONNECTOR` naming an unregistered backend. An unknown connector raises
`KeyError` at `get()`.

**The `in_process` arm is SHIPPED**, and two connectors ride it: `llm_only` (one
direct LLM call, no backend server) and `promptpotter` (an inner cycle — L4, via
`runner/inner/cycle.py`). It does not raise `NotImplementedError`.

Contract: [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md).

---

## 6. An optimizer node

One of the five LLM nodes (`l1_generate`, `l1_critique`, `l2_context`,
`l3_plan`, `checkin`). The JSON declaration format and registry live in
[`developer/node-standard.md`](node-standard.md); response models in
`dispatch/schemas.py::OPTIMIZER_RESPONSE_MODELS`. A node renders a
`PromptTemplate` through the same `DispatchHub` fill path as every other node —
adding a slot it needs is §2.

**Guard (import-time):** `validate_template()` at `load_optimizer_prompt` rejects any
`{{slot}}` the node's template references that isn't in `INJECTIONS`. Keep every
optimizer LLM call on the one `dispatch/llm_call/call.py::llm_call` path — an
unwrapped LLM call is an automatic block at review (pre-flight gate), not a test.

---

## 7. A CLI verb

A new `python -m promptpotter <verb>`. The CLI is a **thin shell**: parse, call into
`application/`, format. Business logic that lands here is drift.

**Recipe:**

1. Write `presentation/cli/commands/<verb>.py` exporting one `cmd_<verb>(args)`
   entry function. Prefer a module; reach for a subpackage only when the verb has
   genuinely separable parts (`lifecycle.py` holds three verbs in one module — a
   directory per verb bought a reader a hop to learn there was nothing to choose).
2. Add its argparse subparser in `presentation/cli/parsers.py`.
3. Add the `"<verb>": cmd_<verb>` row to `COMMANDS` in
   `presentation/cli/campaign_runner.py`, importing the function at the top.
4. Decide the verb's class and honor it: **write** (`new` / `resume` — these mint or
   extend a cycle), **lifecycle** (`archive` / `delete` / `unarchive` / `reset`), or
   **diagnostic** (`ab` / `verify` / `noise-floor` / `matrix` —
   these must not perturb an existing cycle's measurements).
5. Do **not** add a read verb. Reads happen by opening the artifact tree — the file
   tree *is* the dashboard. Nor an `ingest` verb: raw-file ingest is `new <file.csv>`.

**Guard:** none needed — an unknown verb exits non-zero and a missing `COMMANDS` row
breaks loud on first invocation. Both are the "breaks loud → no test" class.

Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md);
verb reference: [`operations/`](../operations/).

---

## The shared discipline

Three invariants hold across all of the above. None is a standing test — each
breaks loud, which is exactly why [`tests/CLAUDE.md`](../../tests/CLAUDE.md) says
not to test it:

- **One ingress.** Observers are built only via `build_run_observers`; campaign
  artifacts are written only through the canonical I/O seams (`CycleEventLog.append`
  or a declared projection).
- **Layers don't reach backward.** `domain↛anything`, `intelligence↛optimization`,
  `infrastructure↛{application,intelligence,optimization}`. Violations fail at import.
- **Registries are code-derived.** If a capability isn't in its registry, it doesn't
  exist — and the registry's own import-time assert proves it is whole.
