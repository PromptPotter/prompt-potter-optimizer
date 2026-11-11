from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from pydantic import Field

from promptpotter.domain.arms import Cut
from promptpotter.domain.cell_terms import CellTerm
from promptpotter.domain.l4.inner_facts import InnerCellFacts, SeriesGap
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.results import CycleResult, RoundResult
from promptpotter.domain.ruler import AbilityReading, ThetaCaveat
from promptpotter.domain.scoring import GradedCell, PipelineData
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.statistics import sample_sd

logger = logging.getLogger(__name__)


class OuterSampleProxies(FrozenModel):
    """Its field may not be defaulted: a cycle that cannot fill it is floored or excluded, never zeroed."""

    # The SCORED term: the standing selection's headline on the cell's held-out rows minus the
    # origin's on the same rows, in accuracy points, averaged over the round budget. A promotion
    # on the rows the search selected on is in-sample and reads here only if it holds on fresh ones.
    held_out_lift: float = Field(ge=-1.0, le=1.0)


OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)

# Here because the `promptpotter` connector declares it too and may not import `application/runner`.
INNER_RESULT_KEY = "final_ranking"
# The facts no term names (the series' reason, its SE, the stop): what the narration and tape read.
INNER_FACTS_KEY = "inner"


def _term(name: str, direction: str, description: str, **rest: object) -> CellTerm:
    return CellTerm.model_validate(
        {"name": name, "direction": direction, "description": description, **rest}
    )


# The `promptpotter` connector's cell vocabulary (`Connector.cell_terms`).
INNER_CELL_TERMS: tuple[CellTerm, ...] = (
    _term(
        "held_out_lift",
        "high",
        "The inner campaign's mean lift over its origin on its held-out rows.",
    ),
    _term("held_out_origin", "high", "The inner origin's headline on the held-out rows."),
    _term("held_out_final_lift", "high", "The held-out lift the inner campaign ended on."),
    _term("lift", "high", "The inner campaign's mean θ lift over its own origin."),
    _term("origin", "high", "The inner campaign's origin level."),
    _term("final_lift", "high", "The lift the inner campaign ended on."),
    _term("peak_lift", "high", "The best lift the inner campaign reached."),
    _term("rounds", "low", "Rounds the inner campaign ran.", dial="anchored"),
    _term("round_budget", "high", "Rounds the inner campaign was allowed."),
)

# Each reported key's `InnerCellFacts` field: the fact rides ONCE, under the key, never in `inner`.
# `cost` and `tokens` answer the core channels of those names: no node usage prices an inner run.
_FACT_OF_KEY: dict[str, str] = {
    "held_out_origin": "bench_origin_level",
    "held_out_final_lift": "bench_final_lift",
    "lift": "mean_round_delta",
    "origin": "origin_level",
    "final_lift": "final_lift",
    "peak_lift": "peak_lift",
    "rounds": "rounds_ran",
    "round_budget": "round_budget",
    "cost": "sent_usd",
    "tokens": "tokens",
}
assert set(_FACT_OF_KEY.values()) <= set(InnerCellFacts.model_fields)

# What the connector reports beside its terms (`Connector.reported_keys`).
INNER_REPORTED_KEYS: tuple[str, ...] = (INNER_FACTS_KEY, "cost", "tokens")
assert {*OUTER_PROXY_KEYS, *_FACT_OF_KEY, INNER_FACTS_KEY} == {
    *(t.name for t in INNER_CELL_TERMS),
    *INNER_REPORTED_KEYS,
}, "a reported key is no term, fact or proxy, or one went unreported"


def inner_cell_report(facts: InnerCellFacts, proxies: OuterSampleProxies) -> dict[str, object]:
    """An absent value is a key left out: a bound 0 would score a measurement nobody took."""
    dumped = facts.model_dump(mode="json")
    reported = {key: dumped[field] for key, field in _FACT_OF_KEY.items()}
    return {
        **proxies.model_dump(),
        **{key: value for key, value in reported.items() if value is not None},
        INNER_FACTS_KEY: {k: v for k, v in dumped.items() if k not in _FACT_OF_KEY.values()},
    }


def inner_facts_of(pipeline: PipelineData) -> InnerCellFacts | None:
    """Written by the recursion alone, so a row carrying it IS an L4 outer row."""
    report = pipeline.cell_report
    raw: Any = report.get(INNER_FACTS_KEY)
    if raw is None:
        return None
    return InnerCellFacts.model_validate(
        {**raw, **{field: report.get(key) for key, field in _FACT_OF_KEY.items()}}
    )


def outer_proxies_of(pipeline: PipelineData) -> OuterSampleProxies | None:
    """The scored term's one reader; ``None`` on a row the recursion did not write."""
    report = pipeline.cell_report
    if INNER_FACTS_KEY not in report:
        return None
    return OuterSampleProxies.model_validate({key: report[key] for key in OUTER_PROXY_KEYS})


def _origin_headroom(result: CycleResult) -> float | None:
    accuracy = result.origin.accuracy if result.origin is not None else None
    return None if accuracy is None else 1.0 - accuracy.value


def unread_rounds(result: CycleResult) -> list[int]:
    series = result.level_series
    return [] if series is None else [r for r in series.rounds if series.reading(r) is None]


def series_reason(result: CycleResult) -> ThetaCaveat | SeriesGap | None:
    """Why the search split's θ trajectory differences nothing; ``None`` where it does."""
    series = result.level_series
    if series is None:
        return SeriesGap.NEVER_STARTED
    if series.caveat is not None:
        return series.caveat
    if unread_rounds(result):
        return SeriesGap.UNREAD_ROUND
    for rnd in series.rounds:
        read = series.reading(rnd)
        if read is not None and read.caveat is not None:
            return read.caveat
    if len(series.rounds) < 2:
        return SeriesGap.NO_L1_ROUND
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


def spent_on_line(rnd: int, cut_round: int | None) -> bool:
    """A round its cycle ran itself (from *cut_round* on, a fork's), never the origin it is differenced against."""
    return rnd > 0 and (cut_round is None or rnd >= cut_round)


def line_round_levels(
    spans: Iterable[tuple[Sequence[tuple[int, float | None]], int | None]],
) -> list[float | None]:
    """Each cycle's whole ``(round, level)`` series and its cut round, oldest first: the line's
    `CycleResult.bench_round_levels`, every round in the order the line spent it."""
    return [level for series, cut in spans for rnd, level in series if spent_on_line(rnd, cut)]


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
    ses = [level.se for level in rounds]
    if not ses:
        return None
    # Mean of the SEs, never `σ/√n` (the levels NEST); the origin's cancels in `variant - origin`.
    return float(sum(ses) / len(ses))


def _spoiled(rnd: RoundResult) -> bool:
    """No proposal reached a cell, whatever the reply's failure: an empty panel is spoiled too."""
    return all(isinstance(arm, Cut) and arm.kind == "invalid" for arm in rnd.panel.values())


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
    if result.stop_reason is StopReason.OPTIMIZER_ABORT:
        return "its optimizer prompt terminated the inner campaign (optimizer_abort)"
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    spent = [r for r in result.rounds if spent_on_line(r.round, None)]
    if spent and all(_spoiled(r) for r in spent):
        return (
            f"no proposal of its {len(spent)} L1 round(s) reached a cell: a reply that yielded "
            "none, or only invalid edits"
        )
    return None


def _floor_proxies() -> OuterSampleProxies:
    """``-1`` sits at or below the bottom of every re-anchoring window, so the composed fitness is exactly 0.0."""
    return OuterSampleProxies(held_out_lift=-1.0)


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
    return OuterSampleProxies(held_out_lift=sum(bench) / len(bench) - result.bench_origin_level)


class PanelPrecision(FrozenModel):
    """How sharply each cell was measured, against how far apart the cells landed."""

    # In θ logits, never fitness: a cell's precision cannot cross the user-editable scoring formula.
    # sqrt(mean over cells of (se_variant² + se_origin²)): the spread if every cell had ONE true value.
    estimation_sd: float
    # Sample SD (n−1) of the per-cell paired diffs. Contains `estimation_sd` plus any real spread.
    observed_sd: float
    n_cells: int
    # No RATIO of the two is served: a clamped share rounds noise above the total spread into "100% noise".


def _level(pipeline: PipelineData) -> float | None:
    inner = inner_facts_of(pipeline)
    return None if inner is None else inner.mean_round_delta


def _level_se(pipeline: PipelineData) -> float | None:
    inner = inner_facts_of(pipeline)
    return None if inner is None else inner.mean_parent_level_se


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
