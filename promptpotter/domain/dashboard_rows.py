from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.paired_reading import ArmPointer
from promptpotter.domain.results import (
    ArmReading,
    DegradationHealth,
    LineRate,
    OptimizerFact,
    OverlapReading,
    VerifyReading,
    line_by_individual,
    overlap_line,
)
from promptpotter.domain.ruler import AbilityReading, series_levels
from promptpotter.domain.scoring import Grade, MeasuredCell, SampleStatus, is_hit
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.wounds import ValidationFailure

__all__ = [
    "SAMPLE_MOVEMENT_LABELS",
    "DashboardCandidate",
    "DashboardSample",
    "LiveCandidate",
    "OptimizerLimit",
    "PrecisionVerdict",
    "RoundSummary",
    "RunLimits",
    "SampleMovement",
    "ServedRound",
    "precision_verdict",
    "sample_movements",
    "sample_status",
    "served_rounds",
]

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

    qi: int = Field(description="Iteration position within the candidate's walk — the #000 column.")
    sample_id: int | None = Field(
        default=None,
        description="Dataset sample id, which diverges from qi once the hard-sample sorter "
        "drives the order. Null where the row carries none.",
    )
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


class DashboardCandidate(StrictModel):
    """One candidate as `dashboard.json` serves it, the same shape live or closed."""

    model_config = ConfigDict(frozen=True)

    reading: ArmReading


class LiveCandidate(DashboardCandidate):
    """A candidate of the round in flight, with its searchpoint, sample tape and rejection reason."""

    # `OptSearchPoint.prompt_field_dict()` shape; with the resolved params, what a steered fork seeds from.
    prompt_fields: dict[str, Any] | None = None
    resolved_pipeline_params: dict[str, Any] | None = None
    pipeline_overlay: dict[str, Any] | None = None
    samples: list[DashboardSample] = Field(default_factory=list)
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    composite_fitness_formula_short: str | None = None


class OptimizerLimit(StrictModel):
    """One knob an optimizer declares as bounding its run, which a fork may reconcile."""

    model_config = ConfigDict(frozen=True)

    node: str
    knob: str
    label: str
    # ``None`` where the knob is declared off.
    value: float | None
    # A whole count; otherwise a fraction in [0, 1].
    integer: bool


class RunLimits(StrictModel):
    """The ceilings a launch declared; a served dashboard lays the standing operator ceiling over them."""

    max_rounds: int | None = None
    ceiling: SpendCeilings = SpendCeilings()
    optimizer: list[OptimizerLimit] = Field(default_factory=list)


class RoundSummary(StrictModel):
    """Top-level `accuracy` is what the round MEASURED; `ability` is the invariant series."""

    model_config = ConfigDict(frozen=True)

    round: int
    leading: ArmPointer | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the selection where the round selected, on a held round the challenger "
        "`verdict_reason` names as its best, at round 0 the origin. `panel_precision` is "
        "measured on it. Null where the selector could read no arm."
    )
    selected: list[ArmPointer] = Field(
        description="The arms the election crowned; empty on a round that held."
    )
    # ``None`` where the round measured nothing readable: the chart draws a GAP, never a point at zero.
    accuracy: float | None
    composite_fitness: float | None
    # Rows of the individual the round ENDED on: on a held round the retained parent, never the leading arm.
    total: int
    # Never add a `cumulative_accuracy`: a mean over DIFFERENT configurations is a score nobody scored.
    ability: AbilityReading | None = None
    # ``None`` where no bench pass graded this round's selection.
    bench: BenchReading | None = None
    # Moves the stall counter and the life bank. Unset at round 0, which holds no election.
    improved: bool | None = None
    verdict_reason: str | None = None
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


class ServedRound(RoundSummary):
    """A closed round as a dashboard serves it: the banked summary plus every reading derived from it."""

    ability_on_series_ruler: bool = Field(
        description="Whether `ability` is on the scale the cycle's θ series is drawn on, the "
        "first ruler a round stamped (`AbilityReading.comparable_to`)."
    )
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
    rounds: Sequence[RoundSummary], verify: Mapping[str, VerifyReading]
) -> list[ServedRound]:
    ordered = sorted(rounds, key=lambda r: r.round)
    levels = series_levels([r.ability for r in ordered])
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
                                update={"verify": verify.get(row.reading.arm.label)}
                            )
                        }
                    )
                    for row in closed.candidates
                ]
            },
            ability_on_series_ruler=level is not None,
            overlap_line=overlap_line(closed.overlap),
            panel_precision_verdict=None
            if closed.panel_precision is None
            else precision_verdict(closed.panel_precision),
            selection_movement=movement,
        )
        for closed, level, movement in zip(ordered, levels, movements, strict=True)
    ]
