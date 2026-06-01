# application/optimization/ — L1 / L2 / L3 agent contracts

This file is the **agent contract** for the three-layer loop — what each layer reads, writes, and decides; when each escalates; what triggers healing. The orchestration shape (Cycle, dispatch, escalation rules) lives in [`../CLAUDE.md`](../CLAUDE.md). Channels, signal routing, and the four-wound graph live in [`../../../docs/developer/dispatch-hub.md`](../../../docs/developer/dispatch-hub.md) — that's the canonical info-flow doc.

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

**Origin = conservative floor.** The dataset's per-node overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`) must start at the floor of each tunable, not the centre — `reasoning_effort: "low"`, low `temperature`, minimal `thinking_budget`, no expensive system-prompt scaffolding. L1 expands upward from there when sibling-yield or stall evidence supports it. Starting with expanded-thinking / high-temperature defaults burns budget on round 0 and steals the headroom L1 is supposed to discover. Canonical per-dataset starting points + cost/stability observations live in [`../../../docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md).

The cycle has hardcoded stop conditions (e.g. `max_rounds`). On top of those, **the L2-layer and L3-layer may decide on their own to terminate the loop** — see below.

## L1-layer — l1_generate

The parent searchpoint was selected for measured reasons. `l1_generate` mutates a pipeline-configuration field only with cause:

- stall-driven escalation,
- sibling-yield evidence on the chosen axis, or
- a refined `task_context` framing from L2.

No data justifying a choice ⇒ do not gamble. Random exploration is reserved for explicit stall.

`l1_generate`'s evidence base lives on its surface:

- `parent_panel` — parent composite + per-sample tally + delta to beat.
- `sibling_yield` — prior round per-axis yield: `axis | n_tried | n_beat_parent | mean_delta`.
- `escalation_panel` — `stall_rounds`, `last_winner_axis`, `params_unlocked`, `exploration_budget ∈ {tight, normal, wide}`.
- `axis_memory` — cross-round AxisIndex digest (`cycle.axes.digest()`); per-axis effect_size vs noise floor.

If a panel field speaks against a mutation, `l1_generate` does not propose it.

Each emitted variant declares an `evidence_grounding: {field, citation}` naming the panel entry that justifies the mutation. `field ∈ EVIDENCE_GROUNDING_FIELDS`; `stall_exploration` is the escape hatch and is only valid when `escalation_panel.exploration_budget ∈ {normal, wide}`. Variants without a real citation fail the `evidence_grounding_present` behavior check (`validators/l1_behavior.py`) — surfaced in `review.md` and `round_NNNN.json`. The Track 4 healing rule that converts this signal into an L2 `task_context` nudge lives behind `docs/specs/m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface`.

Channel: `task_context` (L2-refined task framing) and `plan` (L3-set strategy) arrive on `OptSearchPoint` and surface alongside the panels — `l1_generate` is fan-in, reading both layers' outputs in the same round. Composed by `DispatchHub.fill_l1` walking `opt_sp.memory.l1_layout` over the `INJECTIONS` registry (`dispatch/hub/injections/registry.py`).

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

Fires on L1-layer stall (default) or yield drought (escalation rule `l2_axis_yield_drought` — fires when L1 has stalled at least one round AND AxisIndex shows zero axes with effect above the noise floor). Post-round transitions are decided by `decide_escalation(EscalationInputs)` over `DEFAULT_ESCALATION_RULES` (`escalation/decide.py`); the rule set is the policy and replaces the prior FSM.

Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- a refined `task_context` — the persistent task-framing dict that every layer (L1, L1_CRITIQUE, L2, L3) reads next round, and
- optional `l1_layout` edits + optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The refinement is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative refinements ("maybe try X") are out of contract. `task_context` is persistent, accumulative: each fire merges deltas onto the existing dict; full rewrites are rare. A proposed update that merges to a no-op against the prior framing is flagged as `l2_task_context_verbatim_repeat` → L3 heal trigger.

Channel: written to `OptSearchPoint.task_context`; read by every prompt via the `task_context` signal. Persistent across L2 fires until L2 next refines (or L3 replans).

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal refinement — framed as a remedial nudge rather than a strategic shift.

**Operator-locked axes** (`PARAM_FORBIDDEN_KEYS = {"model", "provider"}` at `domain/search_point.py`) are enforced by the strict validator (`OptimizationConfig.forbidden_axes_strict`, default on) and healed via Wound 1. The same const drives the AxisIndex digest scrub (`application/intelligence/indexes/axis.py`) and the runtime-failure config-match filter (`dispatch/hub/injections/wounds.py`) so L2/L3 prompts never surface the locked axes as candidate mutations. L2 reads the rejected attempt through `dispatch_hub._r_validation_failures` and writes a remedial `task_context` nudge next round. L1's meta-prompt does NOT enumerate the lock — the validator + heal chain is the contract. The behaviour check `forbidden_axes_honored` counts attempts for the audit trail; the `forbidden_axis_attempts` + `forbidden_axis_healed` fields on L1Stats surface the heal trail in `review.md`.

The L2-layer may **terminate the loop** on:

- goal reached (composite ≥ goal, sustained one round), or
- infinite stall (no improvement reachable through framing refinements).

The L2-layer escalating to L3 is **rare** — only when the failure mode is outside the framing surface: context-shape mismatch, scoring-set drift, verbatim-repeat refinements that L2 cannot resolve. Default: the L2-layer keeps refining the framing for L1.

## L3-layer — l3_plan

Fires only on L2-layer stall (L2 patience exceeded). Receives the evidence panels plus `l2_summary` (prior fires + their measured lift) and the runtime-failure trail. `l3_plan` produces:

- a **strategic replan** — rewrites the framing surface, escalation policy, or which axes are in scope; the cycle continues under a new plan rather than a new variant. Channel: written to `OptSearchPoint.plan` (persistent) and read by **every** prompt via the `plan` signal — the strategic frame inside which both L2 (refining task_context) and L1 (generating candidates) operate.

The L3-layer also **heals the L2-layer** on validator outcomes:

- `l2_task_context_verbatim_repeat` (proposed framing merged to a no-op),
- L1 layout HARD-validator failures (mandatory placeholder missing, unknown name, dup within slot), or
- repeated cross-field issues that the framing refinement surface can't resolve.

These are signs that `l2_context` is thrashing within the current plan rather than refining across the plan-space — the L3-layer rewrites the policy, not just the next framing.

The L3-layer may **terminate the loop** on the same two cases as the L2-layer (goal reached / infinite stall). If the L3-layer fires repeatedly inside one cycle, that is the loop's signal that the plan-space itself is exhausted — `l3_plan` should terminate rather than replan again.

L3-layer firing is **rarer still** than L2 — a fire signals the cycle's plan was wrong, not that one variant missed. Default: the L3-layer stays idle while the L2-layer carries the load.

### L2/L3 fork-proposal channel (automatic rebase) + forward direction

**Shipped (automatic rebase).** Both L2 and L3 may emit `fork_proposal: ForkProposal | None` on their output schema (`{round_offset: int, reason: str}`; `round_offset` MUST be negative — a rewind). After `transition.apply(...)` and the exit-phase event, `_run_transition` stashes a `RebaseRequest` on `cycle.rebase_request` and raises `StopLoop(StopReason.REBASED)`. The current cycle finalizes normally on the old `session.state.cycle_id`; `runner.entry` then resolves the request post-finalize: calls `_mint_fork(L{2,3}_REBASE, fork_from_round=current + round_offset)` (which retargets the active pointer), rebuilds observers around the new ledger, and re-enters the optimize loop on the new fork. Auto-continuation is capped at `MAX_AUTO_REBASES = 10` per CLI invocation; over-cap rebases fall through with the last cycle's stop_reason.

L2 emits as a rare escape hatch when refining `task_context` cannot recover; L3 emits when its replan space is exhausted and a specific deferred ancestor looks materially more promising per the panels. Operator-side `resume --rewind N "reason"` mints an `OPERATOR_REWIND` sibling at round N (parent preserved) and starts optimization on the fork — the equivalent CLI gesture.

The `rebase_capability` injection (`dispatch/hub/injections/layer_state.py::_r_rebase_capability`) renders the fork_proposal instruction into L2 + L3 prompts only when `OptimizationConfig.rebase_capability` is on. Off ⇒ empty render, no LLM emission, no rebase loop — bit-for-bit identical input distribution to a no-rebase ablation run.

**Forward direction (M13+).** Backpropagation (rolling round outcomes up the lineage tree as node-stats) and UCB-style ancestor-selection make this AlphaZero-shaped MCTS over the lineage rather than greedy descent + escape hatches. See [`../../../docs/research/related-work.md#comparison-to-mcts`](../../../docs/research/related-work.md#comparison-to-mcts), [`../../../docs/specs/roadmap.md#backlog-unscheduled`](../../../docs/specs/roadmap.md#backlog-unscheduled).

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.

## L4 — recursion, not a new layer

L4 is **not** a 4th `LayerStrategy` driver inside this package. There is no `l4_*.py` and there will not be one. L4 is the same PromptPotter applied to itself via the `promptpotter` connector: an outer cycle whose backend is an inner cycle, mutating the inner's meta-prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`) as `pipeline_params`.

Conceptually L2 / L3 / L4 are the same family — each mutates a slower-changing surface of the level below (L2 → `task_context`; L3 → `plan`; L4 → meta-prompt templates). Structurally L2 and L3 live here as escalation strategies; L4 lives at the connector seam (`../../connectors/promptpotter.py`) and at the dataset (`datasets/promptpotter-self/`). Spec: [`../../../docs/specs/m12-multi-connector.md#track-15--promptpotter-as-connector`](../../../docs/specs/m12-multi-connector.md). Concept: [`../../../docs/concepts/optimizer-of-the-optimizer.md`](../../../docs/concepts/optimizer-of-the-optimizer.md).

## checkin — the fifth optimizer node (decomposition + origin resolution)

There are **five** registered optimizer nodes, not four (`OPTIMIZER_RESPONSE_MODELS`, `dispatch/schemas.py`). The fifth is `checkin` — **renamed from `restructure`** (commit `269e9b87`); searching the tree for "restructure" finds nothing because the concept lives under this name now. It is **not a loop layer**: it runs *around* the loop, not inside it, and does **not** go through the `build_bundle → DispatchHub.fill_l1|fill_fixed` injection path. It's a non-ledger call straight through `run_optimizer_node → compile_prompt → llm_call` (`dispatch/llm_call/call.py:410`).

**One node, two modes, one output schema (`CheckinOutput`).** Don't add a second decomposition/resolution node — the existing node already covers both, and they share one shape:

- **Task decomposition** — CLI `new`. Raw `task_description` → the six Layer-1 prompt strings (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`) + the `task_context` sub-object. Disk-cached by content hash at `{base}/{backend_id}/checkin_cache.json`; a re-run on an unchanged task is free. Driver: `task_context.py::decompose_prompt_fields`.
- **Origin resolution** — web ingest check-in. A draft-campaign origin → `assessment` + `findings` (evidence-cited proposed field values) + `next_action` + `recap`. Driver: `../datasets/origin_resolve.py::resolve_origin_turn` — explicitly reuses this node per the operator's steer rather than a parallel `origin_resolve` node.

Both modes return the **same** `CheckinOutput`; each populates its own half. The six decomposition fields are produced in *both* modes, and both drivers capture them: task-decomposition feeds them straight into the prompt, and `origin_resolve.py::_apply_findings` lifts the non-empty decomposition strings onto `draft.starting_prompt` (CONFIRMED provenance) alongside its `findings`/`recap` consumption. So an origin turn returns the resolved origin *and* a seeded starting prompt the operator then edits before mint.
