# Adding a surface — golden-path recipes

Where you type, and what catches you if you wire only half. Every recipe: **one registry is the source of truth, and an import- or init-time assert beside it proves nothing fell out** — never a standing structure test ([`tests/CLAUDE.md`](../../tests/CLAUDE.md) § Structural invariants live in production, not tests).

| You want to add… | Recipe | What actually catches you |
|---|---|---|
| A telemetry event / ledger record | [§1](#1-a-ledger-record--telemetry-event) | Import-time: `Projection.__init_subclass__` (`projections/base.py`) refuses a `_handle_*` whose annotation names no `CycleRecord` arm |
| A prompt injection (`{{slot}}`) | [§2](#2-a-prompt-injection) | Init-time: the `injection_table()` guard + `validate_template()` |
| A dashboard / view field | [§3](#3-a-dashboard--view-field) | Breaks loud — a wrong/empty dashboard |
| A resume / decision checkpoint | [§4](#4-a-resume--decision-checkpoint-kind) | Import-time: `decisions.py` + `replayers.py` asserts |
| A connector (backend) | [§5](#5-a-connector-backend) | Init-time: the `CONNECTORS` registry guard |
| An optimizer node | [§6](#6-an-optimizer-node) | `validate_template()` at prompt load |
| A CLI verb | [§7](#7-a-cli-verb) | `gate.py --only handler-tables` over `commands/verbs.py::VERBS` |
| A control-plane command kind | [§8](#8-a-control-plane-command-kind) | None to satisfy: cap, handler and the CLI verb are columns of the kind's one row |
| A served READ (a GET) | [§9](#9-a-served-read) | `gate.py --only specs` / `--only ts-types`, but **only once the route carries a `response_model`** |
| A measurement field, archive query or derived index | [§10](#10-a-measurement-field) | Arm-time where a connector declares the key (`Connector.required_observation_keys`), otherwise nothing — dropped at `sample_measurement.py::measure_sample` in silence |
| A self-healing check | [§11](#11-a-self-healing-check) | Nothing past the check: render and persistence iterate the outcomes |

---

## 1. A ledger record / telemetry event

Two writer shapes, not interchangeable — pick by whether the call site holds a ledger handle: in the **runner**, which threads per-cycle `ViewContext` state, a `RunCallbacks` method (`application/run_callbacks.py`); **deep in the async LLM / dispatch chain**, an `emit_*` helper (`infrastructure/llm/telemetry.py`) off the `_CYCLE_LEDGER` ContextVar. Steps: [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) § Conventions, plus the one it does not spell out — override `_handle_xxx` on each projection that surfaces the fact (`LiveDashboardProjection`, `AuditTrailProjection`, `ReadoutProjection`). **A record no projection handles does NOT break loud**: it reaches no artifact and nothing raises. A trace is a fold of the same record — one `_handle_*` on `infrastructure/tracing/bridge.py::TracingProjection`, nothing in the loop emits to it ([`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § Persistence — one ingress, two projections).

---

## 2. A prompt injection

A pure `_r_<name>(bundle) -> list[Item]` renderer in `dispatch/injections/` (`[]` when its source is empty), decorated `@signal("<name>", kind=…, char_cap=…, citable=…)`; reachable through the node's `NODE_LAYOUTS[node].floor` (on by default) or `.extras`. `injection_table()` fails loud on a `possible` name with no renderer, `validate_template()` on an unregistered `{{slot}}`. Contract: [`dispatch-hub.md`](dispatch-hub.md) § L1 layout — L2's structural edit surface.

---

## 3. A dashboard / view field

A view is the typed `PhaseRecord.view`, so the field is on-disk shape: declare it on the `*View` model in `domain/phase_views.py`, set it in its builder in `application/views/ingress.py`, render it in `application/views/render/` or read it where surfaced (`LiveDashboardProjection._handle_phase`); if it also appears in post-hoc `log.md`, set it in `application/runner/output.py::from_disk_log`, a separate cross-cycle source.

A field on the ROUND file is one edit: `domain/results.py::RoundOutcome` — the model IS the document. But the webapp's live surface is `dashboard.json`, so ask whose fact it is: **per-ARM** → `ArmReading` (`domain/arm_reading.py`, built by `ArmReading.of`; dashboard rows, scoreboard rows and tree nodes embed it) — and ask WHEN it exists: one the scorer knows per sample rides `SampleScoredRecord.running`, one the election stamps rides `ElectionRecord.fit`, and a field reaching neither is null on every live row until the round closes; the webapp seam is `CandidateRow` (`lib/derivations/round-candidates.ts`). **Per-ROUND** → `RoundSummary` (`domain/dashboard_rows.py`), set in `RoundSummary.of`. A fact the engine alone reads stays on `RoundOutcome`; a served copy of it is dead surface. **A display field is not done until you have SEEN it render**: a field nothing writes passes every check that could exist. Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md).

---

## 4. A resume / decision checkpoint kind

Name the kind on the deciding party's `CheckpointKind` enum — `BenchCheckpointKind` (`domain/run_records.py`), potter's `PotterCheckpointKind` (`optimizers/potter/records.py`), or a plugin's own string. A member records it with `ctx.read(Replay).decide(kind, inputs, outcome)` (`bench/node_context.py`); the bench's own go through `record_decision`. If a resume must re-derive it, register a replayer on the deciding member's `replayers` — **registering one IS the gating**: a kind with none is archived and never compared. `replayers.py::replayers` refuses two members claiming one kind (at `wiring.py::complete_registries`); `cli/commands/launch.py::divergence_hint` lists the checked kinds off that table.

---

## 5. A connector (backend)

One file under `connectors/` defining `CONNECTOR`; its declarations are [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md)'s, and `connectors/__init__.py::_validate` enumerates every half-wiring it rejects. The wiring is the easy half; settle what the backend does not declare:

- **The formula** — `{dataset}/dataset.yaml::campaign.scoring` ([`stable-api.md`](stable-api.md) § 2). Start with `per_sample` alone: a cost term needs a MEASURED anchor.
- **What it may move** — `nodes.{node}.optimizer.param_keys`. The prompt is a lever only if the node declares `prompt_info` (`pipeline_resolve.py::_validate_prompt_reach`). A cost rail (Harbor's `max_turns`) stays in `config`, out of `param_keys`.
- **The test slice** — never optimize on the rows they will report on: [`datasets.md`](../operations/datasets.md) § Adding a dataset.
- **The dataset directory** ([`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md) § A dataset file is a partial); every tunable starts at its floor ([`optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md) § Origin = conservative floor).

Screen the instrument before funding a campaign: `budget: {rounds: 0}` measures the origin only; read the spread, not the mean (an origin at 0% is floor-pinned, one at 100% has nothing to move). `seed-screen`, `noise-floor`, `verify` and `evidence` answer the sharper versions.

---

## 6. An optimizer node

One of the optimizer's own LLM nodes — `optimizers.llm_nodes()` enumerates the structured ones (each an `LlmNode` member declaring its `response_model`); the declaration format and registry are [`node-standard.md`](node-standard.md)'s. It renders an `OptimizerPromptTemplate` through the same `DispatchHub` fill path — a slot it needs is §2, and `validate_template()` rejects an unregistered one. Every optimizer LLM call goes through `bench/llm_call.py::llm_call`.

**A whole optimizer is one package and edits nothing outside it:** `members.py` exports `MEMBERS`, its manifest is a directory holding `pipeline.yaml` (a plugin ships both through the two entry-point groups, the directory as a `ManifestDir`), each kind of state it banks is a `StateSlice` (across rounds) or a `RoundOutput` (one round's) registered under its own `kind`, and its decision kinds a `CheckpointKind` its members' `replayers` gate. [`examples/optimizer-plugin/`](../../examples/optimizer-plugin/) is that package, and the [offline run](offline-run.md) runs it as the proof.

---

## 7. A CLI verb

A thin shell — parse, call into `application/`, format: one module under `presentation/cli/commands/` and one `Verb` in `commands/verbs.py::VERBS`, the handler NAMED (`module:function`) so `--help` imports no verb (`gate.py --only handler-tables`, `--only help-imports`). **A verb answering a `/commands/{kind}` is not written**: `VERBS` derives it from the kind's payload model and `command.py::cmd_command` dispatches it (§8).

What the wiring does not decide: a diagnostic verb must not perturb an existing cycle's measurements; a maintenance verb is dry-run by default and refuses while a producer could still be writing what it rewrites (`application/maintenance/archive_maintenance.py::archive_writers`), except one that only rebuilds a derived index (`reindex`). No new read verb: reads open the artifact tree, and the readings in no file are [`persistence-and-state.md`](../operations/persistence-and-state.md) § Active session pointer's. An L4 inner run is addressed on the reads, never on a run-control verb (`cycles --inside`, a subject's `;in=`): every other cycle-scoped kind declares an `inner_refusal` (`domain/command_kinds.py`).

---

## 8. A control-plane command kind

A kind is ONE row of `domain/command_kinds.py::CommandKind`, and every table a reader wants is derived from it; it sits in `domain/` because the lazy CLI `--help` and a ledger fold cannot import the dispatcher.

| The row's column | What it is | What refuses a wrong one |
|---|---|---|
| the kind | the wire name; its payload model declares it (`class …(CyclePayload, kind=CommandKind.…)`), filing the model in `PAYLOAD_MODEL_FOR_KIND` | `command_kinds.py` fails at import on a kind with no model, or the declaration on a second one |
| `handler` | `module:handler` in the `application/commands/` module named for what the kind does; imported when dispatched, never at import | `gate.py --only handler-tables` |
| `capability` | the rung the dispatcher's `require_capability` asks | the row cannot be written without one |
| `cli_verb` | the terminal verb that reaches it, derived from the payload model unless `verbs.py` writes it | the row cannot be written without one |

The payload's address base is its target ledger (`CyclePayload`, `CampaignPayload`, a check-in's, or a bare `CommandPayload`), and `routers/commands.py` routes on it. `cli_verb` is the `<entry-point-parity>` guard: `None` is a declaration, not an escape hatch — exactly one exists (`set-sample-lookahead`, [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § Sample look-ahead). Nothing else is written: `scripts/build_specs.py` and `scripts/build_ts_types.py` generate the spec entry and the `CommandPayloads` entry `postCommand` types its body on.

---

## 9. A served read

A new `GET` — not a Control-remote command and not a sixth I/O kind, since that kind is defined by MUTATION (`architecture.md` § Control-remote). **Nothing here is caught by an import-time assert**:

| Step | Do | Why it is not optional |
|---|---|---|
| 1 | Name the scope the read is a fact ABOUT, and address it by that entity's id | a read keyed on the wrong entity answers *plausibly and wrongly* |
| 2 | Declare the response model before the handler | the spec and TS types are built from it (`--only specs`, `--only ts-types`); a route without one is undeclared with every gate green |
| 3 | Resolve in `application/`, never in the router | [`decisions.md`](../decisions.md) § 0006; [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § Out-of-bounds |
| 4 | Serve provenance for anything the client could otherwise infer | a value the browser has to *diff* to explain becomes a client twin that drifts |
| 5 | Add the row to [`webapp/lib/CLAUDE.md`](../../webapp/lib/CLAUDE.md) § Display-data sources | a data class with no row has no owner |
| 6 | Run it and look at the panel | §3's rule |

New webapp data panels ride `dashboard.json` + the SSE tail. A GET beside that pair is allowed only as **one-shot topology** — its answer cannot change unless something the poll already announces changes. Anything on its own schedule belongs on the poll.

---

## 10. A measurement field

The archive's shape is [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md)'s; `MeasurementArchive` is the only gateway.

| Change | Files |
|---|---|
| New field on every measurement | a measured FACT joins `domain/scoring.py::MeasuredCell` (or `PipelineData`); a STAMP beside the facts joins `domain/sample.py::FiledAnswer`. A `pipeline_data` key also needs `_INFRA_KEYS` or a dataset `observation_mapping`, plus the compaction asserts beside those types |
| New retrieval query | a method on `MeasurementArchive` parallel to `measurements_for_config` |
| New derived index | a class folding each POPULATION into one row keyed by `config_key`, applied through ONE `replay_row()` live and on replay; register on `SampleIndex.refresh()`, persist via `archive_queries.write_sample_fold`'s shape |

`node_configs` is canonical identity — deterministic from pipeline params.

---

## 11. A self-healing check

Pick the wound by detector + score effect ([`self-healing-internals.md`](self-healing-internals.md) § The wounds, mapped to the two axes); the owner falls out of the record type.

- Gen-time check on L1's output → **Wound 1**: a function in `validators/l1_strict.py` called from `parse_population`, or in `bench/children.py` when every optimizer's child owes it. Give it a stable `reason`.
- Runtime measurement pointing at a config region → **Wound 2**: a check emitting `RuntimeFailure` from `application/scoring/arm_states.py::read_breakage`, stamped `NurseOwner.L1` when L1 can retune it, `NurseOwner.OPERATOR` when only the operator can.
- Strategic stall → **Wound 3** is the patience timer, not a registry.
- Post-parse check on L2/L3 output → **Wound 4**: extend `validators/l3_output.py::l3_plan_breaches` or `dispatch/layout.py::validate_l1_layout`. Give it a stable `validator_id`.

Render and persistence iterate the outcomes, not a hard-coded list. Add a `NurseOwner` member only when a producer actually stamps it.
