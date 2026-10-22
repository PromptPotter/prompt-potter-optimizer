from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import Field

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.results import (
    ArmReading,
    DegradationHealth,
    LineRate,
    OptimizerFact,
    OverlapReading,
    VerifyReading,
    arm_name,
    line_by_individual,
    overlap_line,
)
from promptpotter.domain.results_health import critical_health_title
from promptpotter.domain.ruler import AbilityReading, LevelSeries, SeriesReading
from promptpotter.domain.scoring import Grade, MeasuredCell, SampleStatus, is_hit
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.wounds import ValidationFailure
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.run_records import RoundClosedRecord


#: `noise`: the cells were measured no more sharply than they landed apart; `spread`: they differ.
PrecisionVerdict = Literal["noise", "spread"]


def precision_verdict(precision: PanelPrecision) -> PrecisionVerdict:
    return "noise" if precision.estimation_sd >= precision.observed_sd else "spread"


#: Against the last earlier round that measured any; `readded` was measured before, but not in it.
SampleMovement = Literal["new", "readded", "gained", "lost", "kept"]

SAMPLE_MOVEMENT_LABELS: dict[SampleMovement, str] = {
    "new": "new",
    "readded": "re-added",
    "gained": "gained position",
    "lost": "lost position",
    "kept": "kept position",
}
assert SAMPLE_MOVEMENT_LABELS.keys() == set(get_args(SampleMovement))


def sample_status(facts: MeasuredCell, grade: Grade) -> SampleStatus:
    """`ERR` and `UNSC` are asked BEFORE the grade: an errored row grades `fitness = 0.0`, which reads as a MISS."""
    if facts.errored:
        return "ERR"
    if grade.unscored is not None:
        return "UNSC"
    return "HIT" if is_hit(grade.fitness) else "MISS"


class DashboardSample(StrictModel):
    """One scored sample as `dashboard.json` serves it, display-trimmed and one shape in any state."""

    position: int = Field(
        description="The sample's slot in the bank the cycle drew, which diverges from the row's "
        "position in the walk once the hard-sample sorter drives the order.",
    )
    sample_key: str = Field(description="What the sample IS (`Sample.key`).")
    status: SampleStatus = Field(description="The grading verdict.")
    fitness: float | None = Field(
        default=None,
        description="The graded per-cell score `status` is the verdict OF — the same number "
        "`CellRow.fitness` carries, so a live cell shades a partial grade `status` rounds to "
        "HIT or MISS. Null on an errored row, which was never graded.",
    )
    terminal_node: str | None = Field(
        default=None,
        description="The deepest pipeline node the row reached; the tape badges it. Null where "
        "the row names none.",
    )
    cached: bool = Field(
        default=False,
        description="Measurement reused from a prior identical searchpoint, not a fresh call.",
    )
    cost_s: float | None = Field(
        default=None,
        description="Seconds producing this row COST, summed off `step_timings` — what the cell "
        "took when it was measured, which survives the cache stamp where a replay's own clock "
        "reads 0.0. Null where the row recorded no per-node timing.",
    )
    predicted: str = Field(
        default="",
        description="Prediction, trimmed for display. EMPTY on a verifier-graded row (see "
        "ground_truth) — the pair is both halves of a comparison nobody made there.",
    )
    ground_truth: str = Field(
        default="",
        description="Ground truth, trimmed for display. EMPTY declares a VERIFIER-GRADED row: "
        "the backend answered with a number its own verifier decided (a Harbor task's "
        "tests/test.sh, L4's outer proxies), so there is no truth for `predicted` to match and "
        "`status` carries the whole verdict. A client tells that from a broken extraction by the "
        "pair: both empty is verifier-graded, `NO_RESULT` beside a real truth is extraction.",
    )
    ground_truth_text: str = Field(
        description="`ground_truth` as a surface shows it: itself, or the one sentence for a "
        "verifier-graded row (`domain/scoring.py::ground_truth_text`)."
    )
    query: str = Field(default="", description="Query, trimmed for display.")
    input_tokens: int | None = Field(default=None)
    output_tokens: int | None = Field(default=None)
    cache_read_tokens: int | None = Field(
        default=None,
        description="How many of `input_tokens` the PROVIDER served off its own prefix cache — a "
        "SUBSET, never an addition, and distinct from `cached`, which says OUR archive answered. "
        "Null where no breakdown was reported; 0 where one was and there was no hit.",
    )


class DashboardCandidate(FrozenModel):
    """One candidate as `dashboard.json` serves it, the same shape live or closed."""

    reading: ArmReading


class LiveCandidate(DashboardCandidate):
    """A candidate of the round in flight, with its searchpoint, sample tape and rejection reason."""

    prompts: dict[str, NodePrompt] | None = Field(
        default=None,
        description="`OptSearchPoint.prompts`; with the resolved params, what a steered fork "
        "seeds from. Null until the candidate is announced.",
    )
    resolved_params: dict[str, Any] | None = None
    pipeline_overlay: dict[str, Any] | None = None
    samples: list[DashboardSample] = Field(default_factory=list)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    composite_fitness_formula_short: str | None = None


class OptimizerLimit(FrozenModel):
    """One knob an optimizer declares as bounding its run, which a fork may reconcile."""

    node: str
    knob: str
    label: str
    # ``None`` where the knob is declared off.
    value: float | None
    # A whole count; otherwise a fraction in [0, 1].
    integer: bool


class RunLimits(StrictModel):
    """The budget a launch declared, beside its optimizer's own bounds; a served dashboard lays
    the standing budget over it."""

    # Its money axes are the CAMPAIGN's ceiling, which every live cycle shares.
    budget: Budget = Budget()
    optimizer: list[OptimizerLimit] = Field(default_factory=list)


class RoundSummary(FrozenModel):
    """Top-level `accuracy` is what the round MEASURED; its θ is `ServedRound.level`."""

    round: int
    leading: ArmName | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the selection where the round selected, on a held round the challenger "
        "`verdict_reason` names as its best, at round 0 the origin. `panel_precision` is "
        "measured on it. Null where the selector could read no arm."
    )
    selected: list[ArmName] = Field(
        description="The arms the election crowned; empty on a round that held."
    )
    # ``None`` where the round measured nothing readable: the chart draws a GAP, never a point at zero.
    accuracy: float | None
    composite_fitness: float | None
    # Rows of the individual the round ENDED on: on a held round the retained parent, never the leading arm.
    total: int
    # Never add a `cumulative_accuracy`: a mean over DIFFERENT configurations is a score nobody scored.
    # ``None`` where no bench pass graded this round's selection.
    bench: BenchReading | None = None
    # Moves the stall counter and the life bank. Unset at round 0, which holds no election.
    improved: bool | None = None
    verdict_reason: str | None = None
    # The round's own arms: a reference it re-read is no row of it.
    candidates: list[DashboardCandidate] = Field(default_factory=list)
    # Measurement order, off the longest candidate sequence: PoBB truncates losers.
    selection: list[int] = Field(default_factory=list)
    # ``None`` only when the round measured zero samples. Rendered, never recomputed.
    health: DegradationHealth | None = None
    health_alert: str | None = Field(
        default=None,
        description="The alert a `critical` `health` raises, worded "
        "(`results_health.py::critical_health_title`); null on every other grade.",
    )
    # C0 and each new best since on ONE shared set of cells: the only basis two rounds can be differenced on.
    overlap: OverlapReading
    # ``None`` on a non-L4 round. The VERDICT rides `candidates[].reading.vs_reference`.
    panel_precision: PanelPrecision | None = None
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)

    @classmethod
    def of(cls, closed: RoundClosedRecord) -> RoundSummary:
        arms = closed.cells.arms
        longest = arms[max(arms, key=lambda k: (len(arms[k]), k))] if arms else []
        election, fold = closed.election, closed.fold

        def name(key: ArmKey) -> ArmName:
            return arm_name(key, closed.arms[key], closed.round)

        return cls(
            round=closed.round,
            leading=None
            if election is None or election.leading is None
            else name(election.leading),
            selected=[] if election is None else [name(key) for key in election.selected],
            accuracy=None if fold is None else fold.accuracy,
            composite_fitness=None
            if fold is None or fold.accuracy is None
            else fold.composite_fitness,
            total=0 if fold is None else fold.total,
            improved=None if closed.round == 0 else closed.improved,
            verdict_reason=None
            if closed.round == 0 or election is None
            else election.verdict_reason,
            candidates=[
                DashboardCandidate(reading=reading)
                for reading in closed.arm_readings()
                if reading.role != MeasurementRole.PARENT
            ],
            selection=list(dict.fromkeys(closed.positions[key] for key, _, _ in longest)),
            health=closed.health,
            health_alert=critical_health_title(closed.round, closed.health),
            overlap=closed.overlap,
            panel_precision=closed.panel_precision,
            optimizer_facts=closed.optimizer_facts,
        )


class ServedRound(RoundSummary):
    """A closed round as a dashboard serves it: the banked summary plus every reading derived from it."""

    overlap_line: list[LineRate] = Field(
        description="`overlap` as its members and rates, C0 first; empty where the lead was not "
        "read."
    )
    panel_precision_verdict: PrecisionVerdict | None = Field(
        description="Which lever `panel_precision` calls for, null where it is. Beside the "
        "reading and not on it: `PanelPrecision` is hashed into the L4 estimator's identity."
    )
    selection_movement: list[SampleMovement] = Field(
        description="How each sample of `selection` moved against the round before it that "
        "measured any, position for position with `selection` (`sample_movements`)."
    )
    level: AbilityReading | None = Field(
        description="This round's level on the cycle's `LevelSeries`, the one two rounds may "
        "difference: null where `series_reading` carries a caveat."
    )
    series_reading: SeriesReading | None = Field(
        description="That level as read, under the caveat that withholds it, for a surface that "
        "shows θ under its caveat; null where no scored cell sits on the scale."
    )


def sample_movements(
    selection: Sequence[int], previous: Sequence[int], seen: Collection[int]
) -> list[SampleMovement]:
    """*seen* is every sample an earlier round measured; a sample listed twice stands at its last position."""
    was = {sample_id: at for at, sample_id in enumerate(previous)}
    now = {sample_id: at for at, sample_id in enumerate(selection)}

    def movement(sample_id: int) -> SampleMovement:
        before = was.get(sample_id)
        if before is None:
            return "readded" if sample_id in seen else "new"
        at = now[sample_id]
        return "gained" if at < before else "lost" if at > before else "kept"

    return [movement(sample_id) for sample_id in selection]


def served_rounds(
    rounds: Sequence[RoundSummary],
    verify: Mapping[ArmKey, VerifyReading],
    series: LevelSeries | None,
) -> list[ServedRound]:
    ordered = sorted(rounds, key=lambda r: r.round)
    line = line_by_individual(
        next((r.overlap for r in reversed(ordered) if overlap_line(r.overlap)), None)
    )
    movements: list[list[SampleMovement]] = []
    previous: Sequence[int] = ()
    seen: set[int] = set()
    for closed in ordered:
        movements.append(sample_movements(closed.selection, previous, seen))
        if closed.selection:
            previous = closed.selection
            seen.update(closed.selection)
    return [
        ServedRound(
            **{name: getattr(closed, name) for name in RoundSummary.model_fields}
            | {
                "candidates": [
                    row.model_copy(
                        update={
                            "reading": row.reading.on_line(line).model_copy(
                                update={"verify": verify.get(row.reading.arm.key)}
                            )
                        }
                    )
                    for row in closed.candidates
                ]
            },
            overlap_line=overlap_line(closed.overlap),
            panel_precision_verdict=None
            if closed.panel_precision is None
            else precision_verdict(closed.panel_precision),
            selection_movement=movement,
            level=None if series is None else series.level(closed.round),
            series_reading=None if series is None else series.reading(closed.round),
        )
        for closed, movement in zip(ordered, movements, strict=True)
    ]
