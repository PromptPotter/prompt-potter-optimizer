"""It fits nothing: a δ here is the ruler's, a θ the round's own stamp."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    build_round_order,
    marginal_hit_probability,
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

    from promptpotter.application.intelligence.exploration import Observation
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
    parent_grades: Mapping[int, float],
    cycle_id: str | None,
) -> HardSamples:
    standing = RulerStanding.of(ruler)
    carried = ruler.delta if ruler is not None else {}
    measured = sorted({o.sample_id for o in observations})

    def difficulty(sid: int) -> SampleDifficulty:
        pick: float | None = None
        p_hat: float | None = None
        if ruler is None or sid not in carried:
            return SampleDifficulty.on(standing, None, pick_score=pick, p_hat=p_hat)
        delta, se = carried[sid], ruler.delta_se[sid]
        if frontier is not None:
            mu, mu_se = frontier
            var = ruler.sigma_theta * ruler.sigma_theta
            pick = pick_value(mu, var, mu, mu_se * mu_se, delta, se)
            p_hat = marginal_hit_probability(mu_c=mu, var_c=var, delta_s=delta, se_delta_s=se)
        return SampleDifficulty.on(standing, (delta, se), pick_score=pick, p_hat=p_hat)

    def sample_key(sid: int) -> tuple[int, float, int]:
        return (0, -carried[sid], sid) if sid in carried else (1, 0.0, sid)

    def candidate_key(cid: str) -> tuple[int, float, str]:
        return (0, -arm_theta[cid], cid) if cid in arm_theta else (1, 0.0, cid)

    candidate_order = sorted({o.candidate_id for o in observations}, key=candidate_key)
    return HardSamples(
        schema_version=HARD_SAMPLES_VERSION,
        cycle_id=cycle_id,
        generated_at=utcnow_iso(),
        ruler=standing,
        candidate_order=candidate_order,
        theta={cid: arm_theta[cid] for cid in candidate_order if cid in arm_theta},
        sample_order=sorted(measured, key=sample_key),
        samples={sid: difficulty(sid) for sid in sorted({*carried, *measured})},
        round_order=build_round_order(parent_grades, ruler, measured),
        cells=[
            HardSampleCell(candidate=o.candidate_id, sample_id=o.sample_id, fitness=o.response)
            for o in observations
        ],
    )


def rank_hard_samples(
    view: HardSamples,
    sample_ids: Iterable[int],
    *,
    measured: Collection[int],
    order: HardSampleOrder,
) -> tuple[HardSampleOrder, list[int]]:
    picks = {sid: on.pick_score for sid, on in view.samples.items() if on.pick_score is not None}
    deltas = {sid: on.delta for sid, on in view.samples.items() if on.delta is not None}
    resolved: HardSampleOrder = order if picks else "difficulty"
    keys = picks if resolved == "info_gain" else deltas

    # An unmeasured sample carries a δ it never earned in this scope, so it trails by id.
    def rank(sid: int) -> tuple[int, float, int]:
        if sid not in measured:
            return (2, 0.0, sid)
        return (0, -keys[sid], sid) if sid in keys else (1, 0.0, sid)

    return resolved, sorted(sample_ids, key=rank)


def read_hard_samples(path: Path) -> HardSamples | None:
    held = read_json_tolerant(path)
    if not held or held.get("schema_version") != HARD_SAMPLES_VERSION:
        return None
    return HardSamples.model_validate(held)
