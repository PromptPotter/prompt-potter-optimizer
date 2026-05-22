# promptpotter/ — agent contract for L1 / L2 / L3

This file covers the L1/L2/L3 **agent contracts** — what each layer reads, writes, and decides; when each escalates; what triggers healing. Channels, signal routing, and the four-wound graph live in [`docs/developer/dispatch-hub.md`](../docs/developer/dispatch-hub.md) — that's the canonical info-flow doc.

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

**Origin = conservative floor.** The dataset's per-node overlay (`datasets/{name}/pipeline.json::nodes.{name}.config`) must start at the floor of each tunable, not the centre — `reasoning_effort: "low"`, low `temperature`, minimal `thinking_budget`, no expensive system-prompt scaffolding. L1 expands upward from there when sibling-yield or stall evidence supports it. Starting with expanded-thinking / high-temperature defaults burns budget on round 0 and steals the headroom L1 is supposed to discover. Canonical per-dataset starting points + cost/stability observations live in [`docs/operations/dataset-reasoning-matrix.md`](../docs/operations/dataset-reasoning-matrix.md).

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

Each emitted variant declares an `evidence_grounding: {field, citation}` naming the panel entry that justifies the mutation. `field ∈ EVIDENCE_GROUNDING_FIELDS`; `stall_exploration` is the escape hatch and is only valid when `escalation_panel.exploration_budget ∈ {normal, wide}`. Variants without a real citation fail the `evidence_grounding_present` behavior check (`application/optimization/validators/l1_behavior.py`) — surfaced in `review.md` and `round_NNNN.json`. The Track 4 healing rule that converts this signal into an L2 `task_context` nudge lives behind `m10-prompt-iteration-framework.md#track-7--l2-self-diagnosis-surface`.

Channel: `task_context` (L2-refined task framing) and `plan` (L3-set strategy) arrive on `OptSearchPoint` and surface alongside the panels — `l1_generate` is fan-in, reading both layers' outputs in the same round. Composed by `DispatchHub.fill_l1` walking `opt_sp.l1_layout` over the `INJECTIONS` registry (`dispatch/hub/injections/registry.py`).

**Reviewing an L1 round trace.** Load
[`docs/developer/l1-candidate-analysis-checklist.md`](../docs/developer/l1-candidate-analysis-checklist.md)
before reporting findings on any operator-pasted round dump or
meta-campaign cycle review. The checklist enumerates the eight checks
that historically slipped past — evidence-availability in the rendered
input, re-proposal of known-failing configs, PEAKED-axis violations,
±50% envelope, param-axis overuse, intra-round paraphrase, citation
hallucinations, output-format integrity — and notes which are enforced
by validators today vs which are pure analysis responsibility.

## L2-layer — l2_context

Fires on L1-layer stall (default) or yield drought (escalation rule `l2_axis_yield_drought` — fires when L1 has stalled at least one round AND AxisIndex shows zero axes with effect above the noise floor). Post-round transitions are decided by `decide_escalation(EscalationInputs)` over `DEFAULT_ESCALATION_RULES` (`application/optimization/escalation/decide.py`); the rule set is the policy and replaces the prior FSM.

Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- a refined `task_context` — the persistent task-framing dict that every layer (L1, L1_CRITIQUE, L2, L3) reads next round, and
- optional `l1_layout` edits + optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The refinement is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative refinements ("maybe try X") are out of contract. `task_context` is persistent, accumulative: each fire merges deltas onto the existing dict; full rewrites are rare. A proposed update that merges to a no-op against the prior framing is flagged as `l2_task_context_verbatim_repeat` → L3 heal trigger.

Channel: written to `OptSearchPoint.task_context`; read by every prompt via the `task_context` signal. Persistent across L2 fires until L2 next refines (or L3 replans).

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal refinement — framed as a remedial nudge rather than a strategic shift.

**Operator-locked axes** (`PARAM_FORBIDDEN_KEYS = {"model", "provider"}` at `domain/search_point.py`) are enforced by the strict validator (`OptimizationConfig.forbidden_axes_strict`, default on) and healed via Wound 1. The same const drives the AxisIndex digest scrub (`application/intelligence/indexes/axis.py`) and the runtime-failure config-match filter (`application/optimization/dispatch/hub/injections/wounds.py`) so L2/L3 prompts never surface the locked axes as candidate mutations. L2 reads the rejected attempt through `dispatch_hub._r_validation_failures` and writes a remedial `task_context` nudge next round. L1's meta-prompt does NOT enumerate the lock — the validator + heal chain is the contract. The behaviour check `forbidden_axes_honored` counts attempts for the audit trail; the `forbidden_axis_attempts` + `forbidden_axis_healed` fields on L1Stats surface the heal trail in `review.md`.

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

### L3 fork-proposal channel (selection signal, observation-only) + forward direction

**Shipped (selection signal).** L3 may emit `fork_proposal: ForkProposal | None` alongside its `plan` rewrite. Schema: `{round_offset: int, reason: str}` — see `application/optimization/dispatch/schemas.py::ForkProposal`. The field rides the existing L3 output path: `_parse_l3()` reads it from `L3PlanOutput`, `_apply_l3()` does NOT mutate the cycle, `_l3_exit()` writes it into `round_NNNN.json::nodes[l3_plan].exit.fork_proposal`. **Observation-only contract**: the runner never automatically forks. The operator reads the proposal and manually runs `python -m promptpotter resume --from N` if the case is sound. L3's prompt instruction in `datasets/_optimizer/pipeline.json::resolved_prompts['l3_plan/1']` tells L3 to set this only when (a) the current subtree is exhausted (multiple L3 fires with no lift) AND (b) a specific deferred ancestor looks promising per the panels — not as a hedge.

**Forward direction (M13+).** This ships the **selection signal** half of the AlphaZero-shaped-MCTS arc. The remaining halves: **(a)** persist round outcomes as node-stats up the lineage tree (backpropagation), and **(b)** add a UCB-style ancestor-selection rule plus automatic-fork wire from L3's proposal into `inherit_from(parent, offset)`. With all three, PromptPotter becomes AlphaZero-shaped MCTS over the lineage — categorical capability unlocked: *recovery from dead-end branches*. See [`docs/research/related-work.md#comparison-to-mcts`](../docs/research/related-work.md#comparison-to-mcts) for the algorithmic-class comparison, [`docs/specs/roadmap.md#backlog-unscheduled`](../docs/specs/roadmap.md#backlog-unscheduled) for the backlog entry.

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.
