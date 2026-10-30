from __future__ import annotations

from promptpotter.application.optimizers.nodes import MemberCoupling
from promptpotter.domain.campaign_config import Estimand
from promptpotter.shared.hashing import shapes_optimizer_prompt

# Their prose reaches the optimizer prompt's `confounds` panel.
shapes_optimizer_prompt(__name__)


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
            "round's new cells, so the accuracy-space collision is resolved."
        ),
        consequence=(
            "Resubset is comparability-coherent in θ, with ONE residual, WARMTH: below "
            "elimination_n_min banked samples the ruler stays flat and θ is logit-accuracy "
            "on each arm's OWN subset, so those rounds are subset-relative until it warms. "
            "A cell missing from a WARM ruler is not that residual — it raises."
        ),
        severity="info",
        predicate=lambda _c, k, _d: bool(k.per_round_resubset),
    ),
    MemberCoupling(
        name="display_subset_relative_under_resubset",
        knobs=("per_round_resubset",),
        bench_knobs=("display_metric",),
        estimand=Estimand.DISPLAY,
        relation=(
            "With per_round_resubset ON, accuracy/composite are subset-relative while "
            "the gate is θ; the number the operator reads first should be ability "
            "(θ) or carry the subset badge."
        ),
        consequence=(
            "The views lead with accuracy/composite while θ decides the winner. Every "
            "surface now prints θ beside it, so the pairing is legible rather than "
            "unexplained — but the number read first is still the subset-relative one. Set "
            "display_metric='ability' under resubset."
        ),
        # `inert`, not `info`: nothing co-moves; one knob's display choice wastes the other's invariance.
        severity="inert",
        predicate=lambda c, k, _d: bool(k.per_round_resubset) and c.display_metric != "ability",
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
        predicate=lambda _c, k, _d: k.epsilon_floor > k.epsilon,
    ),
)
