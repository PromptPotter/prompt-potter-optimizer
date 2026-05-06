# promptpotter/ — agent contract for L1 / L2 / L3

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

The cycle has hardcoded stop conditions (e.g. `max_rounds`). On top of those, **the L2-layer and L3-layer may decide on their own to terminate the loop** — see below.

## L1-layer — l1_generate

The parent searchpoint was selected for measured reasons. `l1_generate` mutates a pipeline-configuration field only with cause:

- stall-driven escalation,
- sibling-yield evidence on the chosen axis, or
- a refined `task_context` framing from L2.

No data justifying a choice ⇒ do not gamble. Random exploration is reserved for explicit stall.

`l1_generate`'s evidence base lives on its surface:

- `parent_baseline` — parent composite + per-sample tally + delta to beat.
- `sibling_yield` — prior round per-axis yield: `axis | n_tried | n_beat_parent | mean_delta`.
- `escalation_panel` — `stall_rounds`, `last_winner_axis`, `params_unlocked`, `exploration_budget ∈ {tight, normal, wide}`.

If a panel field speaks against a mutation, `l1_generate` does not propose it.

Channel: `task_context` (L2-refined task framing) and `plan` (L3-set strategy) arrive on `OptSearchPoint` and surface alongside the panels — `l1_generate` is fan-in, reading both layers' outputs in the same round. Composed by `DispatchHub.fill_l1` walking `opt_sp.l1_layout` over the `SIGNALS` registry (`dispatch_hub.py`).

## L2-layer — l2_context

Fires only on L1-layer stall. Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- a refined `task_context` — the persistent task-framing dict that every layer (L1, L1_CRITIQUE, L2, L3) reads next round, and
- optional `l1_layout` edits + optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The refinement is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative refinements ("maybe try X") are out of contract. `task_context` is persistent, accumulative: each fire merges deltas onto the existing dict; full rewrites are rare. A proposed update that merges to a no-op against the prior framing is flagged as `l2_task_context_verbatim_repeat` → L3 heal trigger.

Channel: written to `OptSearchPoint.task_context`; read by every prompt via the `task_context` signal. Persistent across L2 fires until L2 next refines (or L3 replans).

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal refinement — framed as a remedial nudge rather than a strategic shift.

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

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.
