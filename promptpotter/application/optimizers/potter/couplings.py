"""The couplings potter's members declare between their knobs and the campaign's."""

from __future__ import annotations

from promptpotter.application.campaign_config import Estimand
from promptpotter.application.optimizers.nodes import MemberCoupling
from promptpotter.shared.hashing import shapes_optimizer_prompt

# Their prose reaches the optimizer prompt's `confounds` panel.
shapes_optimizer_prompt(__name__)

__all__ = ["ADAPTIVE_QUEUE", "ESCALATION", "POBB"]


ADAPTIVE_QUEUE = (
    MemberCoupling(
        name="resubset_subset_relative_on_thin_bank",
        knobs=("per_round_resubset",),
        bench_knobs=("optimization.elimination_n_min",),
        estimand=Estimand.SELECTION,
        relation=(
            "per_round_resubset=ON re-picks the scored subset per candidate. Every "
            "cross-round comparator reads ONE anchored δ ruler in θ — the stall replayer, "
            "c0_ok, the round-winner election and PoBB elimination all via "
            "fit_theta_given_delta on cycle.ruler — and that ruler is EXTENDED to cover each "
            "round's new cells, so the accuracy-space collision is resolved."
        ),
        consequence=(
            "Resubset is comparability-coherent in θ, with TWO residuals. WARMTH: below "
            "elimination_n_min banked samples the ruler stays flat and θ is logit-accuracy "
            "on each arm's OWN subset, so those rounds are subset-relative until it warms. "
            "BAND: the acquisition buys the cells whose δ sits nearest the leader's θ, which "
            "against a wide bank collapses onto a narrow δ range — inside it every cell is "
            "equally hard, so θ reduces to logit-accuracy plus a constant while the ruler is "
            "warm and every id matches, which makes this the silent one. Turning resubset "
            "OFF freezes the panel to the campaign-start prefix, which removes the band "
            "residual by forcing the same cells into every panel. A cell missing from a WARM "
            "ruler is neither residual — it raises."
        ),
        severity="info",
        predicate=lambda c, k, d: bool(k.per_round_resubset),
    ),
    MemberCoupling(
        name="headline_subset_relative_under_resubset",
        knobs=("per_round_resubset",),
        bench_knobs=("headline_metric",),
        estimand=Estimand.DISPLAY,
        relation=(
            "With per_round_resubset ON, accuracy/composite are subset-relative while "
            "the gate is θ; the headline number the operator reads should be ability "
            "(θ) or carry the subset badge."
        ),
        consequence=(
            "The headline reads accuracy/composite while θ decides the winner. Every "
            "surface now prints θ beside it, so the pairing is legible rather than "
            "unexplained — but the headline is still the subset-relative one. Set "
            "headline_metric='ability' under resubset."
        ),
        # `inert`, not `info`: nothing co-moves — one knob's display choice wastes the other's
        # invariance, which is exactly what inert names.
        severity="inert",
        predicate=lambda c, k, d: bool(k.per_round_resubset) and c.headline_metric != "ability",
    ),
)


POBB = (
    MemberCoupling(
        name="lock_in_floor_below_elimination",
        knobs=("lock_in_n_min",),
        bench_knobs=("optimization.elimination_n_min",),
        estimand=Estimand.STOPPING,
        relation=(
            "A leader can lock in (and stop measuring) at lock_in_n_min samples; "
            "losers only start being eliminated at elimination_n_min."
        ),
        consequence=(
            "If lock-in fires on fewer samples than elimination needs, a leader is "
            "crowned before the field can be dropped — the round can end before it is "
            "tested. Keep lock_in_n_min ≥ elimination_n_min."
        ),
        severity="info",
        predicate=lambda c, k, d: k.lock_in_n_min < c.optimization.elimination_n_min,
    ),
    MemberCoupling(
        name="epsilon_threshold_inert",
        knobs=("epsilon", "epsilon_elimination"),
        bench_knobs=(),
        estimand=Estimand.STOPPING,
        relation="epsilon is read only by the Bayesian best-test (epsilon_elimination).",
        consequence=(
            "epsilon was tuned away from the manifest's value but epsilon_elimination is "
            "OFF, so nothing reads it — the knob is inert and every round runs full budget."
        ),
        severity="inert",
        predicate=lambda c, k, d: (not k.epsilon_elimination) and k.epsilon != d.epsilon,
    ),
    MemberCoupling(
        name="epsilon_floor_inverted",
        knobs=("epsilon_floor", "epsilon"),
        bench_knobs=(),
        estimand=Estimand.STOPPING,
        relation=(
            "epsilon_floor is the bar at elimination_n_min, ramping UP to epsilon "
            "by twice that depth — being a floor, it belongs at or below epsilon."
        ),
        consequence=(
            "epsilon_floor sits ABOVE epsilon, which would grade the bar DOWNWARD "
            "as evidence accumulates. The ramp goes flat at epsilon instead, so the floor "
            "is inert and shallow candidates are cut on the deep bar."
        ),
        severity="inert",
        predicate=lambda c, k, d: k.epsilon_floor > k.epsilon,
    ),
    MemberCoupling(
        name="lock_in_threshold_inert",
        knobs=("lock_in", "lock_in_n_min", "leader_lock_in"),
        bench_knobs=(),
        estimand=Estimand.STOPPING,
        relation=(
            "lock_in / lock_in_n_min only govern anything while the leader_lock_in toggle is ON."
        ),
        consequence=(
            "A lock-in threshold was tuned but leader_lock_in is OFF, so no leader "
            "ever locks in early — the knobs are inert."
        ),
        severity="inert",
        predicate=lambda c, k, d: (
            (not k.leader_lock_in)
            and (k.lock_in != d.lock_in or k.lock_in_n_min != d.lock_in_n_min)
        ),
    ),
)


ESCALATION = (
    MemberCoupling(
        name="lives_no_headroom",
        knobs=("lives",),
        bench_knobs=("optimization.max_rounds",),
        estimand=Estimand.ESCALATION,
        relation=(
            "lives stops a stalling run early, but only when the bank can empty before the "
            "calendar cap does."
        ),
        consequence=(
            "lives.start ≥ max_rounds, so hearts can never run out first: the run stops on "
            "the calendar and reports `lives_exhausted` for it. Lower lives.start to brake a "
            "stalling run early, or raise max_rounds to give it room — leaving both equal "
            "makes the stop reason unreadable."
        ),
        severity="inert",
        predicate=lambda c, k, d: (
            k.lives is not None
            and c.optimization.max_rounds is not None
            and k.lives.start >= c.optimization.max_rounds
        ),
    ),
)
