# application/optimization/ — L1 / L2 / L3 agent contracts

This file is the **agent contract** for the three-layer loop — what each layer reads, writes, and decides; when each escalates; what triggers healing. The orchestration shape (Cycle, dispatch, escalation rules) lives in [`../CLAUDE.md`](../CLAUDE.md). Channels, signal routing, and the wound-signal graph (two rendered signals over the two-axis self-healing model) live in [`../../../docs/developer/dispatch-hub.md`](../../../docs/developer/dispatch-hub.md) — that's the canonical info-flow doc.

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

## Origin = conservative floor

**Start every tunable in the dataset's per-node overlay (`datasets/{name}/pipeline.yaml::nodes.{name}.config`) at its FLOOR, not its centre** — `reasoning_effort: "low"`, low `temperature`, minimal `thinking_budget`, no expensive system-prompt scaffolding. L1 expands upward from there when sibling-yield or stall evidence supports it. Starting from expanded-thinking / high-temperature defaults burns budget on round 0 and steals the headroom L1 exists to discover. Canonical per-dataset starting points + cost/stability observations: [`../../../docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md).

## Cycle stop conditions

The cycle has boundary stops — the calendar cap `max_rounds`, or (opt-in) its measurement-driven twin `OptimizationConfig.lives` ("hearts": +1 per improving round / −1 per stall, banked, stop at 0 → `LIVES_EXHAUSTED`; banks the same `improved` verdict the stall counter reads, on `EscalationFSM`). On top of those, **the L2-layer and L3-layer may end the cycle themselves** — either through the deterministic escalation rules (goal reached / infinite stall) or by emitting a `terminate_proposal` when the fault is unrecoverable through any framing or plan move (the LLM-emitted stop; see the layer-control channel below).

## L1-layer — l1_generate

The parent searchpoint was selected for measured reasons. `l1_generate` mutates a pipeline-configuration field only with cause:

- stall-driven escalation,
- critique / axis-memory evidence on the chosen axis, or
- the operator's frozen `task_context` framing (read-only evidence about the task).

No data justifying a choice ⇒ do not gamble. Random exploration is reserved for explicit stall.

`l1_generate`'s evidence base lives on its surface — every citable panel is a same-named DispatchHub injection (no phantom panels; a citable name that never renders invites fabricated citations).

**The decision frame comes first, and it answers what the evidence panels cannot: whether the number under all of them can be trusted.** Every one is short, and each self-suppresses where it has nothing to say. `measurand` and `confounds` are `L1_MANDATORY` — a generator that cannot name its objective is optimizing a column, and one blind to a cold ruler or a collapsed band will read noise as a result:

- `measurand` — the ACTIVE composite-fitness formula and where this round landed on it, terms resolved (`shared/composite.py::render_composite_fitness_block`). Not the raw `scoring` block: an expression the model has to parse first is not an objective it can aim at.
- `precision` — that level's own error bar, plus each arm's fitness interval and the scale it was read on (`ruler_id` / `ruler_n` / `calibration_model`). A level with no interval beside it invites reading a move inside its own noise as a result.
- `detectable_move` — the smallest ability gain this round could tell from zero (`shared/statistics.py::min_detectable_effect`, on the CONTRAST se, since an edit is judged against the incumbent). Without a bar, every proposal is measured against none.
- `sample_provenance` — n, whether the subset is `frozen` or `adaptive`, how much of it the last round also graded, and where PoBB cut each arm. A rate is set as much by which cells were graded and where an arm stopped as by the configuration under test.
- `confounds` — the states where the numbers above are NOT ability, MEASURED rather than warned about in advance: a cold ruler, a collapsed δ band (`DeltaRuler.band_span`), a subset that moved whole. Silent when none is live, which is the point — a caveat that renders every round is read as boilerplate by the third one. The engine computes this for the OPERATOR at INIT; the optimizer reasoning from θ needs the same statement.
- `budget_state` — rounds and spend left. **Not citable**: budget says how boldly to spend a round, never that a mutation is the right one.

- `critique` — the distiller's compression of the round's failures: `failure_highlights` (quoting SAMPLE TRANSCRIPTS; generated FIRST so a long output truncates a steer, never the evidence the next round differentiates on) + `priority_fix` + `suggested_axes`.
- `answer_distribution` — what the pipeline ANSWERS vs what is true, as label tallies, plus the score a constant single-label answer would earn. **The collapse detector:** accuracy alone cannot separate a pipeline that reasons from one emitting the same label every time, and on a skewed label set that constant *is* a respectable-looking score — without this panel a generator rewrites the instruction it is already being denied, louder, every round. Silent above `ANSWER_SPACE_CAP` (`config/settings.py`) distinct ground truths, or when every one is distinct (free-text answers have no collapse to detect).
- `failing_samples` — the misses themselves, one line each, ordered easiest-first on the cycle's anchored δ ruler (`Cycle.ruler`, never `hard_samples.json`'s re-fitted δ — and the same ruler binds SAMPLE SELECTION, so no round chooses cells on a different δ than it scores them on). The evidence beside its own compression, so a steer can be checked against what it steers about. The ordering is the one thing here L1 cannot compute for itself, and it is what stops effort going into samples nothing can solve.
- `inner_narratives` — L4 only: what an inner campaign tried (each outer sample IS one), the steer each round acted on, its winning edit, where it stalled. The outer generator's only raw window into inner behaviour, since `critique` compresses it to a few highlights; without it the generator re-proposes what the inner loop already measured. Weakest-lift-first, and only the weakest `INNER_NARRATIVE_RENDER_CAP` render at all — the tail is the panel's own least evidence, and where nothing separates from the origin the header already says the order carries no information. The count withheld is named, never dropped in silence. Silent off the recursion.
- `mutation_memory` — what this cycle already tried: the changed field and value, what it scored against its own **matched** parent, how it ended. Derived from the payload, never from `changes_description` (optional prose two candidates can share). A candidate PoBB-cut early has **no matched parent at all**, so its row reports where it stopped and nothing else — neither a partial accuracy nor a paired win/loss, because it ran a prefix of an order stratified on the incumbent's own grades, so the shared cells ARE the incumbent's misses and both readings are decided by where it stopped rather than what it did (`scoring/metrics.py::matched_parent_stats`). Its subset-invariant standing is the θ beside it.
- `escalation_panel` — stall depth + `exploration_budget ∈ {tight, normal, wide}`.
- `axis_memory` — cross-round AxisIndex digest (`cycle.axes.digest()`); per-axis effect_size vs noise floor.

If a panel field speaks against a mutation, `l1_generate` does not propose it.

**An idea this cycle already measured and lost is rejected, not re-scored** (`repeat_variant`, `validators/l1_strict.py`) — the only cross-round invariant, beside the round-local `no_op_variant` and `duplicate_variant`. It catches the failure mode L1 actually exhibits, because **a re-proposal never arrives as a repeated string: it arrives as the same idea rewritten into a different FIELD.** On `justlogic-d234` one idea was proposed for 8 consecutive rounds, walking `instruction` → `thinking_style` → `output_schema_descriptions.reasoning` → `task_intent`, and every exact-match mechanism saw eight distinct mutations while the cycle never tested a second hypothesis.

So the match is lexical — content-word overlap between the mutated VALUES (`domain/candidate_diff.py::idea_fingerprint`), never field names, which would invert the test into "touched the same field". One definition serves all three consumers (round-local dedup, this gate, the ALREADY TRIED panel's `↺`), so a repeat one rejects cannot be rendered as new by another. Because a wrong rejection is destructive and leaves no trace, four things bound it: only **measured losses** count (a 0/0 candidate is absence of evidence, and an idea that BEAT its origin is a direction to refine, not a dead end); the reject threshold is stricter than the marking one; a repeat may cost a candidate but **never empties a round**; and every rejection names the round it repeats, landing on the wound channel rather than vanishing into a yield number.

Alongside the evidence panels, `l1_generate` reads one **value-space menu per mutable surface**: `pipeline_param_catalogue` (what a pipeline param may be set to) and `prompt_block_catalogue` (what a prompt FIELD may be set to — reusable persona / task_intent / thinking_style / answer_format blocks). The block library is the **only** channel that hands L1 reusable prompt *material*; every cross-run panel beside it carries statistics *about* material. `OptimizationConfig.prompt_block_catalogue` picks the mode: `guidance` (default — renders the cycle's **earned** blocks: short field values that earned CREDIBLE lift on a run of the same answer-space shape, mined at `Cycle.start` by `intelligence/earned_blocks.py`; falls back to the task-agnostic PromptWizard reasoning modules when no earned blocks qualify), `restrict` (the static `config/prompt_variants.json` set in FULL, because there it *is* the value space; an off-library value fails `L1_PROMPT_BLOCKS_IN_LIBRARY` → synthetic-0 → L2 wound, the same structural shape as a forbidden axis), `off` (renders empty; prompt bit-for-bit identical to a no-library ablation). Reusing a block does not excuse `evidence_grounding` — the citation still names a panel.

Each emitted variant declares an `evidence_grounding: {field, citation}` naming the panel entry that justifies the mutation — **required on the wire** (`dispatch/l1_wire_schema.py`) though optional at the parse boundary, because tolerating an omission is not the same as offering one: while the emitted schema carried the model's own `| None`, whole rounds answered `null`. The citable set is **derived, never declared**: `citable_fields(layout, exploration_budget)` (`dispatch/injections/registry.py`) = the evidence-bearing panels (`@signal(citable=True)`) the node's **live layout** actually renders, plus the `stall_exploration` escape hatch once the budget widens past `tight`. One derivation feeds the prompt's menu (`{{citable_fields}}`), the wire schema's enum, and the behavior check — so L1 cannot cite a panel it was never shown. Variants without a real citation fail the `evidence_grounding_present` behavior check (`validators/l1_behavior.py`) — surfaced in `review.md` and `round_NNNN.json`. The healing rule that converts this signal into an L2 attention nudge is backlog — `docs/specs/roadmap.md` § Plus-backlog.

**Field order is load-bearing — never reorder `L1Variant` alone.** `evidence_grounding` generates second, above `changes_description` *and* the `*_override` slots (`dispatch/schemas.py`), because fields generate in schema order: emitted after the mutation, a citation can only rationalize it. Three surfaces state that order and move together — the Pydantic model (the SoT), `l1_generate`'s `answer_format` prose, and the regenerated `promptpotter/assets/optimizer/pipeline.yaml::resolved_schemas` (`scripts/build_optimizer_schemas.py`). A schema that disagrees with its own prose teaches twice, contradictorily. See [`docs/concepts/structured-output.md`](../../../docs/concepts/structured-output.md) § Which levers are actually free (lever 2, the coordinates).

Channel: `task_context` (the operator's frozen framing) and `plan` (L3-set strategy) arrive on `OptSearchPoint` and surface alongside the panels — `l1_generate` is fan-in, reading both layers' outputs in the same round. Composed by `DispatchHub.fill` (`dispatch/facade.py` — the hub has no `hub.py`) walking `opt_sp.memory.l1_layout` (l1_generate's per-node layout from `NODE_LAYOUTS`, `domain/l1_layout.py`) over the `INJECTIONS` registry (`dispatch/injections/registry.py`).

**Reviewing an L1 round trace.** Walk the checklist in the `potter-self` skill (§ The round-trace
checklist) before reporting findings on any operator-pasted round dump or cycle review — it enumerates
the checks that historically slipped past, and which are validator-enforced versus pure analysis
responsibility.

## L2-layer — l2_context

Fires on L1-layer stall (default), yield drought (escalation rule `l2_axis_yield_drought` — fires when L1 has stalled at least one round AND AxisIndex shows zero axes with effect above the noise floor), or **evidence-starvation** (`l1_evidence_starved` — a node failed across ~all of a round's samples). Post-round transitions are decided by `decide_escalation(EscalationInputs)` over `DEFAULT_ESCALATION_RULES` (`escalation/rules.py`); the rule set is the policy and replaces the prior FSM.

**The model: self-healing with a HITL escape hatch.** Two tracks, decided by the round's evidence. (1) A *healthy, analysable* round → L1 critique does its job: analyse the per-sample misses, propose mutations. Critique is the prompt-improvement surface and stays concentrated — it is **not** the issue-router and is not loaded with backend-fault diagnostics. (2) *Accumulated evidence of a systemic fault* (evidence-starvation: a node failing across ~all samples — `evidence_starved_node` ≥ `EVIDENCE_STARVED_RATE`, both `domain/results_health.py`) → a **weak preemptor** routes to L2 (bypassing l1_patience so the loop doesn't grind more dead rounds). L2 reads the context and judges: fixable by steering L1's attention → self-heal (`l1_layout` / a probe round); unfixable by any prompt move (Brave quota / backend down) → `terminate_proposal`, the HITL exit that halts carrying the human-action request (the operator banner — `DegradationHealth.suggested_action` — supplies the verbatim connector reason). Deterministic rules stay *weak* by design: they route, they never diagnose or stop. The diagnose-and-stop authority lives in the LLM tier.

Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- an attention edit — which panels L1 sees (`l1_layout`) and how hard it explores (`l1_overrides`), and
- optional optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The steer is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative moves ("maybe try X") are out of contract. An L2 fire that touches no L1 surface is a wasted escalation and is scored as one (`l2_targets_l1_surface`) — that check is the instrument for deciding whether the L2 call earns its cost.

Channel: written to `OptSearchPoint.memory.l1_layout` / `.l1_overrides`. L2 does **not** write `task_context` — see § *The framing is frozen* below.

### Probe rounds — what they mean, and why the lever is not wired

**A probe round spends its whole budget interrogating ONE thing** — one axis, one variable, one recurring mistake — instead of spreading a broad mutation set across the failure surface. The distinguishing move is **more candidates on a narrower question**, so the round returns a real answer about that one thing rather than one noisy sample of it.

**L2 cannot request one today: there is no `action` field on `L2ContextOutput`, deliberately.** The shipped version selected samples by *warning* while the prompt asked L2 which *axis* to probe, and the warning inventory behind it was later deleted — leaving a set that only fills on backend degradation. On a healthy run it is empty, so the round scored every candidate 0/0 and persisted `accuracy: 0.0`, indistinguishable downstream from a genuinely terrible candidate, while still paying its optimizer calls and consuming a round. `l2_targets_l1_surface` even counted the choice as conformant — L2 scored 100% precisely by picking the action that measured nothing.

The lever was removed rather than guarded, because a guard leaves a no-op action sitting on L2's menu. **Re-running warned queries is degradation triage, not a search action**; conflating the two is what put an empty-set path in the candidate scorer. Build the real thing as defined above: pick the target from measurement (a peaked axis, a recurring failure cluster), widen `n_variants` for that round only, and select samples by *relevance to the hypothesis* — never by a set that is empty whenever the run is healthy.

**The framing is frozen — L2 does not write `task_context`.** The five framing fields
(`domain`, `pipeline_purpose`, `data_characteristics`, `optimization_goals`, `key_challenges`)
are operator-authored evidence about the task, and the lock is structural rather than a
convention: `TaskDecomposition.merge` (`domain/search_point.py`) refuses them and the L2 wire schema has no field for
them. When L2 could write them it replaced rather than refined (0.16 mean token overlap with
what it displaced), produced no detectable accuracy effect, and the render then clipped each
field, amputating the operator's tail. A round's findings reach L1 through `critique`,
`axis_memory` and `mutation_memory` instead — derived from measurement rather than
paraphrased from the previous prompt. Only
`upstream_context` / `downstream_context` stay mutable (L1's `task_context_override`): those
splice into the TARGET prompt, so a candidate carrying them is scored.

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal refinement — framed as a remedial nudge rather than a strategic shift.

**The optimizer never searches the model.** Which model a node runs is owned by [`../../../datasets/CLAUDE.md`](../../../datasets/CLAUDE.md) § Sole route; what this layer must do is never treat it as an axis. `PARAM_FORBIDDEN_KEYS = {"model","provider"}` (`domain/search_point.py`) are an INVARIANT, not a toggle: `PipelineSchema.node_param_keys()` (`domain/pipeline_schema.py`) always drops them (so the param catalogue + `build_l1_response_schema` never declare them — the LLM can't emit a key the schema omits). `validate_overrides` is the lone deterministic backstop for a provider that leaks the key past its own schema (`ValidationFailure(reason="forbidden_axis")`, synthetic-0, healed via Wound 1). No soft behavior check, no L1Stats heal counters — the lock is structural, not policed per round. A human may steer the model directly on a fork via the seed overlay, never an optimizer axis. Steering to a model the origin sanctioned (`CampaignConfig.allowed_models`) is a clean fork; steering OUTSIDE it (`overlay_sets_model_outside_allowed`; empty allow-list = nothing sanctioned = restrictive default) is a cap-gated (`campaign.babysit`) act that taints the branch babysat ([ADR-0005](../../../docs/adr/0005-delegated-principals-and-capability-scoping.md) §4). Either way the done C0 is inherited, not re-measured (`try_inherit_fork_origin` accepts a model/provider-only overlay and carries the parent's per-sample rows so round 0 is a faithful copy — the origin gate sees a real origin, not an empty shell).

The L2-layer may **terminate the loop** — via the escalation rules on goal reached (composite ≥ goal, sustained one round) or infinite stall (no improvement reachable through framing refinements), or by emitting `terminate_proposal` when the failure is unrecoverable through any framing move (the layer-control channel below).

The L2-layer escalating to L3 is **rare** — only when the failure mode is outside the framing surface: context-shape mismatch, scoring-set drift, verbatim-repeat refinements that L2 cannot resolve. Default: the L2-layer keeps refining the framing for L1.

## L3-layer — l3_plan

Fires only on L2-layer stall (L2 patience exceeded). Receives the evidence panels plus `l2_summary` (prior fires + their measured lift) and the runtime-failure trail. `l3_plan` produces:

- a **strategic replan** — rewrites the framing surface, escalation policy, or which axes are in scope; the cycle continues under a new plan rather than a new variant. Channel: written to `OptSearchPoint.plan` (persistent) and read by **every** prompt via the `plan` signal — the strategic frame inside which both L2 (steering L1's attention) and L1 (generating candidates) operate.

The L3-layer also **heals the L2-layer** on validator outcomes:

- L1 layout HARD-validator failures (mandatory placeholder missing, unknown name, dup within slot), or
- repeated cross-field issues that the framing refinement surface can't resolve.

These are signs that `l2_context` is thrashing within the current plan rather than refining across the plan-space — the L3-layer rewrites the policy, not just the next framing.

The L3-layer may **terminate the loop** on the same cases as the L2-layer (escalation-rule goal/stall, or an emitted `terminate_proposal`). If the L3-layer fires repeatedly inside one cycle, that is the loop's signal that the plan-space itself is exhausted — `l3_plan` should terminate rather than replan again.

L3-layer firing is **rarer still** than L2 — a fire signals the cycle's plan was wrong, not that one variant missed. Default: the L3-layer stays idle while the L2-layer carries the load.

### L2/L3 layer-control channel — fork_proposal + terminate_proposal

Beyond steering L1's attention / rewriting `plan`, L2 and L3 have **exactly two** LLM-emitted control outputs on their schema — `fork_proposal` (rewind) and `terminate_proposal` (stop). This is the complete layer-control vocabulary; nothing else back-doors a cycle exit. Both ride the same `_run_transition` post-apply seam (the layer's normal output is adopted and the exit-phase event emitted first, then the control output fires), and each is gated by an `OptimizationConfig` capability bit whose injection renders empty when off — so an ablation run is bit-for-bit identical on prompt text. Terminate outranks fork when both are set (stopping is more final than a rewind).

**`fork_proposal`** carries a reason and `unlock_schema_field_rename` — **no round offset, because the layer decides WHETHER to rewind and UCB decides WHERE**. The current cycle finalizes normally on `StopReason.REBASED`, then the runner mints the fork at the UCB-picked round, retargets the active pointer and re-enters the loop there, capped per CLI invocation. L2 emits when steering L1's attention cannot recover; L3 when its replan space is exhausted and a deferred ancestor looks materially more promising. `resume --rewind N "reason"` is the operator's equivalent gesture.

`unlock_schema_field_rename` is the layer's **only** search-policy request and can ride nothing but this rewind: `schema_field_rename` is `policy` + `Estimand.SEARCH`, so unlocking it invalidates comparability and must mint a sibling rather than mutate the running cycle — the same reason the operator's fork-at-offset-0 workflow exists. **It is a bool, never the `ConfigOverrides` object**; handed the whole delta, a layer could move its own spend ceiling. Its prompt clause renders only where a node *declares* the param and the lock is still closed, since teaching a lever the campaign cannot pull is the phantom-panel defect one layer up.

**`terminate_proposal`** stops the cycle outright on the existing HALTED-class `StopReason.ABORT` — no new stop reason, no fork. **A REASON is what makes it a decision**: the field is optional, so a model can fill it with `""`, and presence alone once ended a cycle whose fitness was still climbing. A blank one is a volunteered field — ignored, exactly as one arriving with the capability off is — and both the honored stop and the ignored blank land on the operator's warnings channel (`layer_terminated_cycle` / `layer_terminate_blank`), because a halt whose reason lives only in a log line is a halt nobody can act on. Its canonical user is an **evidence-starved** node: an enricher failing across the round because its backend quota is exhausted. That grade *routes* L2 in via the `l1_evidence_starved` rule — **a weak preemptor that brings L2 to diagnose and never stops anything itself** — and L2 judges recoverability, which a deterministic rule cannot and the LLM tier can. So stop authority lives here rather than in a backend-coupled tripwire, and what the operator acts on is the `suggested_action` banner carrying the verbatim connector reason.

**The layer decides WHETHER to rewind; UCB decides WHERE.** `fork_proposal` carries no round: the target is selected by `application/mask/backprop.py::select_rewind_round` — UCB1 over the lineage tree, whose nodes carry each round's Rasch ability backpropagated to its ancestors. With expansion (a round) and simulation (the deterministic eval pass) already in place, that closes all four MCTS phases: this is AlphaZero-shaped MCTS over the lineage, not greedy descent plus an escape hatch. A layer never had the evidence to name a round — no panel enumerated the ancestors and their fitness — so asking it to was a phantom citation on the loop's most expensive decision. See [`../../../docs/research/related-work.md#comparison-to-mcts`](../../../docs/research/related-work.md#comparison-to-mcts).

## Editing a renderer's PROSE is a measurement change

`dispatch/injections/` composes most of every optimizer prompt, so rewording a directive changes what every inner cycle is handed — and on L4 that is measurement identity. `injection_source_digest` puts this package's source in `_identity_config`, AST-normalized: a comment, docstring or reflow costs nothing; a panel's prose, its `char_cap` or its render condition voids the banked origins. Expect the re-measure; the bug is the reverse.

## A validator either REJECTS or SCORES — never both

`validators/` carries two postures and the split is the point: `*_strict` / `*_output` / `l1_invariants` reject, so a failure routes back up as a `ValidationFailure` and the layer heals; `*_behavior` only scores conformance into `review.md` and the round file, and never blocks a candidate. Within the reject posture, `l1_strict.py` checks a proposal against the pipeline SCHEMA while `l1_invariants.py` compares proposals against each other and against history (no-op / duplicate / repeat) and needs no schema at all. **Emitting the wire schema is neither posture** — that is `dispatch/l1_wire_schema.py`, which composes a prompt surface; it lived here for a while and read as a third kind of validator. The SCORING posture's vocabulary — `CheckResult`, `ValidatorContext`, `CheckFn` — is owned by `validators/behavior_base.py` and by neither layer that speaks it: while it sat in `l1_behavior.py`, `l2_behavior` imported both types out of L1 and then re-declared `CheckFn` verbatim beside them, so an L2 check could not be discussed without reaching through L1. Model/provider locking belongs to neither — it is structural, because those param keys are never emitted for the LLM to set in the first place.

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.

## Add no 4th LayerStrategy — L4 is a recursion

**There is no `l4_*.py` in this package and there will not be one.** The ban is derived, not arbitrary, which is why naming a file `l4_recursion.py` breaks it just as surely: L4 is the same PromptPotter applied to itself via the `promptpotter` connector — an outer cycle whose *backend* is an inner cycle, mutating the inner's optimizer prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) as `pipeline_params`. A driver here would mean the ladder grew a rung; it did not. The ladder is closed at L1 / L2 / L3.

Conceptually L2 / L3 / L4 are one family — each mutates a slower-changing surface of the level below (L2 → L1's attention: `l1_layout` / `l1_overrides`; L3 → `plan`; L4 → optimizer prompt templates). Structurally L2 and L3 live here as escalation strategies while L4 lives at the connector seam and the dataset; which package holds which half is [`../../CLAUDE.md`](../../CLAUDE.md) § Where L4 lives. Spec: [`../../../docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md).

## checkin — the fifth optimizer node (decomposition + origin resolution)

`checkin` is a registered optimizer node (`OPTIMIZER_RESPONSE_MODELS`, `dispatch/schemas.py`) but **not a loop layer**: it runs *around* the loop and skips the `build_bundle → DispatchHub.fill` injection path, calling `run_optimizer_node → compile_prompt → llm_call` directly. It is **not** thereby a "non-ledger" call — both modes bind the seeded campaign's cycle ledger via `task_context.py::checkin_call_context` and wrap in `observed_node`, so tokens, cost and audit record land like any other. They did not, once; a `checkin` reaching the LLM unbilled and untraced is the bug that rule exists to prevent.

**One node, two modes, one output schema (`CheckinOutput`).** Don't add a second decomposition/resolution node — the existing one covers both, and they share one shape:

- **Task decomposition** — CLI `new`. Raw `task_description` → the six Layer-1 prompt strings (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`) + the `task_context` sub-object. Driver: `task_context.py::decompose_prompt_fields`, called from `presentation/cli/commands/new.py`.
- **Origin resolution** — web ingest check-in. A draft-campaign origin → `assessment` + `findings` (evidence-cited proposed field values) + `next_action` + `recap`. Driver: `../datasets/origin_resolve.py::resolve_origin_turn` — explicitly reuses this node per the operator's steer rather than a parallel `origin_resolve` node.

Both modes return the **same** `CheckinOutput`; each populates its own half. The six decomposition fields are produced in *both* modes, and both drivers capture them: task-decomposition feeds them straight into the prompt, and `origin_resolve.py::_apply_findings` lifts the non-empty decomposition strings onto `draft.origin_prompt_fields` (CONFIRMED provenance) alongside its `findings`/`recap` consumption. So an origin turn returns the resolved origin *and* a seeded starting prompt the operator then edits before mint.
