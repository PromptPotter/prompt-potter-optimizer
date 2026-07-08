# application/optimization/ — L1 / L2 / L3 agent contracts

This file is the **agent contract** for the three-layer loop — what each layer reads, writes, and decides; when each escalates; what triggers healing. The orchestration shape (Cycle, dispatch, escalation rules) lives in [`../CLAUDE.md`](../CLAUDE.md). Channels, signal routing, and the wound-signal graph (two rendered signals over the two-axis self-healing model) live in [`../../../docs/developer/dispatch-hub.md`](../../../docs/developer/dispatch-hub.md) — that's the canonical info-flow doc.

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

**Origin = conservative floor.** The dataset's per-node overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`) must start at the floor of each tunable, not the centre — `reasoning_effort: "low"`, low `temperature`, minimal `thinking_budget`, no expensive system-prompt scaffolding. L1 expands upward from there when sibling-yield or stall evidence supports it. Starting with expanded-thinking / high-temperature defaults burns budget on round 0 and steals the headroom L1 is supposed to discover. Canonical per-dataset starting points + cost/stability observations live in [`../../../docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md).

The cycle has boundary stop conditions — the calendar cap `max_rounds`, or (opt-in) its measurement-driven twin `OptimizationConfig.lives` ("hearts": +1 per improving round / −1 per stall, banked, stop at 0 → `LIVES_EXHAUSTED`; banks the same `improved` verdict the stall counter reads, on `EscalationFSM`). On top of those, **the L2-layer and L3-layer may end the cycle themselves** — either through the deterministic escalation rules (goal reached / infinite stall) or by emitting a `terminate_proposal` when the fault is unrecoverable through any framing or plan move (the LLM-emitted stop; see the layer-control channel below).

## L1-layer — l1_generate

The parent searchpoint was selected for measured reasons. `l1_generate` mutates a pipeline-configuration field only with cause:

- stall-driven escalation,
- critique / axis-memory evidence on the chosen axis, or
- a refined `task_context` framing from L2.

No data justifying a choice ⇒ do not gamble. Random exploration is reserved for explicit stall.

`l1_generate`'s evidence base lives on its surface — every citable panel is a same-named DispatchHub injection (no phantom panels; a citable name that never renders invites fabricated citations):

- `critique` — the distiller's compression of the round's failures: `priority_fix` + `failure_highlights` (quoting SAMPLE TRANSCRIPTS) + `suggested_axes`.
- `escalation_panel` — stall depth + `exploration_budget ∈ {tight, normal, wide}`.
- `axis_memory` — cross-round AxisIndex digest (`cycle.axes.digest()`); per-axis effect_size vs noise floor.

If a panel field speaks against a mutation, `l1_generate` does not propose it.

Each emitted variant declares an `evidence_grounding: {field, citation}` naming the panel entry that justifies the mutation. `field ∈ EVIDENCE_GROUNDING_FIELDS`; `stall_exploration` is the escape hatch and is only valid when `escalation_panel.exploration_budget ∈ {normal, wide}`. Variants without a real citation fail the `evidence_grounding_present` behavior check (`validators/l1_behavior.py`) — surfaced in `review.md` and `round_NNNN.json`. The healing rule that converts this signal into an L2 `task_context` nudge is backlog — `docs/specs/roadmap.md` § Plus-backlog.

**Field order is load-bearing — never reorder `L1Variant` alone.** `evidence_grounding` generates second, above `changes_description` *and* the `*_override` slots (`dispatch/schemas.py`), because fields generate in schema order: emitted after the mutation, a citation can only rationalize it. Three surfaces state that order and move together — the Pydantic model (the SoT), `l1_generate`'s `answer_format` prose, and the regenerated `datasets/_optimizer/pipeline.json::resolved_schemas` (`scripts/build_optimizer_schemas.py`). A schema that disagrees with its own prose teaches twice, contradictorily. See `docs/concepts/structured-output.md` § coordinates.

Channel: `task_context` (L2-refined task framing) and `plan` (L3-set strategy) arrive on `OptSearchPoint` and surface alongside the panels — `l1_generate` is fan-in, reading both layers' outputs in the same round. Composed by `DispatchHub.fill` walking `opt_sp.memory.l1_layout` (l1_generate's per-node layout from `NODE_LAYOUTS`) over the `INJECTIONS` registry (`dispatch/hub/injections/registry.py`).

**Reviewing an L1 round trace.** Load
[`../../../docs/developer/l1-candidate-analysis-checklist.md`](../../../docs/developer/l1-candidate-analysis-checklist.md)
before reporting findings on any operator-pasted round dump or
meta-campaign cycle review. The checklist enumerates the eight checks
that historically slipped past — evidence-availability in the rendered
input, re-proposal of known-failing configs, PEAKED-axis violations,
±50% envelope, param-axis overuse, intra-round paraphrase, citation
hallucinations, output-format integrity — and notes which are enforced
by validators today vs which are pure analysis responsibility.

## L2-layer — l2_context

Fires on L1-layer stall (default), yield drought (escalation rule `l2_axis_yield_drought` — fires when L1 has stalled at least one round AND AxisIndex shows zero axes with effect above the noise floor), or **evidence-starvation** (`l1_evidence_starved` — a node failed across ~all of a round's samples). Post-round transitions are decided by `decide_escalation(EscalationInputs)` over `DEFAULT_ESCALATION_RULES` (`escalation/decide.py`); the rule set is the policy and replaces the prior FSM.

**The model: self-healing with a HITL escape hatch.** Two tracks, decided by the round's evidence. (1) A *healthy, analysable* round → L1 critique does its job: analyse the per-sample misses, propose mutations. Critique is the prompt-improvement surface and stays concentrated — it is **not** the issue-router and is not loaded with backend-fault diagnostics. (2) *Accumulated evidence of a systemic fault* (evidence-starvation: a node failing across ~all samples — `evidence_starved_node` ≥ `EVIDENCE_STARVED_RATE`) → a **weak preemptor** routes to L2 (bypassing l1_patience so the loop doesn't grind more dead rounds). L2 reads the context and judges: fixable by a framing move → self-heal (`task_context`); unfixable by any prompt move (Brave quota / backend down) → `terminate_proposal`, the HITL exit that halts carrying the human-action request (the operator banner — `DegradationHealth.suggested_action` — supplies the verbatim connector reason). Deterministic rules stay *weak* by design: they route, they never diagnose or stop. The diagnose-and-stop authority lives in the LLM tier (R-48).

Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- a refined `task_context` — the persistent task-framing dict that every layer (L1, L1_CRITIQUE, L2, L3) reads next round, and
- optional `l1_layout` edits + optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The refinement is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative refinements ("maybe try X") are out of contract. `task_context` is persistent, accumulative: each fire merges deltas onto the existing dict; full rewrites are rare. A proposed update that lands no semantic delta (no-op merge or ≥0.5-Jaccard paraphrase) is flagged as `l2_task_context_stale_repeat` — a soft-reject (prior framing kept; a sole breach does not force-trigger L3).

Channel: written to `OptSearchPoint.task_context`; read by every prompt via the `task_context` signal. Persistent across L2 fires until L2 next refines (or L3 replans).

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal refinement — framed as a remedial nudge rather than a strategic shift.

**Model is dataset-owned; optimizability is ONE bit.** The dataset owns its task model in `pipeline.json::nodes.{node}.config.model` (the benchmarks pin theirs; a fresh drop inherits the connector's `default_node_config` seed, written into its own committed file). Whether the *optimizer* may search the model is the single `OptimizationConfig.forbidden_axes_strict` bit (default on, = locked), applied at ONE surface: `PipelineSchema.node_param_keys(forbidden_strict)` drops `PARAM_FORBIDDEN_KEYS = {"model","provider"}` when locked (so the param catalogue + `build_l1_output_schema` never declare them — the LLM can't emit a key the schema omits) and synthesizes a `model` axis from `available_models` when unlocked. `validate_overrides` is the lone deterministic backstop for a provider that leaks the key past its own schema (`ValidationFailure(reason="forbidden_axis")`, synthetic-0, healed via Wound 1). No soft behavior check, no L1Stats heal counters — the lock is structural, not policed per round.

The L2-layer may **terminate the loop** — via the escalation rules on goal reached (composite ≥ goal, sustained one round) or infinite stall (no improvement reachable through framing refinements), or by emitting `terminate_proposal` when the failure is unrecoverable through any framing move (the layer-control channel below).

The L2-layer escalating to L3 is **rare** — only when the failure mode is outside the framing surface: context-shape mismatch, scoring-set drift, verbatim-repeat refinements that L2 cannot resolve. Default: the L2-layer keeps refining the framing for L1.

## L3-layer — l3_plan

Fires only on L2-layer stall (L2 patience exceeded). Receives the evidence panels plus `l2_summary` (prior fires + their measured lift) and the runtime-failure trail. `l3_plan` produces:

- a **strategic replan** — rewrites the framing surface, escalation policy, or which axes are in scope; the cycle continues under a new plan rather than a new variant. Channel: written to `OptSearchPoint.plan` (persistent) and read by **every** prompt via the `plan` signal — the strategic frame inside which both L2 (refining task_context) and L1 (generating candidates) operate.

The L3-layer also **heals the L2-layer** on validator outcomes:

- `l2_task_context_stale_repeat` or `l2_situational_example_dangling_trigger` when combined with a non-soft breach (each is a soft-reject — `_L2_SOFT_REJECT_VALIDATOR_IDS`; a breach set that is *all* soft-reject skips the L3 fire, since the output was already discarded),
- L1 layout HARD-validator failures (mandatory placeholder missing, unknown name, dup within slot), or
- repeated cross-field issues that the framing refinement surface can't resolve.

These are signs that `l2_context` is thrashing within the current plan rather than refining across the plan-space — the L3-layer rewrites the policy, not just the next framing.

The L3-layer may **terminate the loop** on the same cases as the L2-layer (escalation-rule goal/stall, or an emitted `terminate_proposal`). If the L3-layer fires repeatedly inside one cycle, that is the loop's signal that the plan-space itself is exhausted — `l3_plan` should terminate rather than replan again.

L3-layer firing is **rarer still** than L2 — a fire signals the cycle's plan was wrong, not that one variant missed. Default: the L3-layer stays idle while the L2-layer carries the load.

### L2/L3 layer-control channel — fork_proposal + terminate_proposal

Beyond refining `task_context` / rewriting `plan`, L2 and L3 have **exactly two** LLM-emitted control outputs on their schema — `fork_proposal` (rewind) and `terminate_proposal` (stop). This is the complete layer-control vocabulary; nothing else back-doors a cycle exit. Both ride the same `_run_transition` post-apply seam (the layer's normal output is adopted and the exit-phase event emitted first, then the control output fires), and each is gated by an `OptimizationConfig` capability bit whose injection renders empty when off — so an ablation run is bit-for-bit identical on prompt text. Terminate outranks fork when both are set (stopping is more final than a rewind).

**`fork_proposal: ForkProposal | None`** (`{round_offset: int, reason: str, unlock_schema_field_rename: bool}`; `round_offset` MUST be negative — a rewind). `_run_transition` stashes a `RebaseRequest` on `cycle.rebase_request` and raises `StopLoop(StopReason.REBASED)`; the current cycle finalizes normally, then `runner.entry` resolves the request post-finalize — `_mint_fork(L{2,3}_REBASE, fork_from_round=current + round_offset)` retargets the active pointer, rebuilds observers, and re-enters the loop on the new fork (capped at `MAX_AUTO_REBASES = 10` per CLI invocation; over-cap falls through with the last cycle's stop_reason). L2 emits when refining `task_context` cannot recover; L3 when its replan space is exhausted and a deferred ancestor looks materially more promising per the panels. Operator-side `resume --rewind N "reason"` is the equivalent CLI gesture (an `OPERATOR_REWIND` sibling at round N, parent preserved). Gated by `rebase_capability` (`dispatch/hub/injections/layer_state.py::_r_rebase_capability`).

`unlock_schema_field_rename` is the layer's **only** search-policy request, and it can only ride this rewind. `schema_field_rename` is classified `policy` + `Estimand.SEARCH`, so unlocking an axis invalidates comparability and must mint a sibling rather than mutate the running cycle — the same reason the operator's "behaviour-knob change → fork-at-offset-0" workflow exists. `_stash_rebase_request` widens the bool into the fork's `ConfigOverrides`, which the runner both applies to the fork's config snapshot and persists in its `CycleSeed` (so a later `resume` re-reads the unlock). It is a bool, never the `ConfigOverrides` object: handed the whole delta a layer could move its own spend ceiling. The prompt clause rides `_REBASE_CAPABILITY_TEXT` and renders only where a node **declares** `SCHEMA_RENAME_PARAM` and the lock is still closed — teaching a lever the campaign cannot pull is the phantom-panel defect one layer up.

**`terminate_proposal: TerminateProposal | None`** (`{reason: str}`). The layer judges the fault unrecoverable through any framing or plan move and stops the cycle outright: `_run_transition` raises `StopLoop(StopReason.ABORT)` (the existing HALTED-class reason, reused — no new stop reason, no fork) and the cycle finalizes on the current `cycle_id`. The canonical user is an **evidence-starved** node — an enricher failing across the round because its backend quota / rate-limit is exhausted (`DegradationHealth.reasons` carries `evidence_starved`). That grade now *routes* L2 in via the `l1_evidence_starved` escalation rule (a weak preemptor — it brings L2 to diagnose, it never stops), and the critique it reads names the dead node via the `evidence_health` panel. L2 then judges "is this recoverable?" — a deterministic rule can't, the LLM tier can — and on no → `terminate_proposal`. So the stop authority sits here, not in a backend-coupled tripwire (the deterministic detector routes but never stops; R-48). The human-action request the operator acts on is the `suggested_action` banner, which carries the verbatim connector reason (e.g. the real Brave 429). Gated by `terminate_capability` (`_r_terminate_capability`).

**Forward direction (M13+).** Backpropagation (rolling round outcomes up the lineage tree as node-stats) and UCB-style ancestor-selection make this AlphaZero-shaped MCTS over the lineage rather than greedy descent + escape hatches. See [`../../../docs/research/related-work.md#comparison-to-mcts`](../../../docs/research/related-work.md#comparison-to-mcts), [`../../../docs/specs/roadmap.md`](../../../docs/specs/roadmap.md) § Plus-backlog.

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.

## L4 — recursion, not a new layer

L4 is **not** a 4th `LayerStrategy` driver inside this package. There is no `l4_*.py` and there will not be one. L4 is the same PromptPotter applied to itself via the `promptpotter` connector: an outer cycle whose backend is an inner cycle, mutating the inner's meta-prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) as `pipeline_params`.

Conceptually L2 / L3 / L4 are the same family — each mutates a slower-changing surface of the level below (L2 → `task_context`; L3 → `plan`; L4 → meta-prompt templates). Structurally L2 and L3 live here as escalation strategies; L4 lives at the connector seam (`../../connectors/promptpotter.py`) and at the dataset (`datasets/promptpotter-self/`). Spec: [`../../../docs/specs/roadmap.md`](../../../docs/specs/roadmap.md) § Connectors + L4 inner-cycle execution. Concept: [`../../../docs/concepts/optimizer-of-the-optimizer.md`](../../../docs/concepts/optimizer-of-the-optimizer.md).

## checkin — the fifth optimizer node (decomposition + origin resolution)

There are **five** registered optimizer nodes, not four (`OPTIMIZER_RESPONSE_MODELS`, `dispatch/schemas.py`). The fifth is `checkin` — **renamed from `restructure`** (commit `269e9b87`); searching the tree for "restructure" finds nothing because the concept lives under this name now. It is **not a loop layer**: it runs *around* the loop, not inside it, and does **not** go through the `build_bundle → DispatchHub.fill_l1|fill_fixed` injection path. It's a non-ledger call straight through `run_optimizer_node → compile_prompt → llm_call` (`dispatch/llm_call/call.py:427`).

**One node, two modes, one output schema (`CheckinOutput`).** Don't add a second decomposition/resolution node — the existing node already covers both, and they share one shape:

- **Task decomposition** — CLI `new`. Raw `task_description` → the six Layer-1 prompt strings (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`) + the `task_context` sub-object. Driver: `task_context.py::decompose_prompt_fields` (via `load_or_build_task_context`).
- **Origin resolution** — web ingest check-in. A draft-campaign origin → `assessment` + `findings` (evidence-cited proposed field values) + `next_action` + `recap`. Driver: `../datasets/origin_resolve.py::resolve_origin_turn` — explicitly reuses this node per the operator's steer rather than a parallel `origin_resolve` node.

Both modes return the **same** `CheckinOutput`; each populates its own half. The six decomposition fields are produced in *both* modes, and both drivers capture them: task-decomposition feeds them straight into the prompt, and `origin_resolve.py::_apply_findings` lifts the non-empty decomposition strings onto `draft.origin_prompt_fields` (CONFIRMED provenance) alongside its `findings`/`recap` consumption. So an origin turn returns the resolved origin *and* a seeded starting prompt the operator then edits before mint.
