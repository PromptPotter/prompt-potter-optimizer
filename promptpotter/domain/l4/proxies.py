from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from promptpotter.domain.cell_terms import CellTerm
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.l4.inner_facts import InnerCellFacts, SeriesGap
from promptpotter.domain.paired_reading import READING_STATE_INFO
from promptpotter.domain.phases import StopOutcome, StopReason, stop_reason_outcome
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.ruler import AbilityReading, ThetaCaveat, withheld
from promptpotter.domain.scoring import GradedCell, PipelineData
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import CellUnscoreableError
from promptpotter.shared.statistics import sample_sd


class OuterSampleProxies(FrozenModel):
    """No field may be defaulted: a cycle that cannot fill them is floored or excluded, never zeroed."""

    # 1 where the optimizer prompt spoiled the inner run (`floor_reason`): no lift was measured.
    floored: Literal[0, 1]
    # The standing selection's headline on the cell's held-out rows minus the origin's on the same
    # rows, in headline points, averaged over the round budget (`BenchCurve.area`'s estimate):
    # what ranks and separates outer cells. ``None`` on a floored cell.
    held_out_lift: float | None = Field(ge=-1.0, le=1.0)
    # That estimate's SE, a precision the formula never reads; ``None`` where it took none.
    held_out_lift_se: float | None = Field(ge=0.0)

    @model_validator(mode="after")
    def _a_floor_measured_no_lift(self) -> Self:
        if (self.held_out_lift is None) != bool(self.floored):
            raise ValueError("a floored cell carries no held-out lift, and a measured one does")
        if self.floored and self.held_out_lift_se is not None:
            raise ValueError("a floored cell carries no SE")
        return self


OUTER_PROXY_KEYS: tuple[str, ...] = tuple(OuterSampleProxies.model_fields)

# Here because the `promptpotter` connector declares it too and may not import `application/runner`.
INNER_RESULT_KEY = "final_ranking"
# The facts no term names (the θ series and its reason): columns a reader shows, never a ranked term.
INNER_FACTS_KEY = "inner"


def _term(name: str, direction: str, description: str, **rest: object) -> CellTerm:
    return CellTerm.model_validate(
        {"name": name, "direction": direction, "description": description, **rest}
    )


# The `promptpotter` connector's cell vocabulary (`Connector.cell_terms`).
INNER_CELL_TERMS: tuple[CellTerm, ...] = (
    _term("floored", "low", "1 where the optimizer prompt spoiled the inner campaign, else 0."),
    _term(
        "held_out_lift",
        "high",
        "The inner campaign's mean lift over its origin on its held-out rows.",
    ),
    _term("held_out_origin", "high", "The inner origin's headline on the held-out rows."),
    _term("held_out_final_lift", "high", "The held-out lift the inner campaign ended on."),
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
    "origin": "origin_level",
    "final_lift": "final_lift",
    "peak_lift": "peak_lift",
    "rounds": "rounds_ran",
    "round_budget": "round_budget",
    "cost": "sent_usd",
    "tokens": "tokens",
}
assert set(_FACT_OF_KEY.values()) <= set(InnerCellFacts.model_fields)

# The per-row matrix the held-out area reads (`BenchCurve.rows`), banked whole on the cell's row.
BENCH_ROWS_KEY = "bench_rows"

# What the connector reports beside its terms (`Connector.reported_keys`).
INNER_REPORTED_KEYS: tuple[str, ...] = (
    INNER_FACTS_KEY,
    BENCH_ROWS_KEY,
    "held_out_lift_se",
    "cost",
    "tokens",
)
assert {*OUTER_PROXY_KEYS, *_FACT_OF_KEY, INNER_FACTS_KEY, BENCH_ROWS_KEY} == {
    *(t.name for t in INNER_CELL_TERMS),
    *INNER_REPORTED_KEYS,
}, "a reported key is no term, fact or proxy, or one went unreported"


def inner_cell_report(facts: InnerCellFacts, proxies: OuterSampleProxies) -> dict[str, object]:
    """An absent value is a key left out: a bound 0 would score a measurement nobody took."""
    dumped = facts.model_dump(mode="json")
    reported = {
        **proxies.model_dump(),
        **{key: dumped[field] for key, field in _FACT_OF_KEY.items()},
    }
    return {
        **{key: value for key, value in reported.items() if value is not None},
        INNER_FACTS_KEY: {k: v for k, v in dumped.items() if k not in _FACT_OF_KEY.values()},
    }


_INNER_REPORT_KEYS = frozenset({*(t.name for t in INNER_CELL_TERMS), *INNER_REPORTED_KEYS})


def read_inner_report(pipeline: PipelineData) -> None:
    """The recursion's report proven as its connector declares it, a no-op on any other row: what
    the archive's one reader (`measurement_archive.py::answer_of`) refuses, the landing drops."""
    if INNER_FACTS_KEY not in pipeline.cell_report:
        return
    if undeclared := sorted(pipeline.cell_report.keys() - _INNER_REPORT_KEYS):
        raise KeyError(f"the recursion's cell report declares no {', '.join(undeclared)}")
    inner_facts_of(pipeline)
    outer_proxies_of(pipeline)


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
    # A floored cell's report leaves its lift out.
    return OuterSampleProxies.model_validate({key: report.get(key) for key in OUTER_PROXY_KEYS})


def unread_rounds(result: CycleResult) -> list[int]:
    series = result.level_series
    return [] if series is None else [r for r in series.rounds if series.levels[r] is None]


def series_reason(result: CycleResult) -> ThetaCaveat | SeriesGap | None:
    """Why the search split's θ trajectory differences nothing; ``None`` where it does."""
    series = result.level_series
    if series is None:
        return SeriesGap.NEVER_STARTED
    if unread_rounds(result):
        return SeriesGap.UNREAD_ROUND
    for rnd in series.rounds:
        read = series.levels[rnd]
        if read is not None and withheld(read.caveat):
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
    curve = result.curve
    bench_origin = None if curve is None else curve.origin
    bench_last = curve.levels[-1] if curve is not None and curve.levels else None
    return InnerCellFacts(
        origin_level=origin,
        final_lift=None if origin is None else levels[-1] - origin,
        peak_lift=None if origin is None else max(levels) - origin,
        mean_round_delta=None if origin is None else sum(padded) / len(padded) - origin,
        series_reason=series_reason(result),
        rounds_ran=result.n_rounds_after_origin,
        round_budget=result.round_budget,
        bench_origin_level=bench_origin,
        bench_final_lift=(
            None if bench_origin is None or bench_last is None else bench_last - bench_origin
        ),
        sent_usd=result.spend.sent_usd if result.spend else None,
        tokens=result.spend.total_tokens_used if result.spend else None,
    )


def _held_forward(levels: Sequence[float], result: CycleResult) -> list[float]:
    """Padded forward to the ROUND BUDGET: a mean over the series length is a different estimand per cell."""
    if not levels:
        return []
    budget = max(result.round_budget, len(levels))
    return [*levels, *([levels[-1]] * (budget - len(levels)))]


def parent_level_series(result: CycleResult) -> list[float]:
    held = parent_levels(result)
    return [] if held is None else _held_forward(held[1], result)


def _spoiled(rnd: RoundOutcome) -> bool:
    """No proposal reached a cell, whatever the reply's failure: an empty panel is spoiled too."""
    return all(arm.invalid for arm in rnd.proposed.values())


class AbortOwner(StrEnum):
    """Who an ``OPTIMIZER_ABORT`` belongs to: read off what its line filed, never the abort's words."""

    PROMPT = "prompt"
    UPSTREAM = "upstream"


def abort_owner(rounds: Sequence[RoundResult]) -> AbortOwner:
    """UPSTREAM where a round its line spent filed a cell the configuration does not answer for
    (`MeasuredCell.hole`): a fault no framing refinement fixes, whatever the abort said."""
    faulted = any(
        cell.facts.hole for rnd in rounds for sheet in rnd.cells.values() for cell in sheet
    )
    return AbortOwner.UPSTREAM if faulted else AbortOwner.PROMPT


def floor_reason(result: CycleResult, rounds: Sequence[RoundResult]) -> str | None:
    """Optimizer-prompt-OWNED, so FLOORED rather than excluded: it is reproducible, hence evidence.
    *rounds* are the rounds its line spent (`bench/line.py::LineSpan.own`)."""
    if result.stop_reason is StopReason.OPTIMIZER_ABORT:
        if abort_owner(rounds) is AbortOwner.UPSTREAM:
            return None
        return floor_words(result.stop_reason, len(rounds))
    if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
        return None
    if rounds and all(_spoiled(r) for r in rounds):
        return floor_words(result.stop_reason, len(rounds))
    return None


def floor_words(stop: StopReason, spent: int) -> str:
    """A floor in words, worded at the read off its run (its stop and the rounds its line spent),
    never filed with the cell: `floor_reason` asks the abort first, so a floored abort is one."""
    if stop is StopReason.OPTIMIZER_ABORT:
        return "its optimizer prompt terminated the inner campaign (optimizer_abort)"
    return (
        f"no proposal of its {spent} L1 round(s) reached a cell: a reply that yielded none, or "
        "only invalid edits"
    )


def compute_outer_proxies(result: CycleResult, rounds: Sequence[RoundResult]) -> OuterSampleProxies:
    """A floor is scored by the formula reading `floored`, never by a lift nobody measured. Past
    it, a SUCCESS ``StopOutcome`` or an upstream-owned abort is a measurement, its area read over
    the rounds it ran: any other cut-short run reads flawless to every aggregate."""
    if floor_reason(result, rounds) is not None:
        return OuterSampleProxies(floored=1, held_out_lift=None, held_out_lift_se=None)
    outcome = stop_reason_outcome(result.stop_reason)
    if outcome is not StopOutcome.SUCCESS and result.stop_reason is not StopReason.OPTIMIZER_ABORT:
        # The inner cycle forwarded its own spend onto the outer ledger as it ran.
        raise CellUnscoreableError(
            f"it did not end on its own terms — {outcome} (stop_reason={result.stop_reason}); "
            "its trajectory was cut short by something the optimizer prompt does not own",
            spent={},
        )
    curve = result.curve
    state = result.bench.status.state if curve is None else curve.state
    if curve is None or curve.area is None:
        raise CellUnscoreableError(
            f"its held-out area reads {state}: {READING_STATE_INFO[state].sentence}", spent={}
        )
    held = curve.area.estimate
    return OuterSampleProxies(floored=0, held_out_lift=held.value, held_out_lift_se=held.se)


class PanelPrecision(FrozenModel):
    """How sharply each cell was measured, against how far apart the cells landed."""

    # In headline points on the held-out rows, never fitness: a cell's precision cannot cross the
    # user-editable scoring formula. sqrt(mean over cells of (se_variant² + se_origin²)): the
    # spread if every cell had ONE true value.
    estimation_sd: float
    # Sample SD (n−1) of the per-cell paired diffs. Contains `estimation_sd` plus any real spread.
    observed_sd: float
    n_cells: int
    # No RATIO of the two is served: a clamped share rounds noise above the total spread into "100% noise".


def _lift(pipeline: PipelineData) -> float | None:
    held = outer_proxies_of(pipeline)
    return None if held is None else held.held_out_lift


def _lift_se(pipeline: PipelineData) -> float | None:
    held = outer_proxies_of(pipeline)
    return None if held is None else held.held_out_lift_se


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
    # Two reads, not one tuple: an estimate can carry its lift and no SE.
    v_lift, o_lift = cell_values(variant_rows, _lift), cell_values(origin_rows, _lift)
    v_se, o_se = cell_values(variant_rows, _lift_se), cell_values(origin_rows, _lift_se)
    cells = sorted(v_lift.keys() & o_lift.keys() & v_se.keys() & o_se.keys())
    if len(cells) < 2:
        return None
    observed = sample_sd([v_lift[c] - o_lift[c] for c in cells])
    assert observed is not None
    estimation = (sum(v_se[c] ** 2 + o_se[c] ** 2 for c in cells) / len(cells)) ** 0.5
    return PanelPrecision(estimation_sd=float(estimation), observed_sd=observed, n_cells=len(cells))
