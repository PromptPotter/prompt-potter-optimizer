"""It fits nothing: a δ here is the ruler's, a θ the round's own stamp."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    build_round_order,
    pick_value,
)
from promptpotter.domain.cells import (
    HARD_SAMPLES_VERSION,
    HardSampleCell,
    HardSamples,
    SampleDifficulty,
)
from promptpotter.domain.ruler import RulerStanding
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.intelligence.rasch import Observation
    from promptpotter.domain.results import HardSampleOrder, RoundResult
    from promptpotter.domain.ruler import DeltaRuler

__all__ = [
    "build_hard_samples",
    "rank_hard_samples",
    "read_hard_samples",
    "stamped_abilities",
]


def stamped_abilities(
    rounds: Sequence[RoundResult], ruler: DeltaRuler | None
) -> tuple[tuple[float, float] | None, dict[str, float]]:
    frontier = next(
        (
            (rr.ability.theta, rr.ability.se)
            for rr in reversed(rounds)
            if ruler is not None
            and rr.ability is not None
            and rr.ability.se is not None
            and rr.ability.ruler_id == ruler.anchor_id
        ),
        None,
    )
    theta = {
        c.candidate_id: c.theta for rr in rounds for c in rr.candidate_scores if c.theta is not None
    }
    return frontier, theta


def build_hard_samples(
    observations: Sequence[Observation],
    ruler: DeltaRuler | None,
    *,
    frontier: tuple[float, float] | None,
    arm_theta: Mapping[str, float],
    parent_grades: Mapping[str, float],
    slots: Mapping[str, int],
    cycle_id: str | None,
) -> HardSamples:
    """*slots* places each ``Sample.key`` at its row in the dataset served: a cell no row holds is not drawn."""
    standing = RulerStanding.of(ruler)
    carried = (
        {key: (d, ruler.delta_se[key]) for key, d in ruler.delta.items() if key in slots}
        if ruler is not None
        else {}
    )
    measured = {o.key for o in observations if o.key in slots}

    def difficulty(key: str) -> SampleDifficulty:
        pick: float | None = None
        if ruler is None or key not in carried:
            return SampleDifficulty.on(standing, None, pick_score=pick)
        delta, se = carried[key]
        if frontier is not None:
            mu, mu_se = frontier
            var = ruler.sigma_theta * ruler.sigma_theta
            pick = pick_value(mu, var, mu, mu_se * mu_se, delta, se)
        return SampleDifficulty.on(standing, (delta, se), pick_score=pick)

    def rank(key: str) -> tuple[int, float, int]:
        return (0, -carried[key][0], slots[key]) if key in carried else (1, 0.0, slots[key])

    def candidate_key(cid: str) -> tuple[int, float, str]:
        return (0, -arm_theta[cid], cid) if cid in arm_theta else (1, 0.0, cid)

    candidate_order = sorted({o.candidate_id for o in observations}, key=candidate_key)
    held = sorted({*carried, *measured}, key=slots.__getitem__)
    key_at = {slots[key]: key for key in measured}
    return HardSamples(
        schema_version=HARD_SAMPLES_VERSION,
        cycle_id=cycle_id,
        generated_at=utcnow_iso(),
        ruler=standing,
        candidate_order=candidate_order,
        theta={cid: arm_theta[cid] for cid in candidate_order if cid in arm_theta},
        sample_ids={key: slots[key] for key in held},
        sample_order=sorted(measured, key=rank),
        samples={key: difficulty(key) for key in held},
        round_order=[key_at[sid] for sid in build_round_order(parent_grades, ruler, key_at)],
        cells=[
            HardSampleCell(candidate=o.candidate_id, sample_key=o.key, fitness=o.response)
            for o in observations
            if o.key in slots
        ],
    )


def rank_hard_samples(
    view: HardSamples,
    rows: Mapping[str, int],
    *,
    measured: Collection[str],
    order: HardSampleOrder,
) -> tuple[HardSampleOrder, list[str]]:
    """*rows* places each sample key to rank at its row, which breaks every tie."""
    picks = {k: on.pick_score for k, on in view.samples.items() if on.pick_score is not None}
    deltas = {k: on.delta for k, on in view.samples.items() if on.delta is not None}
    resolved: HardSampleOrder = order if picks else "difficulty"
    keys = picks if resolved == "info_gain" else deltas

    # An unmeasured sample carries a δ it never earned in this scope, so it trails by row.
    def rank(key: str) -> tuple[int, float, int]:
        if key not in measured:
            return (2, 0.0, rows[key])
        return (0, -keys[key], rows[key]) if key in keys else (1, 0.0, rows[key])

    return resolved, sorted(rows, key=rank)


def read_hard_samples(path: Path) -> HardSamples | None:
    held = read_json_tolerant(path)
    if not held or held.get("schema_version") != HARD_SAMPLES_VERSION:
        return None
    return HardSamples.model_validate(held)
