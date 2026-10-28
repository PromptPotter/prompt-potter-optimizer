# Adding a surface — golden-path recipes

The pre-flight gate (root `CLAUDE.md`) and the per-layer `CLAUDE.md` files say *what* the rules
are; this page says *where you type* and *what catches you* if you wire only half. The pattern
every recipe shares: **one registry is the source of truth, and an import- or init-time assert
beside it proves nothing fell out of it** — never a standing `test_structure` scan
([`tests/CLAUDE.md`](../../tests/CLAUDE.md) § Structural invariants live in production, not tests).

| You want to add… | Recipe | What actually catches you |
|---|---|---|
| A telemetry event / ledger record | [§1](#1-a-ledger-record--telemetry-event) | Import-time: `Projection.__init_subclass__` (`projections/base.py`) refuses a `_handle_*` whose annotation names no `CycleRecord` arm |
| A prompt injection (`{{slot}}`) | [§2](#2-a-prompt-injection) | Init-time: the `injection_table()` guard + `validate_template()` |
| A dashboard / view field | [§3](#3-a-dashboard--view-field) | Breaks loud — a wrong/empty dashboard |
| A resume / decision checkpoint | [§4](#4-a-resume--decision-checkpoint-kind) | Import-time: `decisions.py` + `replayers.py` asserts |
| A connector (backend) | [§5](#5-a-connector-backend) | Init-time: the `CONNECTORS` registry guard |
| An optimizer node | [§6](#6-an-optimizer-node) | `validate_template()` at prompt load |
| A CLI verb | [§7](#7-a-cli-verb) | `gate.py --only handler-tables` over `commands/verbs.py::VERBS` |
| A control-plane command kind | [§8](#8-a-control-plane-command-kind) | None to satisfy: cap, handler **and the CLI verb** are columns of the kind's one row, its payload model the class named for it |
| A served READ (a GET) | [§9](#9-a-served-read) | `gate.py --only specs` / `--only ts-types`, but **only once the route carries a `response_model`** — a read without one is invisible to both |
| A measurement field, archive query or derived index | [§10](#10-a-measurement-field) | **Arm-time where a connector declares the key** (`Connector.required_observation_keys`), otherwise nothing — it is dropped at `sample_measurement.py::measure_sample` in silence |
| A self-healing check | [§11](#11-a-self-healing-check) | Nothing to wire past the check: render and persistence iterate the outcomes |

---

## 1. A ledger record / telemetry event

**First, the one decision**: two writer shapes, *not* interchangeable — pick by whether the call
site holds an explicit ledger handle.

| If the fact originates… | Use | Why |
|---|---|---|
| in the **runner**, which owns the observers and threads per-cycle `ViewContext` state across events | **`RunCallbacks`** method (`application/run_callbacks.py`) | The phase path is **stateful** — the view builders read a `ViewContext` carried round-over-round. Owned state, explicit injection. |
| **deep in the async LLM / dispatch chain**, with no ledger handle in scope | **`emit_*`** helper (`infrastructure/llm/telemetry.py`) | Stateless: kwargs in, append out, off the `_CYCLE_LEDGER` ContextVar — which exists *because* these sites can't be handed a handle. |

Do **not** fold one into the other: `RunCallbacks` through `emit_*` turns an explicit
`ViewContext` into an ambient global, and the deep sites have nothing to call `RunCallbacks` on.

The step-by-step is [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
§ Conventions. The one step it does not spell out: **override `_handle_xxx` on each projection
that surfaces the fact** (`LiveDashboardProjection`, `AuditTrailProjection`, `ReadoutProjection`).

**Guard (import-time):** `Projection.__init_subclass__` routes each `_handle_*` by its `record`
annotation and refuses one that names no `CycleRecord` arm. **A record no projection handles does
NOT break loud** — it reaches no artifact and nothing raises.

**A trace is a fold of the same record, not a second home for the fact**: one `_handle_*` on
`infrastructure/tracing/bridge.py::TracingProjection` and one `tracing/events.py::TraceSink` hook;
nothing in the loop emits to it. Contract:
[`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § Persistence — one
ingress, two projections.

---

## 2. A prompt injection

A `{{slot}}` the optimizer LLM sees. The registry is `injection_table()`
(`application/optimizers/potter/dispatch/injections/registry.py`).

1. Write a pure `_r_<name>(bundle) -> list[Item]` renderer in `dispatch/injections/` (returns
   `[]` when its source field is empty — a panel that produces nothing is silent).
2. Decorate it with `@signal("<name>", kind=…, char_cap=…, citable=…)` — registration happens at
   the definition site, no separate registry edit.
3. Make it reachable: the node's `NODE_LAYOUTS[node].floor` puts it on by default, `.extras`
   offers it off the floor; `.possible` is the two together.

**Guard (at registry completion):** `injection_table()` fails loud if a `possible` name has no
registered renderer, and `validate_template()` (at `load_optimizer_prompt`) raises on any
`{{slot}}` not in the registry.

Contract: [`dispatch-hub.md`](dispatch-hub.md) § L1 layout.

---

## 3. A dashboard / view field

A field on a phase view (the live CLI render + `dashboard.json`). A view is the typed
`PhaseRecord.view`, so the field is **on-disk shape**: a ledger fold and a live subscriber hold
the same value, with **no reconstructor to keep in sync**.

1. Add the field to the `*View` model in `domain/phase_views.py`.
2. Set it where the view is built — its builder in `application/views/ingress.py`.
3. Render it in `application/views/render/` and/or read it where the fact is surfaced
   (`LiveDashboardProjection._handle_phase` matches on the typed view).
4. If it also appears in post-hoc `log.md`, set it in `application/runner/output.py::from_disk_log`
   — a genuinely separate, **cross-cycle** source, not a roundtrip shim.

**A field on the ROUND file is one edit**: declare it on `domain/results.py::RoundOutcome` — the
model IS the document, so it reaches every reader of a standing round with no payload builder.

**But the webapp's live surface is `dashboard.json`, not the round.** Ask first *whose* fact it is:

- **Per-ARM** (it already lives on an `ArmState`) → add it to **`ArmReading`**
  (`domain/arm_reading.py`) and stop: a dashboard row live or closed, a scoreboard row and a tree
  node all embed it, built by `ArmReading.of`. The live half is `build_candidate_rows`', so
  **ask WHEN your fact exists**: one the scorer knows per sample rides
  `SampleScoredRecord.running`, one the election stamps rides `ElectionRecord.fit`, and a field
  that reaches neither is null on every live row until the round closes. The webapp seam is
  `CandidateRow` (`lib/derivations/round-candidates.ts` maps both halves through ONE function).
- **Per-ROUND** → declare it on `RoundSummary` (`domain/dashboard_rows.py`) and set it in
  `RoundSummary.of`, beside it, which reads the round's close.

Reach for the round level only when the fact genuinely isn't a candidate's and a panel renders
it: one the engine alone reads stays on `RoundOutcome`, and a served copy of it is dead surface.

**A display field is not done until you have SEEN it render.** Every step above wires a
declaration and a reader; none writes the value, and a field nothing writes passes every check
that could exist — the panel renders blank and each reader honestly reports "this backend does not
say". No assert can catch it, so the enforcement is one glance: run it, look, confirm a number
appeared.

Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md).

---

## 4. A resume / decision checkpoint kind

A decision a resume re-derives, or one it only archives.

1. Name the kind: a value of the deciding party's `CheckpointKind` enum — `BenchCheckpointKind`
   (`domain/run_records.py`), or the optimizer's own in its package (potter's
   `PotterCheckpointKind`, `optimizers/potter/records.py`) — or, for a plugin, its own string.
2. A member records it with `ctx.read(Replay).decide(kind, inputs, outcome)` (`bench/node_context.py`), which
   stamps its node and round; the bench's own go through `record_decision`.
3. If a resume must re-derive it, register a replayer for the kind on the deciding member's
   `replayers`. **Registering one IS the gating**: a kind with none is archived and never compared.

**Guard (no standing test), run where the registries complete
(`wiring.py::complete_registries`):** `replayers.py::replayers` refuses two members claiming one
kind. `cli/commands/launch.py::divergence_hint` lists the checked kinds off that table.

---

## 5. A connector (backend)

A new backend kind — one file under `connectors/` defining `CONNECTOR`, owned step by step by
[`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md).

**The wiring is the easy half**; what decides whether the campaign is worth running is what is
being improved and how it is graded, and nothing about the backend tells you that. Learn the shape
cheapest-first (what they have, then ONE live probe they approve), then settle each decision where
it is owned:

- **One measured row** — one request or a whole run: `measured_unit` + `max_cells_in_flight`,
  [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The measured unit.
- **What grades it** — the answer shape, declared in `extract_experiment` alone, where a
  verifier-graded backend yields `ground_truth: None` (same page, § The answer shape).
- **Transport and credential** — declare `execution` on the connector (§ Execution mode — never a
  branch in the core loop) and the `auth_token` hook (§ The credential rides the connector).
- **The formula** — `{dataset}/dataset.yaml::campaign.scoring` ([`stable-api.md`](stable-api.md)
  § 2). **Start with `per_sample` alone**: a cost term needs a MEASURED anchor, and the first
  campaign is what measures it.
- **What it may move** — `nodes.{node}.optimizer.param_keys`. The prompt is a lever only if the
  node declares `prompt_info`, or run init refuses (`pipeline_resolve.py::_validate_prompt_reach`).
  A cost rail (Harbor's `max_turns`) stays pinned in `config`, out of `param_keys`.
- **The test slice** — never optimize on the rows they will report on:
  [`datasets.md`](../operations/datasets.md) § Adding a dataset, steps 1 and 3.
- **The wiring** — the connector file, then the dataset directory
  ([`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md)
  § A dataset file is a partial); every tunable starts at its **floor**
  ([`optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md)
  § Origin = conservative floor).

### Screen the instrument before funding a campaign

Set `budget: {rounds: 0}` and measure the origin only. **Read the spread, not the mean**: an
origin at 0% is floor-pinned and one at 100% has nothing for a prompt to move, and neither is
visible from a single cell. `seed-screen`, `noise-floor`, `verify` and `evidence` answer the
sharper versions — [`persistence-and-state.md`](../operations/persistence-and-state.md).

**Guard (at registry completion):** `connectors/__init__.py::_validate` raises on a half-wired
connector, and its own raise enumerates every half-wiring it rejects.

---

## 6. An optimizer node

One of the optimizer's own LLM nodes — `optimizers.llm_nodes()` enumerates the structured ones
(each an `LlmNode` member declaring its own `response_model`). The declaration format and registry
live in [`node-standard.md`](node-standard.md). A node renders an `OptimizerPromptTemplate`
through the same `DispatchHub` fill path as every other node — adding a slot it needs is §2.

**Guard (at template load):** `validate_template()` rejects any `{{slot}}` not in
`injection_table()`. Keep every optimizer LLM call on `bench/llm_call.py::llm_call` — an unwrapped
call is an automatic block at review, not a test.

**A whole optimizer is one package and edits nothing outside it:** `members.py` exports `MEMBERS`,
its manifest is a directory holding `pipeline.yaml` (a plugin ships both through the two
entry-point groups, the directory as a `ManifestDir`), each kind of state it banks is a `StateSlice`
(across rounds) or a `RoundOutput` (one round's) registered under its own `kind`, and its decision
kinds a `CheckpointKind` its members' `replayers` gate. A mix of other optimizers' members is a
manifest alone. [`examples/optimizer-plugin/`](../../examples/optimizer-plugin/)
is that package, and the [offline run](offline-run.md) runs it as the proof.

---

## 7. A CLI verb

A new `python -m promptpotter <verb>` — a **thin shell**: parse, call into `application/`, format.
One module under `presentation/cli/commands/` and one `Verb` in `commands/verbs.py::VERBS`, the
handler NAMED (`module:function`) so `--help` imports no verb; parser and dispatch derive from that
roster. **A verb answering a `/commands/{kind}` is not written**: `VERBS` derives it from the
kind's payload model and `command.py::cmd_command` dispatches it; a shell the derivation cannot
express is its own module (`bench.py`).

**Two decisions the wiring does not make for you.** A diagnostic verb must not perturb an
existing cycle's measurements. A maintenance verb (one that rewrites stored artifacts on purpose)
is dry-run by default and refuses while a producer could still be writing what it rewrites
(`application/maintenance/archive_maintenance.py::archive_writers`) — except one that only
rebuilds a derived index and deletes nothing (`reindex`), which owes neither. And do **not** add
a read verb: reads happen by opening the artifact tree; the readings in no file are owned by
[`persistence-and-state.md`](../operations/persistence-and-state.md) § Active session pointer,
each printing the reading its REST peer serves. **An L4 inner run is addressed on the reads,
never on a run-control verb** (`cycles --inside`, an `evidence` / `verify` subject's `;in=`):
every other cycle-scoped kind declares an `inner_refusal` (`domain/command_kinds.py`).

**Guard:** `gate.py --only handler-tables` resolves every named handler, and `--only help-imports`
keeps the roster import-light. If the verb answers a `/commands/{kind}`, §8 owns the other half.

---

## 8. A control-plane command kind

A new `POST /commands/{kind}`. **A kind is ONE row of `domain/command_kinds.py::CommandKind`**,
and every table a reader wants is derived from it. It sits in `domain/` because its readers — the
lazy CLI `--help`, a ledger fold — cannot all afford to import the dispatcher.

| The row's column | What it is | What refuses a wrong one |
|---|---|---|
| the kind | the wire name; its payload model declares it (`class …(CyclePayload, kind=CommandKind.…)`), which files the model in `PAYLOAD_MODEL_FOR_KIND`, beside the row; a type it holds moves down into `domain/` with it | `command_kinds.py` fails at import on a kind with no model, the declaration on a second one |
| `handler` | `module:handler`, in the `application/commands/` module named for what the kind does; the dispatcher imports it when the kind is dispatched, never at its own import | `gate.py --only handler-tables` |
| `capability` | the rung the dispatcher's `require_capability` asks | the row cannot be written without one |
| **`cli_verb`** | the terminal verb that reaches it, derived from the payload model unless `verbs.py` writes it | the row cannot be written without one |

The payload's address base is its target ledger (`CyclePayload`, `CampaignPayload`, a check-in's,
or a bare `CommandPayload` for the workspace), and `routers/commands.py` routes on it.

`cli_verb` is the `<entry-point-parity>` guard. Its value is a CLI verb or `None` — and `None` is
a **declaration**, not an escape hatch. Exactly one exists (`set-sample-lookahead`, whose absence
from the terminal is the contract; root `CLAUDE.md` § Conventions). A second one is a design
decision with a reason, not a wiring shortcut.

Nothing else is written: `scripts/build_specs.py` generates its spec entry and
`scripts/build_ts_types.py` its `CommandPayloads` entry, which `postCommand` types its body on.

---

## 9. A served read

A new `GET`. **Not a Control-remote command and not a sixth I/O kind** — that kind is defined by
MUTATION, so a read adds no ingress and no writer (`architecture.md` § Control-remote).

**Nothing here is caught by an import-time assert**; skip steps 2–4 and every gate stays green
over a surface nobody declared.

| Step | Do | Why it is not optional |
|---|---|---|
| 1 | **Name the scope the read is a fact ABOUT**, and address it by that entity's id | A read keyed on the wrong entity is not a bug you find later — it answers *plausibly and wrongly* |
| 2 | **Declare the response model before the handler** | Root `CLAUDE.md` § Pre-flight gate. The generated spec and TS types are built from it (`--only specs`, `--only ts-types`), and a route without one is undeclared with every gate green — the only step on the page nothing can catch |
| 3 | **Resolve in `application/`, never in the router** | [`decisions.md`](../decisions.md) § 0006 (`cli/` + `embedded_run.py` must reach it) and `presentation/CLAUDE.md` § Out-of-bounds |
| 4 | **Serve provenance for anything the client could otherwise infer** | A closed set belongs on the server (`webapp/lib/CLAUDE.md`). Every value the browser has to *diff* to explain becomes a client twin that drifts |
| 5 | **Add the row to `webapp/lib/CLAUDE.md` § Display-data sources** | A data class with no row is a data class with no owner, and the gap fills itself with stores |
| 6 | **Run it and look at the panel** | §3's rule, and it binds hardest here: a served field that is declared, typed and never *written* renders as nothing, and no check that could exist would see it |

**Reads and the polling rule.** New webapp data panels ride `dashboard.json` + the SSE tail. A
GET is allowed beside that pair only when it is **one-shot topology** — its answer cannot change
unless something the poll already announces changes, so the poll is its *invalidation signal*
rather than its transport. Anything that changes on its own schedule belongs on the poll.

---

## 10. A measurement field

The archive's shape is [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md)'s;
`MeasurementArchive` is the only gateway.

| Change | Files |
|---|---|
| New field on every measurement | A measured FACT joins `domain/scoring.py::MeasuredCell` (or `PipelineData`); a STAMP beside the facts joins `domain/sample.py::FiledAnswer`. A `pipeline_data` key also needs `_INFRA_KEYS` or a dataset `observation_mapping`, plus the compaction asserts beside those types |
| New retrieval query | A method on `MeasurementArchive` parallel to `measurements_for_config`; pair it with an index class if filtering must stay efficient |
| New derived index | A class folding each POPULATION into one row keyed by `config_key` and stamped with its signature, applied through ONE `replay_row()` both live and on replay; register on `AxisIndex.refresh()`, persist via `archive_queries.write_sample_fold`'s shape — never a second mechanism |

**`node_configs` is canonical identity** — it must stay deterministic from pipeline params.

---

## 11. A self-healing check

Pick the wound by detector + score effect ([`self-healing-internals.md`](self-healing-internals.md)
§ The wounds, mapped to the two axes); the owner falls out of the record type, so you never wire a
nurse by hand.

- Gen-time check on L1's output → **Wound 1**: a function in `validators/l1_strict.py` called from
  `parse_population`, or in `bench/children.py` when every optimizer's child owes it. Give it a
  stable `reason`.
- Runtime measurement pointing at a config region → **Wound 2**: a check emitting `RuntimeFailure`
  from `application/scoring/arm_states.py::read_breakage`, stamped `NurseOwner.L1` when L1 can
  retune it, `NurseOwner.OPERATOR` when only the operator can.
- Strategic stall → **Wound 3** is the patience timer, not a registry.
- Post-parse check on L2/L3 output → **Wound 4**: extend the one that owns the output —
  `validators/l3_output.py::l3_plan_breaches` or `dispatch/layout.py::validate_l1_layout`. Give it
  a stable `validator_id`.

Render and persistence are already wired — they iterate the outcomes, not a hard-coded list. Add a
`NurseOwner` member only when a producer actually stamps it.
