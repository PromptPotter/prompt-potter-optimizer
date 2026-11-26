from __future__ import annotations

from collections.abc import Callable

from promptpotter.application.optimizers.nodes import MemberCoupling
from promptpotter.application.optimizers.potter.knobs import EscalationKnobs
from promptpotter.domain.campaign_config import Estimand

ADAPTIVE_QUEUE = (
    MemberCoupling(
        name="resubset_subset_relative_on_thin_bank",
        knobs=("per_round_resubset",),
        bench_knobs=("optimization.elimination_n_min",),
        estimand=Estimand.SELECTION,
        relation=(
            "per_round_resubset=ON re-draws the scored subset per round. Every "
            "cross-round comparator reads ONE anchored δ ruler in θ — the stall replayer, "
            "c0_ok, the round-winner election and PoBB elimination all via "
            "fit_theta on cycle.difficulty — and that ruler is EXTENDED to cover each "
            "round's new cells, so the fitness-space collision is resolved."
        ),
        consequence=(
            "Resubset is comparability-coherent in θ, with ONE residual, WARMTH: below "
            "elimination_n_min banked samples the ruler stays flat and θ is logit-fitness "
            "on each arm's OWN subset, so those rounds are subset-relative until it warms. "
            "A cell missing from a WARM ruler is not that residual — it raises."
        ),
        severity="info",
        predicate=lambda _c, k, _d, _s: bool(k.per_round_resubset),
    ),
    MemberCoupling(
        name="display_subset_relative_under_resubset",
        knobs=("per_round_resubset",),
        bench_knobs=("display_metric",),
        estimand=Estimand.DISPLAY,
        relation=(
            "With per_round_resubset ON, fitness/composite are subset-relative while "
            "the gate is θ; the number the operator reads first should be ability "
            "(θ) or carry the subset badge."
        ),
        consequence=(
            "The views lead with fitness/composite while θ decides the winner. Every "
            "surface now prints θ beside it, so the pairing is legible rather than "
            "unexplained — but the number read first is still the subset-relative one. Set "
            "display_metric='ability' under resubset."
        ),
        # `inert`, not `info`: nothing co-moves; one knob's display choice wastes the other's invariance.
        severity="inert",
        predicate=lambda c, k, _d, _s: (
            bool(k.per_round_resubset) and c is not None and c.display_metric != "ability"
        ),
    ),
)


POBB = (
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
        predicate=lambda _c, k, _d, _s: k.epsilon_floor > k.epsilon,
    ),
    MemberCoupling(
        name="informative_rows_without_overlap",
        knobs=("row_choice",),
        bench_knobs=(),
        estimand=Estimand.SELECTION,
        relation=(
            "row_choice=informative takes each block's rows by expected information, which reverses "
            "the round order's stratified interleave (verdict-resolution.md); the election then "
            "pairs an arm with its parent on the origin panel alone, theta_election.vote=overlap."
        ),
        consequence=(
            "The selector votes on the rows the race chose, which are no longer a blind draw, so "
            "the bar would read the race's choice as the arm's ability. Refused before the mint."
        ),
        severity="refused",
        predicate=lambda _c, k, _d, s: k.row_choice == "informative" and s.vote != "overlap",
    ),
    MemberCoupling(
        name="refit_without_informative_rows",
        knobs=("refit", "row_choice"),
        bench_knobs=(),
        estimand=Estimand.SELECTION,
        relation="refit is the θ row_choice=informative chooses rows on.",
        consequence=(
            "refit is set off the manifest's value, but row_choice=random chooses no row, so the "
            "setting is inert."
        ),
        severity="inert",
        predicate=lambda _c, k, d, _s: k.row_choice == "random" and k.refit != d.refit,
    ),
)


# The `EscalationLadder` predicate each knob is read under: a patience paces the next rung's climb.
_READ_ON = {"l1_patience": "fires_l2", "l2_patience": "fires_l3", "l3_patience": "fires_l3"} | {
    f"{act}_capability": "fires_l2" for act in ("rebase", "reelect", "terminate")
}
assert set(_READ_ON) == set(EscalationKnobs.model_fields) - {"escalation_ladder"}, "a rung unread"


def _unread(
    knob: str, rung: str
) -> Callable[[object, EscalationKnobs, EscalationKnobs, object], bool]:
    return lambda _c, k, d, _s: (
        not getattr(k.escalation_ladder, rung) and getattr(k, knob) != getattr(d, knob)
    )


LADDER = tuple(
    MemberCoupling(
        name=f"{knob}_above_the_ladder",
        knobs=(knob, "escalation_ladder"),
        bench_knobs=(),
        estimand=Estimand.CONTROLLER,
        relation=f"{knob} is read only on a rung `escalation_ladder` climbs to.",
        consequence=(
            f"{knob} is set off the manifest's value, but the ladder never climbs to the rung "
            "that reads it, so the setting is inert."
        ),
        severity="inert",
        predicate=_unread(knob, rung),
    )
    for knob, rung in _READ_ON.items()
)
