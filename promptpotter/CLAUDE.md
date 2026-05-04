# promptpotter/ — agent contract for L1 / L2 / L3

The optimizer is three nested generation loops. Each layer mutates with cause, never at random.

The cycle has hardcoded stop conditions (e.g. `max_rounds`). On top of those, **the L2-layer and L3-layer may decide on their own to terminate the loop** — see below.

## L1-layer — l1_generate

The parent searchpoint was selected for measured reasons. `l1_generate` mutates a pipeline-configuration field only with cause:

- stall-driven escalation,
- sibling-yield evidence on the chosen axis, or
- an explicit `l2_directive`.

No data justifying a choice ⇒ do not gamble. Random exploration is reserved for explicit stall.

`l1_generate`'s evidence base lives on its surface:

- `parent_baseline` — parent composite + per-sample tally + delta to beat.
- `sibling_yield` — prior round per-axis yield: `axis | n_tried | n_beat_parent | mean_delta`.
- `escalation_panel` — `stall_rounds`, `last_winner_axis`, `params_unlocked`, `exploration_budget ∈ {tight, normal, wide}`.

If a panel field speaks against a mutation, `l1_generate` does not propose it.

## L2-layer — l2_context

Fires only on L1-layer stall. Receives the evidence panels plus the prior `l1_critique`. `l2_context` produces:

- a 2–3 sentence **directive** injected into `l1_generate`'s meta-prompt as primary signal, and
- optional `l1_section_overrides` + optimizer-param tweaks (never pipeline_params — those belong to `l1_generate`'s surface).

The directive is **evidence-anchored** — it cites a specific axis, sample, or yield number from the panels. Speculative directives ("maybe try X") are out of contract. Sliding window of 1: a new directive supersedes the prior; cleared on improvement (when the L2-layer doesn't fire). The next directive evolves from the prior, not from scratch.

The L2-layer also **heals `l1_generate`** on:

- `ValidationFailure` (`l1_generate` produced a malformed variant), and
- `RuntimeFailure` from mid-eval `DegradationCheck`.

Healing uses the same surface as a normal directive — framed as a remedial nudge rather than a strategic shift.

The L2-layer may **terminate the loop** on:

- goal reached (composite ≥ goal, sustained one round), or
- infinite stall (no improvement reachable through directive nudges).

The L2-layer escalating to L3 is **rare** — only when the failure mode is outside the directive surface: context-shape mismatch, scoring-set drift, repeated cross-field duplication that a directive cannot resolve. Default: the L2-layer keeps nudging the L1-layer.

## L3-layer — l3_plan

Fires only on L2-layer stall (L2 patience exceeded). Receives the evidence panels plus `l2_summary` (the prior directives + their measured lift) and the runtime-failure trail. `l3_plan` produces:

- a **strategic replan** — rewrites the directive surface, escalation policy, or which axes are in scope; the cycle continues under a new plan rather than a new variant.

The L3-layer also **heals the L2-layer** on validator outcomes:

- cross-field duplication,
- verbatim self-repeat across directives, or
- catalogue redundancy.

These are signs that `l2_context` directives are thrashing within an axis rather than across the plan-space — the L3-layer rewrites the policy, not just the next directive.

The L3-layer may **terminate the loop** on the same two cases as the L2-layer (goal reached / infinite stall). If the L3-layer fires repeatedly inside one cycle, that is the loop's signal that the plan-space itself is exhausted — `l3_plan` should terminate rather than replan again.

L3-layer firing is **rarer still** than L2 — a fire signals the cycle's plan was wrong, not that one variant missed. Default: the L3-layer stays idle while the L2-layer carries the load.

## Signals come from measurement, not from the calendar

Avoid hardcoded round thresholds inside the loops. `params_unlocked` derives from stall depth + mutation history, not `round ≥ 3`. `exploration_budget` widens with `stall_rounds`, not on a fixed schedule. Hardcoded stop conditions sit at the cycle boundary; everything inside the loops reasons from measurement.
