# Adding a surface — golden-path recipes

Expansion in PromptPotter is **fill-in-the-blank, CI-guarded**. Each surface
below has a fixed set of edits and a contract test that fails the build when you
wire only half of it. The pre-flight gate (root `CLAUDE.md`) and the
per-layer `CLAUDE.md` files say *what* the rules are; this page says *where you
type* and *which test catches you* if you miss a half.

The pattern every recipe shares: **one registry is the source of truth, and an
import- or init-time assert proves nothing fell out of it.** A capability can't silently
disappear because the registry is code-derived and the assert walks it.

**Where the guard lives.** Per [`tests/CLAUDE.md`](../../tests/CLAUDE.md) a test
earns its place only if it catches *silent* harm; the structural / wire / shape
suites were deliberately cut because those failures break loud. So most guards
below are **import- or init-time asserts beside the registry they validate**, not standing
tests. Add new ones the same way — never as a `test_structure` scan.

| You want to add… | Recipe | What actually catches you |
|---|---|---|
| A telemetry event / ledger record | [§1](#1-a-ledger-record--telemetry-event) | Import-time: `_ROUTES` (`projections/base.py`) must answer for every `CycleRecord` arm — the trace is a subscriber under the same table |
| A prompt injection (`{{slot}}`) | [§2](#2-a-prompt-injection) | Init-time: the `injection_table()` guard + `validate_template()` |
| A dashboard / view field | [§3](#3-a-dashboard--view-field) | Breaks loud — a wrong/empty dashboard |
| A resume / decision checkpoint | [§4](#4-a-resume--decision-checkpoint-kind) | Import-time: `decisions.py` + `replayers.py` asserts |
| A connector (backend) | [§5](#5-a-connector-backend) | Init-time: the `registered()` registry guard |
| An optimizer node | [§6](#6-an-optimizer-node) | `validate_template()` at prompt load |
| A CLI verb | [§7](#7-a-cli-verb) | Import-time: the `COMMANDS` ↔ `parser_verbs` assert |
| A control-plane command kind | [§8](#8-a-control-plane-command-kind) | Import-time: four asserts over `ALL_DISPATCHED_KINDS` — cap, handler, payload model, **and the CLI verb** |
| A served READ (a GET) | [§9](#9-a-served-read) | `gate.py --only openapi` / `--only ts-types`, but **only once the route carries a `response_model`** — a read without one is invisible to both |
| A measurement field | [developer README §4](README.md#4-cross-run-memory) | **Arm-time where a connector declares the key** (`Connector.required_observation_keys`), otherwise nothing — it is dropped at `sample_measurement.py::measure_sample` in silence. Declare it on `domain/scoring.py::QueryMeasurement` / `PipelineData`; a `pipeline_data` key also needs `_INFRA_KEYS` or a dataset `observation_mapping`, plus the compaction asserts beside those types |

---

## 1. A ledger record / telemetry event

**First, the one decision**: there are
two writer shapes, and they are *not* interchangeable — pick by whether the call
site holds an explicit ledger handle.

| If the fact originates… | Use | Why |
|---|---|---|
| in the **runner**, which owns the observers and threads per-cycle `ViewContext` state across events (phase enter/exit, round complete, the per-candidate / per-sample arm-walk records) | **`RunCallbacks`** method (`application/run_observers.py`) | The runner has the ledger as an explicit dependency and the phase path is **stateful** — the view builders read a `ViewContext` carried round-over-round. Owned state, explicit injection. |
| **deep in the async LLM / dispatch chain**, with no ledger handle in scope (token usage, an LLM-call marker, a command ack, a crash, a self-healed round warning) | **`emit_*`** helper (`infrastructure/llm/telemetry.py`) | Stateless: kwargs in, append out. Reads the ledger from the `_CYCLE_LEDGER` ContextVar (set by `build_run_observers`, reset by `drain_all`) — the ContextVar exists *because* these sites can't be handed a handle. |

Do **not** fold one into the other: routing the runner's `RunCallbacks` through
`emit_*` would force its explicit `ViewContext` into an ambient ContextVar
(implicit mutable global), and routing `emit_*` through `RunCallbacks` is
impossible (the deep sites have nothing to call it on).

The step-by-step is [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
§ Conventions' canonical template. The one step it does not spell out: **override `_handle_xxx`
on each projection that surfaces the fact** (`LiveDashboardProjection` for `dashboard.json`,
`AuditTrailProjection` for `round_NNNN.json`, `ReadoutProjection` for `readout.log`). Unhandled = silently dropped,
which is exactly what the guard prevents.

**Guard (an import-time raise, not a standing test — see
[`tests/CLAUDE.md`](../../tests/CLAUDE.md) § Structural invariants):** `_ROUTES` must answer
for every `CycleRecord` arm, and which arms are deliberately unfolded is stated there with
each one's reason rather than here — a second list is what let the first one go wrong.

**A missing arm does NOT break loud** — a record no fold answers for reaches no artifact and
nothing raises, which is why the claim is checked at import rather than asserted in prose.

**A trace is a fold of the same record, not a second home for the fact.**
`infrastructure/tracing/bridge.py::TracingProjection` is one more ledger subscriber: it carries
the trace TOPOLOGY — campaign / round / phase / call spans and their scores, the shape a remote
sink renders — by overriding `_handle_xxx` like any projection and calling the matching
`tracing/events.py::TraceSink` hook on each sink. A sink overrides only the hooks it renders, so
a record reaches a trace by one `_handle_*` there and one hook; nothing in the loop emits to it.

Contract: [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) §
"Per-call telemetry", [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md)
§ "Persistence — one ingress".

---

## 2. A prompt injection

A `{{slot}}` the optimizer LLM sees. The registry is `injection_table()`
(`application/optimizers/potter/dispatch/injections/registry.py`); every renderer
is a pure `(InjectionBundle) -> list[Item]`.

**Recipe:**

1. Write a `_r_<name>(bundle) -> list[Item]` renderer in `dispatch/injections/`
   (returns `[]` when its source field is empty — a panel that produces nothing is silent).
2. Decorate it with `@signal("<name>", kind=…, char_cap=…, citable=…)` — registration
   happens at the definition site; key and body are co-located, no separate
   registry edit.
3. To make it reachable, add it to the node's `NODE_LAYOUTS[node].possible`
   (and `.floor` to put it on by default — for `l1_generate`, `.possible` and `.mandatory`
   alias `L1_POSSIBLE` / `L1_MANDATORY`).

**Guard (at registry completion, no standing test):** `injection_table()` fails loud if a
`possible` name has no registered renderer, and
`validate_template()` (at `load_optimizer_prompt`) raises at template load on any
`{{slot}}` not in the registry — typos fail loud.

Contract: [`dispatch-hub.md`](dispatch-hub.md) § L1 layout.

---

## 3. A dashboard / view field

A field on a phase view (the live CLI render + `dashboard.json`). A view is the
typed `PhaseRecord.view`, so the field is **on-disk shape**: a reader folding the
ledger and a subscriber folding live hold the same value, and there is **no
reconstructor to keep in sync**.

**Recipe:**

1. Add the field to the `*View` model in `domain/phase_views.py`.
2. Set it where the view is built — its builder in `application/views/ingress.py`,
   or the call site that constructs a trivial one.
3. Render it in `application/views/render/` (`ansi.py::to_text` /
   `markdown.py::to_markdown`) and/or
   read it where the fact is surfaced — `LiveDashboardProjection._handle_phase`
   matches on the typed view.
4. If the field also appears in post-hoc `log.md`, set it in `from_disk_log`
   (`application/runner/output.py`) — that builder reads on-disk `index.json` for
   **cross-cycle** rendering and is a genuinely separate source, not a roundtrip shim.

**A field on the ROUND file is not this recipe — it is one edit.** Declare it on
`RoundOutcome` (`domain/results.py`) and it rides the round's close, reaches every reader
of a standing round, the typed round route and the `rounds/round_NNNN.json` checkout,
because the model IS the document. There is no payload builder to mirror it into.

**But the webapp's live surface is not the round** — it reads `dashboard.json`. Which model you
mirror onto decides the cost, so ask first *whose* fact it is:

- **Per-CANDIDATE** (it already lives on `ScoredCandidate`) → add it to **`ArmReading`**
  (`domain/results.py`) and stop: a dashboard row live or closed, a scoreboard row and a tree
  node all embed it, built by `ArmReading.of` off the score report. `build_candidate_rows` fills the live half from the slot the buffer
  holds — banked at `candidate_scored`, then folded onto by `RoundBuffer.stamp_fit` at the
  election — so **ask WHEN your fact exists**: one the scorer knows per sample rides
  `_composite`, one the election stamps rides `ElectionRecord.fit`, and a field that reaches
  neither is null on every live row until the round closes. The webapp seam is `CandidateRow`
  (`lib/types/candidate.ts` + `lib/derivations/round-candidates.ts`, which maps both halves
  through ONE function).
- **Per-ROUND** → mirror onto `RoundSummary` *and* hand-write the line in
  `projections/live_dashboard/round_summary.py`.

Reach for the round-level route only when the fact genuinely isn't a candidate's, and only for a
fact a panel renders: one the engine alone reads stays on `RoundOutcome`, and a served copy of it
is dead surface.

**A display field is not done until you have SEEN it render.** Every step above wires a
declaration and a reader; none of them writes the value, and a field that nothing writes is
invisible to every check that could exist — it is declared, so no schema refuses it; it is read,
so no compaction moves it; the panel renders blank and each reader honestly reports "this backend
does not say". `domain/scoring.py` names this as the third direction and states outright that no
assert can catch it, so the enforcement is procedural and costs one glance: run it, look at the
surface, confirm a number appeared.

**Guard:** the two-factories-onto-one-View correctness invariant — the live
builder and the disk builder must produce an equal `RoundCompleteView`. No
standing test (a broken round-trip surfaces as a wrong/empty dashboard; the
structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

Contract: [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md).

---

## 4. A resume / decision checkpoint kind

A decision a resume re-derives, or one it only archives.

**Recipe:**

1. Name the kind: a value of the deciding party's `CheckpointKind` enum — `BenchCheckpointKind`
   (`domain/run_records.py`), or the optimizer's own in its package (potter's
   `PotterCheckpointKind`, `optimizers/potter/records.py`) — or, for a plugin, its own string.
2. A member records it with `ctx.decide(kind, inputs, outcome)` (`bench/node_context.py`), which
   stamps its node and round; the bench's own go through `record_decision`.
3. If a resume must re-derive it, register a replayer for the kind on the optimizer runtime's
   `replayers`. **Registering one IS the gating**: a kind with none is archived and never compared.

**Guard (no standing test), run where the registries complete
(`wiring.py::complete_registries`):** `replayers.py::replayers` refuses two runtimes claiming one
kind. `cli/commands/launch.py::divergence_hint` lists the checked kinds off that table.

---

## 5. A connector (backend)

A new backend kind — one file under `connectors/` defining `CONNECTOR`, owned step by step by
[`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md).

**The usual reader here is not us** — it is someone with a backend already running who wants it
optimized, working through it in one conversation. **The wiring is the easy half**: four required
fields, and the guard below catches a half-wired one before a run spends. Step 2 is what decides whether the
campaign is worth running, and nothing about their backend tells you the answer. Each step's output
is the next one's input.

### Step 1 — Learn the shape, cheapest source first

Read what they already have (spec, handler source, a saved response, a `curl` line) — that costs
nothing and answers most of it. Then **probe the live endpoint once**, with one real input they
choose, which is the only way to see what it *actually* returns rather than what it documents;
it spends against their provider, so ask first and fire once. Ask them only what neither answered.

Come out knowing five things, and say them back before writing code: the **request** shape, the
**response** shape, **where the number is** in it (or that there isn't one), what one row **costs**
in seconds and dollars, and which request fields are **safe to vary**.

### Step 2 — Decide what is being improved, and how it is graded

The hard half, and the one that has no default. Five questions, each with a consequence:

- **What is ONE measured row — one request, or a whole run?** It decides `measured_unit` and
  `max_cells_in_flight` — and NOT how long an operator's concurrency press lasts, which is the
  round's to spend and no connector's to declare;
  [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The measured unit.
- **What decides whether that row was good — a label, or a number something else produced?**
  That is the ANSWER SHAPE, declared in `extract_experiment` and nowhere else; § The answer shape
  on the same page has what each choice commits you to.
- **What is the formula?** `campaign.yaml::scoring` — `per_sample` is *was this cell RIGHT*,
  `per_cell` the optional composite of what it was WORTH (latency, cost, reliability), and θ is
  fit on the second. Namespace: [`stable-api.md`](stable-api.md) § 2. **Start with `per_sample`
  alone.** A cost term needs a MEASURED anchor, and the first campaign is what measures it.
- **What may the optimizer move?** `nodes.{node}.optimizer.param_keys` in `pipeline.yaml`. The
  prompt is a lever only if the node declares `prompt_info` — a `remote_http` backend serves that
  over `GET /pipeline`, an `in_process` one must declare it, or every variant would score
  identically as no-skill: run init refuses that shape
  (`pipeline_resolve.py::_validate_prompt_reach`). Prompt fields in `param_keys` do not stand in
  for it unless the connector declares `prompt_fields_as_node_params`, which only the recursion does.
- **What must it never move?** Anything that is a cost rail rather than a search axis stays
  pinned in `config` and out of `param_keys` (Harbor's `max_turns` moves a cell's cost by an
  order of magnitude). `model` and `provider` are structurally unreachable and need no decision —
  [`optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md).

And one question that is theirs, not ours: **which slice have they reserved as test?** Never
optimize on the rows they will later report on. [`dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md)
§ Adding a dataset — the wiring process owns the rest of that decision; its step 1 is about
published benchmarks and does not apply to a private backend, but steps 2–4 do.

### Step 3 — Wire it

The connector file, then the dataset directory
([`datasets/CLAUDE.md`](../../datasets/CLAUDE.md) § Canonical layout). Every tunable starts at its
**floor**, never its centre —
[`optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md)
§ Origin = conservative floor.

### Step 4 — Screen the instrument before funding a campaign

Set `max_rounds: 0` and measure the origin only. **Read the spread, not the mean.** An origin at
0% is floor-pinned and an origin at 100% has nothing for a prompt to move; both are unusable
instruments and neither is visible from a single cell. `seed-screen`, `noise-floor` and `verify`
answer the sharper versions of this, and `evidence` answers whether two readings can be compared
at all — [`persistence-and-state.md`](../operations/persistence-and-state.md).

**Guard (at registry completion, no standing test):** `connectors/__init__.py::_validate` raises
if any connector is half-wired, and its own raise enumerates every half-wiring it rejects — read
the list there rather than a copy here.

Three things the recipe cannot show you:

- **Execution mode** — owned by
  [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode — declare
  `execution` on the connector; transport is never a branch in the core loop.
- **The answer shape** — owned by
  [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The answer shape — decide
  it while writing `extract_experiment`, which is where a verifier-graded backend yields
  `ground_truth: None` and where it declares that nowhere else.
- **The credential** — owned by
  [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The credential rides the
  connector — declare it on the `auth_token` hook, never read one at a construction site.

---

## 6. An optimizer node

One of the optimizer's own LLM nodes — `optimizers.llm_nodes()` enumerates the structured
ones (each an `LlmNode` member declaring its own `response_model`), and is the only place that count is correct. The JSON declaration format
and registry live in [`developer/node-standard.md`](node-standard.md). A node renders a
`PromptTemplate` through the same `DispatchHub` fill path as every other node —
adding a slot it needs is §2.

**Guard (at template load):** `validate_template()` at `load_optimizer_prompt` rejects any
`{{slot}}` the node's template references that isn't in `injection_table()`. Keep every
optimizer LLM call on the one `bench/llm_call.py::llm_call` path — an
unwrapped LLM call is an automatic block at review (pre-flight gate), not a test.

**A whole optimizer is one package and edits nothing outside it:** `members.py` exports `MEMBERS`
and `RUNTIME` (a plugin ships them through the two entry-point groups instead), the runtime's
`manifest_dir` holds its `pipeline.yaml`, its payload is a `RoundPayload` registered under the
manifest's name and its decision kinds a `CheckpointKind` its runtime gates.
[`examples/optimizer-plugin/`](../../examples/optimizer-plugin/) is that package, installable as it
stands, and the [offline run](offline-run.md) runs it as the proof.

---

## 7. A CLI verb

A new `python -m promptpotter <verb>`. The CLI is a **thin shell**: parse, call into
`application/`, format. Business logic that lands here is drift.

One module under `presentation/cli/commands/`, one argparse subparser, one `COMMANDS` row —
the wiring is owned by [`presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md)
§ Layout, and an import-time check pins the parser and the table together. Prefer a module over a
subpackage: `lifecycle.py` holds the thin `CommandDispatcher` shells in one file, because a
directory per verb bought a reader a hop to learn there was nothing to choose. A shell that
needs an import the others do not is its own module (`bench.py`), so the rest do not pay for it.

**Two decisions the wiring does not make for you.** Honor the verb's class — **write**
(`new` / `resume`, which mint or extend a cycle), **lifecycle** (`archive` / `delete` /
`unarchive` / `reset`), **manifest-edit** (`rename`, which rewrites `campaign.json` in place
and leaves the tree and every measurement where they are), **diagnostic** (`ab` / `verify` /
`noise-floor` / `seed-screen` / `decision-bank`, which must not perturb an existing cycle's measurements), or
**maintenance** (`reindex` / `restamp` / `compact-archive`, which rewrite stored artifacts on
purpose). A maintenance verb owes two things a diagnostic does not: it is dry-run by default,
and it refuses while a producer could still be writing what it rewrites
(`application/maintenance/archive_maintenance.py::archive_writers`) — except `reindex`, which
rebuilds a derived index from the cell files and deletes nothing, so it owes neither. And do
**not** add a read verb: reads happen by opening the artifact tree. **The readings in no file**
— owned by [`persistence-and-state.md`](../operations/persistence-and-state.md) § Active session
pointer; a read verb prints the application reading its REST peer serves. **An L4 inner run is
addressed on the reads, never on a run-control verb** (`cycles --inside`, an `evidence` / `verify`
subject's `;in=`): every other cycle-scoped kind declares an `inner_refusal`
(`application/commands/payloads.py`), so its verb takes no such flag. Raw-file ingest is
`new <file.csv>`, not an `ingest` verb.

**Guard:** the import-time check named above — `COMMANDS.keys()` must equal
`parser_verbs(build_parser())`. If the verb answers a `/commands/{kind}`, §8 owns the other half.

---

## 8. A control-plane command kind

A new `POST /commands/{kind}`. **The vocabulary is `domain/command_kinds.py`** — five `Literal`
aliases by scope, and `ALL_DISPATCHED_KINDS` derived from them. It sits in `domain/` and not
beside the dispatcher because the parties that must agree on it cannot all afford to import the
dispatcher: the CLI resolves command bodies lazily so `--help` does not pay for the application
tree, and `scripts/build_ts_types.py` emits the `CommandKind` union from these names alone.

Join the right `Literal` and four import-time asserts start demanding the rest of the wiring:

| Add | Where | The assert that demands it |
|---|---|---|
| a capability | `CAP_FOR_KIND` (`application/commands/dispatcher.py`) | `set(CAP_FOR_KIND) != ALL_DISPATCHED_KINDS` — a kind with no cap is a silent unguarded verb |
| a handler | `HANDLER_FOR_KIND` (same file) — `module:handler`, in the `application/commands/` module named for what the kind does; the dispatcher imports it when the kind is dispatched, never at its own import | the same loop, beside the capability's |
| a payload model | `PAYLOAD_MODEL_FOR_KIND` (`application/commands/payloads.py`) | the sibling raise beside it |
| **the terminal's half** | `CLI_VERB_FOR_KIND` (`cli/campaign_runner.py`) | totality over `ALL_DISPATCHED_KINDS`, plus every named verb being a real `COMMANDS` key |

`CLI_VERB_FOR_KIND` is the `<entry-point-parity>` guard. Its value is either
a CLI verb or `None` — and `None` is a **declaration**, not an escape hatch. Exactly one exists
(`set-sample-lookahead`, whose absence from the terminal is the contract; root `CLAUDE.md`
§ Conventions). Writing a second one is a design decision with a reason, not a wiring shortcut.

Then declare it on the wire: `docs/specs/api-openapi.yaml`, *before* the handler lands
(root `CLAUDE.md` § Pre-flight gate). The router needs nothing — `_WIRED_KINDS` is
`ALL_DISPATCHED_KINDS` minus the typed routes, so a new kind is wired by default and staying
*unwired* is what has to be written down.

---

## 9. A served read

A new `GET`. **Not a Control-remote command and not a sixth I/O kind** — that kind is defined by
MUTATION, so a read adds no ingress and no writer (`architecture.md` § Control-remote).

**Nothing here is caught by an import-time assert.** Steps 2–4 are what make a read
machine-checked at all; skip them and every gate stays green over a surface nobody declared.

| Step | Do | Why it is not optional |
|---|---|---|
| 1 | **Name the scope the read is a fact ABOUT**, and address it by that entity's id | A read keyed on the wrong entity is not a bug you find later — it answers *plausibly and wrongly* |
| 2 | **Declare path + response schema in `docs/specs/api-openapi.yaml`, before the handler** | Root `CLAUDE.md` § Pre-flight gate. That file has **no enforcer** for reads, so this one is a review act — the only step on the page nothing can catch |
| 3 | **Resolve in `application/`, never in the router** | ADR-0006 (`cli/` + `embedded_run.py` must reach it) and `presentation/CLAUDE.md` § Out-of-bounds |
| 4 | **Give the route a `response_model` and register it in `scripts/build_ts_types.py::EXPORTED_MODELS`** | Now `--only openapi` and `--only ts-types` cover it, and `webapp/CLAUDE.md` § A wire shape is GENERATED keeps its hand-write escape closed |
| 5 | **Serve provenance for anything the client could otherwise infer** | A closed set belongs on the server (`webapp/CLAUDE.md`). Every value the browser has to *diff* to explain becomes a client twin that drifts |
| 6 | **Add the row to `webapp/CLAUDE.md` § Display-data sources** | A data class with no row is a data class with no owner, and the gap fills itself with stores |
| 7 | **Run it and look at the panel** | §3's rule, and it binds hardest here: a served field that is declared, typed and never *written* renders as nothing, and no check that could exist would see it |

**Reads and the polling rule.** `roadmap.md` § Hard ordering requires new webapp data panels to
ride `dashboard.json` + the SSE tail. A GET is allowed beside that pair only when it is
**one-shot topology** — its answer cannot change unless something the poll already announces
changes, so the poll is its *invalidation signal* rather than its transport. Anything that
changes on its own schedule belongs on the poll.

---

## The shared discipline

Three invariants hold across every recipe above, each owned elsewhere and each breaking loud,
which is why [`tests/CLAUDE.md`](../../tests/CLAUDE.md) says not to test them: **one ingress**
— owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md)
§ Persistence — one ingress, two projections · **layers don't reach backward** — owned by
[`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) § Layer rule · **registries
are code-derived** — owned by [`../../promptpotter/CLAUDE.md`](../../promptpotter/CLAUDE.md)
§ Ask the typed predicate, never a set of names.
