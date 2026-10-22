from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence

from pydantic import Field

from promptpotter.domain.l4.inner_facts import InnerCellFacts
from promptpotter.domain.optimizer_state import PARSE_FAILURE_TOOLING
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import Cut, CycleResult, RoundResult
from promptpotter.domain.ruler import THETA_CAVEAT_INFO, AbilityReading
from promptpotter.domain.scoring import PIPELINE_KEYS, GradedCell, PipelineData
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.statistics import sample_sd

logger = logging.getLogger(__name__)


class OuterSampleProxies(FrozenModel):
    """Its field may not be defaulted: a cycle that cannot fill it is floored or excluded, never zeroed."""

    # The SCORED term: the standing selection's headline on the cell's held-out rows minus the
    # origin's on the same rows, in accuracy points, averaged over the round budget. A promotion
    # on the rows the search selected on is in-sample and reads here only if it holds on fresh ones.
    mean_bench_lift: float = Field(ge=-1.0, le=1.0)


OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)

# Here because the `promptpotter` connector declares it too and may not import `application/runner`.
INNER_RESULT_KEY = "final_ranking"


def _origin_headroom(result: CycleResult) -> float | None:
    accuracy = result.origin.accuracy if result.origin is not None else None
    return None if accuracy is None else 1.0 - accuracy.value


def series_reason(result: CycleResult) -> str | None:
    """Why the search split's θ trajectory differences nothing; ``None`` where it does."""
    series = result.level_series
    if series is None:
        return "it never started, so it read no level series"
    if series.caveat is not None:
        return (
            f"its level series carries `{series.caveat}` "
            f"({THETA_CAVEAT_INFO[series.caveat].head}), so no two of its levels difference"
        )
    if holes := [rnd for rnd in series.rounds if series.level(rnd) is None]:
        return (
            f"round(s) {holes} hold no scored cell on its series' δ scale, or a level carrying a "
            "θ caveat of its own, so there is no level to difference there"
        )
    if len(series.rounds) < 2:
        return "it closed no L1 round, so its series holds no level past its origin's"
    return None


def _trajectory(result: CycleResult) -> tuple[AbilityReading, list[AbilityReading]] | None:
    series = result.level_series
    if series is None or series_reason(result) is not None:
        return None
    levels = [level for rnd in series.rounds if (level := series.level(rnd)) is not None]
    return levels[0], levels[1:]


def parent_levels(result: CycleResult) -> tuple[float, list[float]] | None:
    """``None`` where the series differences nothing (`series_reason` names why)."""
    trajectory = _trajectory(result)
    if trajectory is None:
        return None
    return trajectory[0].theta, [level.theta for level in trajectory[1]]


def inner_cell_facts(result: CycleResult) -> InnerCellFacts:
    held = parent_levels(result)
    origin = None if held is None else held[0]
    levels = [] if held is None else held[1]
    padded = parent_level_series(result)
    bench_origin = result.bench_origin_level
    bench_last = result.bench_round_levels[-1] if result.bench_round_levels else None
    return InnerCellFacts(
        origin_level=origin,
        final_lift=None if origin is None else levels[-1] - origin,
        peak_lift=None if origin is None else max(levels) - origin,
        mean_round_delta=None if origin is None else sum(padded) / len(padded) - origin,
        series_reason=series_reason(result),
        mean_parent_level_se=mean_parent_level_se(result),
        rounds_ran=result.n_rounds_after_origin,
        round_budget=effective_round_budget(result),
        stop_reason=result.stop_reason,
        origin_headroom=_origin_headroom(result),
        bench_origin_level=bench_origin,
        bench_final_lift=(
            None if bench_origin is None or bench_last is None else bench_last - bench_origin
        ),
        sent_usd=result.spend.sent_usd if result.spend else None,
        tokens=result.spend.total_tokens_used if result.spend else None,
    )


def effective_round_budget(result: CycleResult) -> int:
    ran = result.n_rounds_after_origin
    return ran if result.round_budget is None else max(result.round_budget, ran)


def _held_forward(levels: Sequence[float], result: CycleResult) -> list[float]:
    """Padded forward to the ROUND BUDGET: a mean over the series length is a different estimand per cell."""
    if not levels:
        return []
    budget = len(levels) if result.round_budget is None else max(result.round_budget, len(levels))
    return [*levels, *([levels[-1]] * (budget - len(levels)))]


def parent_level_series(result: CycleResult) -> list[float]:
    held = parent_levels(result)
    return [] if held is None else _held_forward(held[1], result)


def bench_level_series(result: CycleResult) -> list[float]:
    """Empty where any round's bench pass read nothing: a hole held forward scores a pass nobody read."""
    levels = result.bench_round_levels
    if any(level is None for level in levels):
        return []
    return _held_forward([level for level in levels if level is not None], result)


def mean_parent_level_se(result: CycleResult) -> float | None:
    """One arm's half of a paired difference (two add in quadrature). A PRECISION, never a penalty or rank key."""
    trajectory = _trajectory(result)
    rounds = [] if trajectory is None else trajectory[1]
    ses = [level.se for level in rounds if level.se is not None]
    if not ses or len(ses) < len(rounds):
        return None
    # Mean of the SEs, never `σ/√n` (the levels NEST); the origin's cancels in `variant - origin`.
    return float(sum(ses) / len(ses))


def _is_evidential(rnd: RoundResult) -> bool:
    """A round that lost its candidates to an empty optimizer response is missing data, not a bad mutation."""
    panel = rnd.panel.values()
    if not panel:
        return not rnd.closed_state.lost_to_empty_response()
    return not all(
        isinstance(arm, Cut)
        and arm.kind == "invalid"
        and any(vf.reason == PARSE_FAILURE_TOOLING for vf in arm.validation_failures)
        for arm in panel
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
    if result.bench_origin_level is None:
        return "its origin's bench pass read nothing, so there is no held-out floor to difference against"
    if holes := [r + 1 for r, level in enumerate(result.bench_round_levels) if level is None]:
        return f"the bench pass of round(s) {holes} read nothing, so its held-out series has a hole"
    if not result.bench_round_levels:
        return "it held no bench levels to difference against its origin's"
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
    """``-1`` sits at or below the bottom of every re-anchoring window, so the composed fitness is exactly 0.0."""
    return OuterSampleProxies(mean_bench_lift=-1.0)


def compute_outer_proxies(result: CycleResult) -> OuterSampleProxies:
    if (floor := floor_reason(result)) is not None:
        logger.warning("inner cycle scored at the floor: %s", floor)
        return _floor_proxies()
    if (reason := no_evidence_reason(result)) is not None:
        logger.warning("inner cycle EXCLUDED (no evidence about the optimizer prompt): %s", reason)
        # The inner cycle forwarded its own spend onto the outer ledger as it ran.
        raise CellUnscoreableError(reason, spent={})

    # Guaranteed by no_evidence_reason.
    assert result.bench_origin_level is not None
    bench = bench_level_series(result)
    return OuterSampleProxies(mean_bench_lift=sum(bench) / len(bench) - result.bench_origin_level)


class PanelPrecision(FrozenModel):
    """How sharply each cell was measured, against how far apart the cells landed."""

    # In θ logits, never fitness: a cell's precision cannot cross the user-editable scoring formula.
    # sqrt(mean over cells of (se_variant² + se_origin²)): the spread if every cell had ONE true value.
    estimation_sd: float
    # Sample SD (n−1) of the per-cell paired diffs. Contains `estimation_sd` plus any real spread.
    observed_sd: float
    n_cells: int
    # No RATIO of the two is served: a clamped share rounds noise above the total spread into "100% noise".


assert set(OUTER_PROXY_KEYS) <= PIPELINE_KEYS, (
    "an L4 key PipelineData does not declare is filed as a dataset observation, and no field "
    "reader finds it"
)


def _level(pipeline: PipelineData) -> float | None:
    return None if pipeline.inner is None else pipeline.inner.mean_round_delta


def _level_se(pipeline: PipelineData) -> float | None:
    return None if pipeline.inner is None else pipeline.inner.mean_parent_level_se


def cell_values(
    rows: Iterable[GradedCell], read: Callable[[PipelineData], float | None]
) -> dict[str, float]:
    """Keyed by QUERY: a per-campaign ``position`` names a different cell in each campaign."""
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
