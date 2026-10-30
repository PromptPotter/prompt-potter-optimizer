# Dispatch hub + L1 layout + L2 internals

Reference for `promptpotter/application/optimizers/potter/dispatch/` — the registry that fills `{{placeholders}}` in the four optimizer prompts — and for `L1Layout`, the structural surface L2 edits to decide what L1_GENERATE sees. **L2_CONTEXT firing lives here too**, from § Trigger down: L2 is one entry in this hub — its manifest node's member (`potter/members.py::L2Context`), a `Layer` as L3 is, same `fill` path, same `Bundle` per-call state, and the hub is what stops it accumulating its own renderers, surface object and escape hatches. Concept role: [`../concepts/the-loop.md`](../concepts/the-loop.md).

**Optimizer prompts.** Every optimizer LLM node — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — renders an `OptimizerPromptTemplate`, whose `RENDER_ORDER` is cut for the provider prefix cache. The target prompt it produces has no class: each prompt node declares its fields in render order (`NodePromptInfo.fields`), and that order sits inside the archive key. **No prompt site summarizes its own data**: a name not in `injection_table()` does not enter a prompt.

The hub is stateless. `injection_table()` (`dispatch/injections/registry.py`) is built and checked on its first call from the `@signal("<name>", …)` decorator at each renderer's definition site — read an entry's fields off `_Injection`. `validate_template()` (called from `load_optimizer_prompt`) raises on `{{slot}}` names not in the registry: a typo fails at template load, not at first render.

`citable` answers one question: may an `l1_generate` variant name this panel in `evidence_grounding`? True for panels that REPORT (what was measured, what failed, what the layers steered); False for the value-space menus and the prompt under edit — citing those grounds a mutation in its own subject. `citable_fields(layout, exploration_budget, rendered)` intersects the flag with the node's **live layout** and what rendered, so what L1 may cite is exactly what L1 was shown; that call fills the wire schema's enum — the one place the menu is stated — and the round banks it as `L1Readout.l1_citable`, which `evidence_grounding_present` reads. Adding a panel to a floor makes it citable automatically.

## Reference

**The registered signals** are every `@signal(…)` across `injections/layer_state.py` · `panels.py` · `catalogues.py` · `wounds.py` — each renderer is its slot's SoT — plus the caller extras (`n_variants`, `citable_fields`). `[fenced]` = output wrapped in `<UNTRUSTED_DATASET_CONTENT>`.

### Every item that reaches an LLM carries an upper limit — bounded where it is PRODUCED

**No injectable is unbounded, and a `char_cap` alone does not bound one.** The limit has to exist where the text is *authored*, because that is the only place an overlong item can be judged **faulty** rather than quietly shortened — truncating at render turns a producer's fault into silent content loss with nobody at fault and every gate green. Bound it where it is written, cap it where it is rendered, let the composition spend what is left.

| where it is produced | the bound that judges it |
|---|---|
| an LLM optimizer output (`plan`, `critique`, an L2/L3 field) | `max_length` + a `_truncate*` validator on the response model (`dispatch/schemas.py`) — the parse boundary |
| operator-authored framing (`task_context`) | `TaskDecomposition.check_budget` at mint — per field **and** in total, so five legal fields cannot compose an illegal framing |
| a derived/measurement view | its own `*_RENDER_CAP` top-K, which bounds rows rather than characters |

`char_cap` catches only what those missed, and only on the indivisible panels: `facade.py::_cap_runaway` is the runaway backstop and emits an `injection_budget_overrun` warning. No other panel carries one: a production cap pre-decides how much a panel takes against a ceiling the panel cannot see, and a set of such caps chosen one at a time has a sum nobody owns.

**The composition owns the budget, the fence and the count — because a panel can see none of them.** A composition-site bound may **select**, never **cut**: slicing a rendered panel only chooses which half the model sees. Renderers hand back `list[Item]` of `(text, trusted)`; `compose.select` takes each panel's next item in layout order, round after round, until the node's `NODE_LAYOUTS[node].discretionary_chars` is spent by the DISCRETIONARY panels alone, leaving a smaller COMPLETE package rather than half of one. What a renderer must do about it:

- **`L1_MANDATORY` is admitted whatever it costs**, served first and charged separately — a first claim on a budget is not the same promise as being present. A mandatory panel the composition cannot place raises `MandatoryPanelStarvedError`; a renderer that *raised* halts the run as `RENDER_ERROR` — an operator-recoverable stop rather than a degraded prompt.
- **Selection drops whole items and never slices one**, and every panel places its first item before any panel places its second, so no panel can crowd the package by being large.
- **A renderer never bakes its own fence** and writes no `(+N not shown)` line: it cannot know where the selection will cut, and an unterminated fence lets sample text run loose to the end of the prompt — a prompt-injection surface.
- **Row granularity is what stops starvation.** A panel handing back one large fenced block cannot be thinned, only starved whole.

Which panels may be thinned is `InjectionKind.divisible`, asked of the kind every signal already declares (read the kinds off `InjectionKind`): MEASUREMENT and DERIVED are evidence and thin gracefully, TRACE and DIRECTIVE carry state and are placed whole or not at all — asked of the kind rather than a set of names, because a set silently skips whatever it failed to list. `mutation_memory` renders **newest round first**, so what the ceiling drops is the OLDEST attempt. `prompt_chars` on the ledger stays the measurement, and what selection dropped rides beside it.

Two corollaries are easy to miss: input length is a quality tax and not only a bill — every model degrades as its input grows — and **the response JSON Schema is prompt text**, riding `response_format` on every call, so its field names and `description` prose are bounded on the same rule.

### Round-end measurement — what L1_SCORE + L1_CRITIQUE leave behind

- **`diagnostics`** ← a plain STATUS prefix (layer fires), the fenced `RoundDiagnostics` body (`compute_round_diagnostics`, least-actionable last so the ceiling refuses those first), and EVOLUTION — the ONE progress view on the one comparable column: the L1 stall (`domain/run_standing.py::rounds_without_advance`), each round's `overlap.advance` label, the origin panel's line. **No accuracy column**: a round's own accuracy sits on cells the next round re-picks, so it is no series.
- **`l1_wounds`** [fenced] ← the last measured round's arms + `Cycle.runtime_failures` — L1-owned wounds, one block ([`self-healing-internals.md`](self-healing-internals.md)). Its runtime config shows only the keys L1 may set whose values are a closed vocabulary or a number, so a route or provider it cannot touch never reads as a lever.
- **`guard_breaches`** ← `L2Memory.guard_breaches`, `L3Memory.guard_breaches` · post-parse breaches. Plain: evidence values render only where they name a closed vocabulary; an LLM-authored placeholder or plan reports its size, so no untrusted content reaches the unfenced block.
- **`critique`** ← `bundle.digest.critique` (`format_l1_critique_for_prompt`). **A cluster spans as many rows the panels marked as `detectable_move` names, two at least, or it is not one**: an edit fixing a smaller one whole could not separate from its parent. `minItems` is advisory on a `strict: False` wire, so `l1/critique.py::spanning_clusters` decides against `panels.py::clustered_sample_ids` and `panels.py::detectable_rows`, and drops what spans fewer (`critique_cluster_dropped`). **Rows are the key, never a label**: the generator answers `targets_rows` as `[#id]` marks, and `validators/l1_behavior.py` scores a variant whose rows are unmarked, fewer than two, or spread over two clusters — a label is coined freely on that wire, an id is checkable against the prompt. `[#id]` is the one mark for a row a cluster may span.

### Strategic injects — L3_PLAN writes the plan, the operator authors the framing, persistent

- **`plan`** ← `L3Memory.plan` · L3_PLAN-only writer; each fire replaces it whole.
- **`task_context`** ← `Cycle.framing` — fields per `TaskDecomposition`, authored at check-in and **FROZEN there**: no L1/L2/L3 wire schema has a field of it, so no fire can move a byte of it (why it is the one `PREFIX_STABLE_PANELS` member — § L1 layout). On `l1_generate`'s floor only; `l2_context` / `l3_plan` hold it in `possible`, an axis L4 can search back in. `upstream_context` / `downstream_context` render here and also splice around `problem_description` into the TARGET prompt (`OptSearchPoint.scored_prompts`) — this panel is the only place L1 sees them as context.
- **`l3_to_l2_note`** ← `L3Memory.note` · L2_CONTEXT only; excluded from L1_GENERATE.

### Cross-round derived

- **`axis_memory`** (DERIVED) ← `Cycle.axes().digest()` — AxisIndex per-axis effect size + sample coverage, for L1_GENERATE, L2_CONTEXT, L3_PLAN; empty until AxisIndex is initialised.
- **Panels family** (`injections/panels.py`) — each one's ordering and caps live at its `@signal`; here only what each is FOR:
  - `escalation_panel` — L1 stall depth + `exploration_budget`; gates the `stall_exploration` citation.
  - `evidence_health` — per-node failure rates: an evidence-starved enricher is an upstream fault no param mutation fixes.
  - `answer_distribution` — the collapse detector (accuracy cannot tell reasoning from one repeated label); it renders the rule, `domain/scoring.py::enumerable_truth_labels` owns it, shared with the θ gate and PoBB.
  - `failing_samples` — a current miss as ONE row, easiest-first on the locked δ ruler; once clustered, only the rows the clusters span, so the generator reads no id whose row it cannot see. Never a tally by label: a cluster is a mechanism and a tally shows none.
  - `sample_transcripts` — the whole trace; a looping one (`dispatch/bundle.py::LOOP_NOVELTY`) shows head and tail only. Its query is the head, plus the one line below the cut that names the gold answer (`panels.py::_query_excerpt`): a head cut alone shows a context dataset's distractors and never the paragraph the miss turned on.
  - `inner_narratives` — each row as its PAIRED difference from the origin on the same seed.
  - `mutation_memory` — what this cycle ALREADY tried and settled: each edit that separated from its parent on its whole set, or collapsed to one label (`panels.py::_Edit.verdict`), with its words, its score and the cells it gained and lost (`application/scoring/paired.py::flipped_keys`). An edit inside the noise says nothing of its idea, so it is left out, never shown as a loss.
  - The **decision frame**, short and self-suppressing: `measurand` (what ELECTS and the θ ± se it reads), `detectable_move` (the smallest gain this round could tell from zero, in rows), `confounds` (cold ruler / collapsed δ band / subset moved whole — measured, silent when none is), `budget_state` (round, spend; the one non-citable member).
- **Capability directives** (`injections/layer_state.py`: `rebase_capability`, `terminate_capability`) **ride the layout channel** — on the `l2_context`/`l3_plan` floors AND mandatory sets, so a layout edit that excises one is rejected and a prose rewrite *cannot* drop them; the config bit is the one sanctioned way to silence one. The base prompts' one remaining prose token is the inline caller extra `{{n_variants}}`, guarded by `validators/l1_strict.py::l1_inner_placeholders_intact` on the MERGED params.

### Current state

- **`rendered_prompt`** ← `parent.prompts` ⊕ `effective_optimizer_prompts(pipeline_schema, pipeline_params, inner_cells)` — **the artifact under edit**.
  - It renders one LABELLED section per locus, `[node.field]`, in each node's declared order: the override schema keys on those names, so an unlabelled blob would ask the generator to attribute a paragraph whose boundary the render had stripped. Each section carries the field's OWN value — a replacement written over a spliced value would absorb the framing as prose the next render splices around again.
  - Both halves render — the target prompt and the inner nodes' optimizer prompts (`runner/inner/tasks.py::InnerCells.owners`) — each empty where it is not the mutation surface, so a normal campaign is bit-identical and an L4 outer point (no prompt locus) does not render a MANDATORY panel as nothing. Every mutation is a whole-field replacement, so a truncated render would be worse than an absent one.
- **`pipeline_param_catalogue`** ← `pipeline_schema`: `node_param_keys` for WHICH axes, `PipelineSchema.param_options` for each one's value space (the one resolver). A one-value axis is dropped (`PipelineSchema.pinned`), so a variant cannot spend its mutation proposing the value already running. Seated directly under `rendered_prompt`, because it names the prose sent WITH the node's prompt and the matcher's extraction contract (`matchers::MATCHERS`).
- **`block_pool`** ← the `BlockPool` slice through `InjectionBundle.block_window()`, gated by the node's `block_access` — the value space of a prompt FIELD, as the catalogue is of a PARAM. A block is seeded only where the run holds what its `Snippet.presumes`. `closed` narrows each field's wire schema to the window (`validators/l1_strict.py::l1_blocks_in_pool`); `off` renders empty, bit-identical to a no-pool ablation.
- **`demo_pool`** ← `InjectionBundle.demo_pool` + the node's `k_max` · the value space of the `shots` slot, as demo-pool ids: the parent's shots first — the one place L1 sees them, since shots render only in the target, last, resolved by key (`OptSearchPoint.scored_prompts(…, demo=)`) — then a window of `pool_window` rotating by round. `L1_MANDATORY` for that reason; silent with no pool or `k_max: 0`. `validators/l1_strict.py::l1_shots_in_demo_pool` rejects an id outside the pool, a repeat, or a list past `k_max`.
- **`recombination_pool`** ← `Lineage.measured` ranked on `Lineage.theta` (unread last): the top `pool_window` of the population, marked main, then of the rest, and the loci a main can take from another entry · the value space of the top-level `recombinations` slot, as id heads (`main` the marked ones, `donor` any) and locus names on an enum. `L1_MANDATORY` as its one carrier; silent at `recombine: 0`, under another `recombination_host`, or with no two tradable entries, which withdraws the slot. `l2_recombination_pool` is the same pool on `l2_context`'s floor, rendering only under `recombination_host: l2_context`.
- **`l1_overrides`** ← `L2Memory.steered("l1_generate")` less its `layout` — the call settings L2 moves (`L2Context.steers`): `n_variants` enters L1_GENERATE only via the `{{n_variants}}` caller extra; `temperature` never reaches the prompt text.
- **`l1_layout`** ← `node_layout("l1_generate", l2)` · the one name that is BOTH a structural input and a signal. ONE resolution for every node (`dispatch/prompts.py::node_layout`): the layout a layer steered onto it, else an outer's declared edit, else `NODE_LAYOUTS[node].floor`. Registered on `l2_context`'s floor too — an edit names a panel and its target slot, so the writer must read where each sits now.

### L2_CONTEXT / L3_PLAN-internal

- **`l1_signal_catalogue`** ← `NODE_LAYOUTS["l1_generate"].mandatory` · the one layout rule no JSON Schema can state — after an edit is applied, each mandatory signal must still sit under SOME slot. It binds the MERGED layout, so it constrains what an edit may take AWAY and never asks L2 to restate slots it is not changing. Slots and the signal enum ride `l1_layout`'s own schema (`layout_json_schema`).

### Caller extra — L1_GENERATE template scalar (`l1/generate.py`)

**`n_variants`** — substituted by `compile_prompt`, not a signal; capped in `l1/candidate_source.py`. A directive L1_GENERATE obeys.

## Mechanics

- **Entry points** — `render(name, bundle)` and `fill(template, bundle, *, node)`, both stateless; the layout is resolved inside from `node`, never passed in. `InjectionBundle` is the per-call frozen state built once via `build_bundle(cycle)` over the cycle's LAST round; the bench folds a round in (`Cycle.absorb_round`) before its adapters run, so `l1_critique` reads the round it distils like every other node.
- **No optimizer prose token names an injection** — every surviving `{{token}}` in the shipped prompts is a caller extra, and every `problem_description` body is empty, so the template carries no per-ROUND value to void the stable prefix behind it.
- **`L1_MANDATORY` is two kinds, and the second gets forgotten**: a field L1 cannot OPERATE without (parent prompt, plan, task framing, mutation surface, failure digest), and the sole carrier of a state L1 must not enter BLIND — `answer_distribution`, without which a collapse onto one label is invisible to the run collapsing, and `measurand` + `confounds`, without which the generator optimises a column it cannot name and reads a cold ruler as ability. Missing after a merged edit fires `l1_layout_missing_mandatory`, a guard breach routing to L3.

## L1 layout — L2's structural edit surface

L1_GENERATE's prompt is composed by walking a per-slot list of **injection names** (`records.py::L1Layout`, one list per addressable slot, declared IN RENDER ORDER) and resolving each through the registry. L2 owns the layout; the registry is closed and code-derived. Static text in each slot stays and the layout's renderings are appended. `answer_format` is not a slot — it carries L1's output JSON schema, a code contract. Renderers are layer-agnostic: an injection needing to differ per layer is two injections.

**`VOLATILE_SLOT` is the provider prefix-cache boundary.** An implicit prefix cache hits on an identical LEADING byte range, so a changing panel ahead of the boundary re-bills the static template itself at full rate every round. An import-time assert holds every floor behind the line, and `validate_l1_layout` reports an EDIT that crosses it (`l1_layout_voids_prefix`, soft: placement is a real axis). `PREFIX_STABLE_PANELS` is the exemption, and **membership is a claim about a WRITER, not a renderer**: `task_context` is its one member because no layer writes the operator's framing, while every other panel moves when the measurement does. Adding a name means re-reading its renderer and its writer — a panel merely stable today is untested.

**Default floor** — `NODE_LAYOUTS["l1_generate"].floor`. **Read the membership there, never from a copy here.** **Order is priority in a second sense**: `dispatch/compose.py` selects in layout order under the discretionary allowance, so the decision frame is placed before a large panel can crowd it. The floor is what a *first* L1 round reads, not what most rounds read — L2 fires edit it, and L4 optimises that authoring.

**Validation — split HARD / SOFT.** `validate_l1_layout(layout, *, spec, prior_layout)` enforces against the node's `NodeLayoutSpec`:

- HARD — missing mandatory placeholder, name outside the node's `possible`. **What it costs is the caller's.** L2's edit keeps the prior layout and appends to the guard-breach stream, self-healing on the next fire. An L4 override is REJECTED at proposal (`validators/l1_strict.py::l1_inner_layout_applies`) and rides `validation_failures` — substituting the floor there would spend a whole inner campaign on the parent's information flow and report it as the edit's own reading. `resolve_layout_override` is the one derivation both boundaries ask.
- SOFT — `l1_layout_voids_prefix` and `l1_layout_unchanged_from_prior`. Applied, and reported on the same stream; a `ValidatorOutcome` carries no score and no severity, so no consumer may escalate on the stream alone ([`self-healing-internals.md`](self-healing-internals.md) § Wound 4 — immediate, never patient).

**A signal sits in at most ONE slot, and the WIRE SHAPE is what makes that true.** `fill` appends one render per occurrence, so a name in two slots is emitted twice — and no `char_cap` can see it, being applied per render. An edit addresses a panel and names the slot it moves to, so there is no second place for it to land; the floor is the only producer that could still name a panel twice, and `dispatch/layout.py`'s import-time block asserts it does not.

**Adding an injection** → [`adding-a-surface.md`](adding-a-surface.md) § 2. A prompt injection.

## Trigger — when L2 fires

The `ladder` slice (`records.py::Ladder`) is a frozen value replaced whole and banked on the round it stands on; the pure functions of `escalation/state.py` answer the next one. After every L1 round `observe` applies the `StallEffect` `domain/origin_panel.py::ROUND_ADVANCE_INFO` declares for the round's `overlap.advance` — and when the count hits `l1_patience`, L2 fires. Preemptors fire L2 *before* patience (`escalation/rules.py`); `l1_evidence_starved` is the self-heal-vs-HITL fork: a starved round routes to L2 so it can read `evidence_health` and refine or **terminate**. **Deterministic rules only route; they never diagnose or stop** — termination authority belongs to the most-general reader, and a backend-coupled deterministic check only WARNS.

The decision is recorded as `PotterCheckpointKind.L2_ESCALATION_TRIGGER` (`data.rule` names the matched rule), gated **ARCHIVAL**: the trigger is a fold over the cycle's escalation history, not a function of one round's measurements, which is what a replayer is pure over. On resume the ladder is taken up from the last standing round's `optimizer_state` (`SliceMap.seat`), not re-derived.

## Inputs — L2 via the hub

L2's injection set **is** `NODE_LAYOUTS["l2_context"].floor` — read the membership there; its template's `problem_description` body is empty, and no L2-only surface object exists. L2 reads its own refusals through `guard_breaches`; a refused edit fires L3 at once, so by L2's next fire it reads the new `plan`. `l1_signal_catalogue` is L2-only and absent from `l1_generate`'s `possible`, so L2 cannot inject its own catalogue into L1.

## Outputs — what L2 writes

The wire model is `dispatch/schemas.py::L2ContextOutput`. Every field is optional at the PARSE boundary — a missing one leaves the corresponding state untouched, so an omission never costs the rest of the fire. Only the LEVERS are optional to *write*: `l1_layout` (what L1 looks at), `l1_overrides` (how hard it explores) and, where `recombination_host: l2_context` offers it (`L2RecombiningOutput`, closed on the `l2_recombination_pool` panel's ids), `recombinations`; a fire touching none is a wasted escalation, scored by `l2_targets_l1_surface`. The REASON — `axis_targeted` + `rationale` — rides every fire, graded by `l2_rationale_substantive` and `l2_evidence_anchored`. `terminate_proposal` is the HITL exit: on evidence-starvation L2 names the dead node and what to fix, and the cycle halts (`StopReason.OPTIMIZER_ABORT`). Both control outputs are gated by their capability bit — [`../../promptpotter/application/optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md) § The layer-control channel.

**Two fields this schema deliberately does not have**, both stated on `L2ContextOutput`: `task_context` (the operator's framing is frozen for the run) and `action` (probe rounds are not wired — [`../specs/roadmap.md`](../specs/roadmap.md) § Probe rounds).

`L2Context.parse` (`members.py`) constructs a `TransitionResult`:

- `axis_targeted` — the fire's evidence anchor; deliberately **not** a steering surface: L1 reads its axes from `axis_memory`, derived from measurement.
- `rationale` — the diagnosis, carried forward as `described`, the fire's exit-view line; an absent one is REPORTED, never replaced by a stand-in — a placeholder makes an undiagnosed fire read like a diagnosed one.
- `l1_layout` — **a per-slot EDIT, not a replacement layout**: `coerce_l1_layout(raw, base=node_layout("l1_generate", l2))` moves the listed panels and keeps the rest, the rule `resolve_node_layout` gives L4's `layout` param; a replacement silently loses every signal L2 did not restate. HARD failures roll back to the prior.
- `l1_overrides` — the keys `L2Context.steers` names, numeric only, laid key by key over the standing steer.

### How L2 steers L1

Two channels, ONE carrier — a node overlay on `l1_generate` (`L2Memory.steer`), keyed as that node's config is:

| Channel | Overlay key | L1 effect |
|---------|-----------|-----------|
| Attention | `layout` (whole, validated) | `DispatchHub.fill` walks the layout and appends each named injection's rendering to its slot. Mutating the layout reshapes which injections L1 sees and where. |
| Exploration | `n_variants`, `temperature` | L1's next call — `n_variants` (in-prompt directive via the `{{n_variants}}` caller extra) and `temperature` (the LLM call's, out-of-prompt). |

`task_context` is **not** a channel: it is operator-authored framing that L2 cannot write, and it is off L2's floor. L2 also cannot edit L1's static template text and cannot toggle `answer_format` — those are code contracts. Anything L2 wants L1 to see must already be a registered injection (from `l1_generate`'s `possible`).

### Side effects — `L2Context.land`

`potter/members.py::L2Context.land` puts the next memory — the fire's steer onto `l1_generate` where it set one, `guard_breaches` always — and `L2Context.record` answers the next ladder. Memory and ladder are potter's state slices (`records.py::L2Memory`, `L3Memory`, `Ladder`), each written by its own layer alone, held on `Cycle.slices` and never a field of the individual — so they carry across every adoption (an L1 win and an L2/L3 transition alike) by not moving, and every round banks them whole as its `optimizer_state`. Read each field's lifecycle off the models.

Two facts the models cannot tell you. **`L3Memory.note` is sticky free-text, not a failure record** — L3 sets it to steer L2, it survives every parent swap, and it is cleared only when L3 fires again. **The L1 critique is on neither memory**: it lives on `RoundResult.critique`, read through `bundle.digest.critique`, as per-round trajectory lives on `Cycle.rounds`. The campaign's `task_context` is not memory at all: it rides `Cycle.framing`.

**A fire lands after its round closed, so it restates that round's state** (`NodeContext.restate` → one `RoundRestatedRecord`): the last standing state is the one restore a resume or a fork has, and the fire's exit view displays the fire and carries nothing. The fire itself is on the ledger as `L2_ESCALATION_TRIGGER`; layout and exploration content are not separate decisions.
