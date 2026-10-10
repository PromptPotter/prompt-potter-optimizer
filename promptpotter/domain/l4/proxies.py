from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence

from pydantic import ConfigDict, Field

from promptpotter.domain.optimizer_state import PARSE_FAILURE_TOOLING
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.results import ArmOutcome, CycleResult, RoundResult
from promptpotter.domain.scoring import PIPELINE_KEYS, GradedCell, PipelineData
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.statistics import sample_sd

logger = logging.getLogger(__name__)


class OuterSampleProxies(StrictModel):
    """Its field may NOT be defaulted: a cycle that cannot fill it is floored or excluded, never zeroed."""

    model_config = ConfigDict(frozen=True)

    # A PLAUSIBILITY bound: a difference of two logit abilities is unbounded, so ±4 binds only a runaway fit.
    mean_round_delta: float = Field(ge=-4.0, le=4.0)


OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)

# Here because the `promptpotter` connector declares it too and may not import `application/runner`.
INNER_RESULT_KEY = "final_ranking"

# The SE of the arm's own parent level, NOT of the delta.
PARENT_LEVEL_SE_KEY = "mean_parent_level_se"


class InnerCellFacts(StrictModel):
    """Carried BESIDE the measurement, never as ``OuterSampleProxies`` fields the formula could score."""

    model_config = ConfigDict(frozen=True)

    inner_origin_level: float
    inner_final_lift: float
    inner_peak_lift: float
    inner_rounds_ran: int
    inner_round_budget: int
    inner_stop_reason: StopReason
    # Reporting only, cumulative across attempts: billing is each call's own, and these enter no ledger.
    inner_sent_usd: float | None
    inner_tokens: int | None
    inner_campaign_id: str


INNER_FACT_KEYS: tuple[str, ...] = tuple(InnerCellFacts.model_fields)


def inner_cell_facts(result: CycleResult, campaign_id: str) -> InnerCellFacts | None:
    """``None`` where there is no trajectory (a floored cell, an unscored origin): absent, never zeroed."""
    levels = result.round_levels
    if result.origin_level is None or not levels:
        return None
    origin = result.origin_level
    return InnerCellFacts(
        inner_origin_level=origin,
        inner_final_lift=levels[-1] - origin,
        inner_peak_lift=max(levels) - origin,
        inner_rounds_ran=result.n_rounds_after_origin,
        inner_round_budget=effective_round_budget(result),
        inner_stop_reason=result.stop_reason,
        inner_sent_usd=result.spend.sent_usd if result.spend else None,
        inner_tokens=result.spend.total_tokens_used if result.spend else None,
        inner_campaign_id=campaign_id,
    )


def effective_round_budget(result: CycleResult) -> int:
    ran = len(result.round_levels)
    return ran if result.round_budget is None else max(result.round_budget, ran)


def parent_level_series(result: CycleResult) -> list[float]:
    """Padded forward to the ROUND BUDGET: a mean over the series length is a different estimand per cell."""
    levels = result.round_levels
    if not levels:
        return []
    return levels + [levels[-1]] * (effective_round_budget(result) - len(levels))


def mean_parent_level_se(result: CycleResult) -> float | None:
    """One arm's half of a paired difference (two add in quadrature). A PRECISION, never a penalty or rank key."""
    ses = result.round_level_ses
    if not ses:
        return None
    # Mean of the SEs, never `σ/√n` (the levels NEST); `origin_level` cancels in `variant - origin`.
    return float(sum(ses) / len(ses))


def _is_evidential(rnd: RoundResult) -> bool:
    """A round that lost its candidates to an empty optimizer response is missing data, not a bad mutation."""
    if not rnd.candidate_scores:
        return not rnd.optimizer_state.payload.lost_to_empty_response()
    return not all(
        cs.outcome is ArmOutcome.INVALID
        and any(vf.reason == PARSE_FAILURE_TOOLING for vf in cs.validation_failures)
        for cs in rnd.candidate_scores
    )


def no_evidence_reason(result: CycleResult) -> str | None:
    """Only a SUCCESS ``StopOutcome`` is a measurement: a cut-short optimizer prompt reads flawless to every aggregate."""
    outcome = stop_reason_outcome(result.stop_reason)
    if outcome is not StopOutcome.SUCCESS:
        return (
            f"it did not end on its own terms — {outcome} (stop_reason={result.stop_reason}); "
            "its trajectory was cut short by something the optimizer prompt does not own"
        )
    if not result.rounds:
        return f"it ran no L1 rounds (stop_reason={result.stop_reason})"
    if result.origin_level is None:
        return "its origin was never scored, so there is no floor to difference its rounds against"
    if not result.round_levels:
        return "it held no parent levels to difference against its origin"
    return None


def floor_reason(result: CycleResult) -> str | None:
    """Optimizer-prompt-OWNED, so FLOORED rather than excluded: it is reproducible, hence evidence."""
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    if result.rounds and not any(_is_evidential(r) for r in result.rounds):
        return (
            f"every one of its {len(result.rounds)} L1 round(s) lost its candidates to an "
            "empty optimizer response"
        )
    return None


def _floor_proxies() -> OuterSampleProxies:
    """``-1`` is the bottom of the scoring formula's re-anchoring window, so the composed fitness is exactly 0.0."""
    return OuterSampleProxies(mean_round_delta=-1.0)


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    """Origin and rounds share one fit, so the ruler cancels in the delta."""
    if (floor := floor_reason(result)) is not None:
        logger.warning("inner cycle scored at the floor: %s", floor)
        return _floor_proxies()
    if (reason := no_evidence_reason(result)) is not None:
        logger.warning("inner cycle EXCLUDED (no evidence about the optimizer prompt): %s", reason)
        # The inner cycle forwarded its own spend onto the outer ledger as it ran.
        raise CellUnscoreableError(reason, spent={})

    assert result.origin_level is not None  # guaranteed by no_evidence_reason
    # Levels are abilities in LOGITS on the fixed ruler; nothing normalizes for difficulty.
    levels = parent_level_series(result)
    return OuterSampleProxies(
        mean_round_delta=sum(levels) / len(levels) - result.origin_level,
    )


class PanelPrecision(StrictModel):
    """How sharply each cell was measured, against how far apart the cells landed."""

    # In θ logits, never fitness: a cell's precision cannot cross the user-editable scoring formula.
    model_config = ConfigDict(frozen=True)

    # sqrt(mean over cells of (se_variant² + se_origin²)): the spread if every cell had ONE true value.
    estimation_sd: float
    # Sample SD (n−1) of the per-cell paired diffs. Contains `estimation_sd` plus any real spread.
    observed_sd: float
    n_cells: int
    # No RATIO of the two is served: a clamped share rounds noise above the total spread into "100% noise".


assert {*OUTER_PROXY_KEYS, PARENT_LEVEL_SE_KEY, *INNER_FACT_KEYS} <= PIPELINE_KEYS, (
    "an L4 key PipelineData does not declare is filed as a dataset observation, and no field "
    "reader finds it"
)


def _level(pipeline: PipelineData) -> float | None:
    return pipeline.mean_round_delta


def _level_se(pipeline: PipelineData) -> float | None:
    return pipeline.mean_parent_level_se


def cell_values(
    rows: Iterable[GradedCell], read: Callable[[PipelineData], float | None]
) -> dict[str, float]:
    """Keyed by QUERY: a per-campaign ``sample_id`` names a different cell in each campaign."""
    acc: dict[str, list[float]] = {}
    for r in rows:
        value = read(r.facts.pipeline)
        if value is not None:
            acc.setdefault(r.facts.query, []).append(value)
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def panel_precision(
    variant_rows: Sequence[GradedCell], origin_rows: Sequence[GradedCell]
) -> PanelPrecision | None:
    """``None`` below two cells both arms measured: a fabricated 0.0 would read as a perfect instrument."""
    # Two reads, not one tuple: a FLOORED cell carries a level and no SE.
    v_level, o_level = cell_values(variant_rows, _level), cell_values(origin_rows, _level)
    v_se, o_se = cell_values(variant_rows, _level_se), cell_values(origin_rows, _level_se)
    cells = sorted(v_level.keys() & o_level.keys() & v_se.keys() & o_se.keys())
    if len(cells) < 2:
        return None
    observed = sample_sd([v_level[c] - o_level[c] for c in cells])
    assert observed is not None
    estimation = (sum(v_se[c] ** 2 + o_se[c] ** 2 for c in cells) / len(cells)) ** 0.5
    return PanelPrecision(estimation_sd=float(estimation), observed_sd=observed, n_cells=len(cells))


__all__ = [
    "INNER_FACT_KEYS",
    "OUTER_PROXY_KEYS",
    "PARENT_LEVEL_SE_KEY",
    "InnerCellFacts",
    "OuterSampleProxies",
    "PanelPrecision",
    "cell_values",
    "compute_outer_proxies",
    "floor_reason",
    "inner_cell_facts",
    "mean_parent_level_se",
    "no_evidence_reason",
    "panel_precision",
    "parent_level_series",
]
