from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import Field

from promptpotter.domain.arm_reading import ArmReading
from promptpotter.domain.arms import ArmState, Proposed
from promptpotter.domain.failures import ValidationFailure
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.layer_fold import Unbounded
from promptpotter.domain.opt_search_point import EvidenceGrounding, NodePrompt
from promptpotter.domain.origin_panel import (
    OverlapReading,
    RoundEnding,
    line_by_individual,
    overlap_line,
)
from promptpotter.domain.paired_reading import ArmKey, ArmName, Estimate, ReadingState
from promptpotter.domain.results import OptimizerFact
from promptpotter.domain.results_health import floor_said, health_reading
from promptpotter.domain.round_health import DegradationHealth, HealthGrade, HealthReading
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.said import Said, Tone
from promptpotter.domain.scoring import Grade, MeasuredCell, SampleStatus, is_hit
from promptpotter.domain.spend import (
    CAP_AXIS_INFO,
    CAP_BOOK_WORDS,
    CAP_CLAMP_WORDS,
    CEILING_METER_LABELS,
    Budget,
    CapAxis,
    CapClamp,
    CeilingMeter,
    SpendCount,
    usd_text,
)
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.verification import VerifyReading
from promptpotter.shared.clock import duration_words
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded

#: `noise`: the cells were measured no more sharply than they landed apart; `spread`: they differ.
PrecisionVerdict = Literal["noise", "spread"]

PRECISION_VERDICT_ADVICE: Worded[PrecisionVerdict, str] = Worded(
    {
        "noise": "The panel is re-reading its own noise — sharpen the cells before buying more of them.",
        "spread": "The cells genuinely differ — that spread is signal about where this optimizer "
        "prompt works.",
    }
)


def precision_verdict(precision: PanelPrecision) -> PrecisionVerdict:
    return "noise" if precision.estimation_sd >= precision.observed_sd else "spread"


#: Against the last earlier round that measured any; `readded` was measured before, but not in it.
SampleMovement = Literal["new", "readded", "gained", "lost", "kept"]

SAMPLE_MOVEMENT_LABELS: Worded[SampleMovement, str] = Worded(
    {
        "new": "new",
        "readded": "re-added",
        "gained": "gained position",
        "lost": "lost position",
        "kept": "kept position",
    }
)
assert SAMPLE_MOVEMENT_LABELS.keys() == set(get_args(SampleMovement))
#: The ink a movement is drawn in; a re-added sample reads as new.
SAMPLE_MOVEMENT_TONE: Worded[SampleMovement, Tone] = Worded(
    {
        "new": "attention",
        "readded": "attention",
        "gained": "success",
        "lost": "warn",
        "kept": "quiet",
    }
)


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


class ServedArm(DashboardCandidate):
    """One arm as every round read serves it, in flight (`current_round.candidates`) or filed
    (`RoundView.candidates`): its reading, its searchpoint and why it was refused."""

    prompts: dict[str, NodePrompt] | None = Field(
        default=None,
        description="`OptSearchPoint.prompts`; with the resolved params, what a steered fork "
        "seeds from. Null until the candidate is announced.",
    )
    resolved_params: dict[str, Any] | None = None
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    evidence_grounding: EvidenceGrounding | None = None

    @classmethod
    def of(cls, reading: ArmReading, arm: ArmState) -> ServedArm:
        return cls(
            reading=reading,
            prompts=arm.individual.prompts,
            evidence_grounding=arm.individual.lineage.evidence_grounding,
            resolved_params=None if isinstance(arm, Proposed) else arm.resolved_params,
            validation_failures=arm.validation_failures,
        )


class LiveCandidate(ServedArm):
    """A candidate of the round in flight, with its sample tape beside its served arm."""

    pipeline_overlay: dict[str, dict[str, Any]] | None = None
    samples: list[DashboardSample] = Field(default_factory=list)
    open_cells: list[int] = Field(
        default_factory=list,
        description="The positions of this arm's cells out now, oldest first: arms walking "
        "abreast each carry their own; empty between its walks.",
    )

    @classmethod
    def in_flight(
        cls,
        reading: ArmReading,
        arm: ArmState,
        *,
        samples: list[DashboardSample],
        open_cells: list[int],
    ) -> LiveCandidate:
        """The arm as `ServedArm.of` serves it, its overlay and tape beside it."""
        return cls(
            **dict(ServedArm.of(reading, arm)),
            pipeline_overlay=arm.overlay,
            samples=samples,
            open_cells=open_cells,
        )


class LineArm(DashboardCandidate):
    """One arm of a cycle's served line, a closed row and a live one in one shape."""

    address: str = Field(description="The candidate's address on the cycle that ran it.")
    open_cells: list[int] = Field(
        default_factory=list, description="`LiveCandidate.open_cells`; empty on a closed round."
    )


class OptimizerLimit(FrozenModel):
    """One knob an optimizer declares as bounding its run, which a fork may reconcile."""

    node: str
    knob: str
    label: str
    # ``None`` where the knob is declared off; ``unbounded`` where it binds nothing.
    value: float | Unbounded | None
    # A whole count; otherwise a fraction in [0, 1].
    integer: bool


class RunLimits(StrictModel):
    """A cycle's limits: its launch's wiring and every standing patch, folded in order of arrival
    (`RunLimitsProjection`)."""

    # Its money axes are the CAMPAIGN's ceiling, which every live cycle shares.
    budget: Budget = Budget()
    overshoot: bool = Field(
        default=False,
        description="Whether a call holds its node's mean billed cost in the run rather than its "
        "worst case, a bill past it drawn on the run's hold (`set-limits --overshoot`).",
    )
    optimizer: list[OptimizerLimit] = Field(default_factory=list)


class CapReading(FrozenModel):
    """One cap as it binds, read against its OWN book (`CAP_AXIS_INFO[axis].book`), every figure
    in the axis's wire unit."""

    axis: CapAxis
    asked: float | None = Field(
        description="What the operator asked; it differs from `armed` only where `clamped_by` "
        "says why. Null asked no cap."
    )
    armed: float | None = Field(description="The cap that binds; null is no cap.")
    used: float | None = Field(
        description="What the axis's book has used; null where it reads nothing now (no launch "
        "in flight for the clock, a replay)."
    )
    meter: CeilingMeter | None = Field(
        description="What a money axis's book counts; null on rounds and the clock."
    )
    count: SpendCount | None = Field(
        description="A money axis's book by part, the count its stop reads (`used` is its "
        "`counted`); null on rounds, the clock, and a replay."
    )
    fill: float | None = Field(
        description="`used` over `armed`, where both stand (a cap of 0 is full): the one meter a "
        "surface draws."
    )
    clamped_by: CapClamp | None

    @classmethod
    def of(
        cls,
        axis: CapAxis,
        *,
        asked: float | None,
        armed: float | None,
        used: float | None,
        meter: CeilingMeter | None,
        count: SpendCount | None,
    ) -> CapReading:
        if count is not None:
            used = getattr(count.counted, axis)
        fill = None if armed is None or used is None else used / armed if armed else 1.0
        return cls(
            axis=axis,
            asked=asked,
            armed=armed,
            used=used,
            meter=meter,
            count=count,
            fill=fill,
            clamped_by=None if asked is None or asked == armed else "allowance",
        )

    def lines(self) -> list[str]:
        """THE terminal wording of the row, the panel's in words: the cap and its clamp, then
        what its book has used."""
        info = CAP_AXIS_INFO[self.axis]
        head = f"{info.short.lower():<7}"
        cap = "no cap" if self.armed is None else _cap_words(self.axis, self.armed)
        if self.clamped_by is not None and self.asked is not None:
            cap = (
                f"armed {cap} (asked {_cap_words(self.axis, self.asked)}, "
                f"{CAP_CLAMP_WORDS[self.clamped_by]})"
            )
        book = [CAP_BOOK_WORDS[info.book]]
        used = [] if self.used is None else [f"{_cap_words(self.axis, self.used)} {info.used}"]
        if self.meter is not None:
            label = CEILING_METER_LABELS[self.meter]
            parts = None if self.count is None or self.axis != "usd" else self.count.words(label)
            book.append(parts or label)
        if self.clamped_by is None:
            return [head + " · ".join([cap, *used, *book])]
        return [head + cap, " " * len(head) + " · ".join([*used, *book])]


class ArmedCaps(FrozenModel):
    """Every cap of a cycle as it binds, in `CAP_AXIS_INFO` order: the dashboard's, and what
    `set-limits` answers at every door once it applied."""

    caps: list[CapReading]
    said: str = Field(description="The rows in words, one per line: the terminal's rendering.")

    @classmethod
    def of(cls, caps: list[CapReading]) -> ArmedCaps:
        return cls(caps=caps, said="\n".join(line for cap in caps for line in cap.lines()))


def _cap_words(axis: CapAxis, value: float) -> str:
    if axis == "usd":
        return usd_text(value)
    if axis == "wall_clock_s":
        return duration_words(value)
    return f"{int(value):,}" if axis == "rounds" else f"{int(value):,} tokens"


VerificationState = Literal["running", "done", "failed"]


class VerificationMark(FrozenModel):
    """The latest verify or bench pass on one arm, as its records left it."""

    kind: MeasurementRole
    arm: ArmName = Field(description="The arm the pass measures; `arm.key` joins it to its row.")
    state: VerificationState
    rows: int = Field(description="Rows the pass sends.")
    scored: int = Field(description="Rows of it scored so far.")
    level: Estimate | None = Field(
        description="Fitness over the rows scored: the running value, unbanded, while `running`; "
        "the pass's reading once `done`. Null before the first row, and on a `failed` pass, "
        "which reads nothing."
    )
    failed_as: ReadingState | None = Field(
        description="How a `failed` pass reads, so why it ended without a reading; null otherwise."
    )

    def scored_one(self, level: Estimate | None) -> VerificationMark:
        return self.model_copy(update={"scored": self.scored + 1, "level": level})

    def ended(
        self, *, scored: int, level: Estimate | None, failed_as: ReadingState | None
    ) -> VerificationMark:
        state: VerificationState = "done" if failed_as is None else "failed"
        return self.model_copy(
            update={
                "state": state,
                "scored": scored,
                "level": level if failed_as is None else None,
                "failed_as": failed_as,
            }
        )

    def orphaned(self) -> VerificationMark:
        """A pass whose process is gone before it was graded."""
        return self.ended(scored=self.scored, level=None, failed_as=ReadingState.PASS_STOPPED)


class RoundHealth(FrozenModel):
    """A round's degradation grade as a surface reads it, worded now off the facts its close stamped."""

    grade: HealthGrade
    reading: HealthReading | None = Field(
        description="Why the grade is not healthy, in words; null on `healthy` and on `unmeasured`."
    )
    floor: Said | None = Field(
        description="The origin read paired against its constant-answer floor, `warn` where the "
        "floor is above it; null on every later round and where answers are no repeated label."
    )

    @classmethod
    def of(cls, round_num: int, health: DegradationHealth) -> RoundHealth:
        floor = health.constant_floor
        return cls(
            grade=health.grade,
            reading=health_reading(round_num, health),
            floor=None if floor is None else floor_said(floor),
        )


class RoundSummary(FrozenModel):
    """A closed round as its arms, its election and its health read; each number is an arm's own."""

    round: int
    leading: ArmName | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the crown, the leader the line moved to, on a held round the challenger "
        "`verdict_reason` names as its best, at round 0 the origin. `panel_precision` is "
        "measured on it. Null where the selector could read no arm."
    )
    ending: RoundEnding | None = Field(
        description="How the round ended on `leading` (`Election.ending`); `adopted` at round 0; "
        "null where the round holds no election."
    )
    verdict_reason: str | None = None
    # The round's own arms: a reference it re-read is no row of it.
    candidates: list[DashboardCandidate] = Field(default_factory=list)
    # Measurement order, off the longest candidate sequence: PoBB truncates losers.
    selection: list[int] = Field(default_factory=list)
    health: RoundHealth
    # C0 and each new best since on ONE shared set of cells: the only basis two rounds can be differenced on.
    panel_reading: OverlapReading
    # ``None`` on a non-L4 round. The VERDICT rides `candidates[].reading.vs_reference`.
    panel_precision: PanelPrecision | None = None
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)


class ServedRound(RoundSummary):
    """A closed round as a dashboard serves it: the banked summary plus every reading derived from it."""

    panel_precision_verdict: PrecisionVerdict | None = Field(
        description="Which lever `panel_precision` calls for, null where it is."
    )
    selection_movement: list[SampleMovement] = Field(
        description="How each sample of `selection` moved against the round before it that "
        "measured any, position for position with `selection` (`sample_movements`)."
    )
    measured: list[int] = Field(
        description="The samples `selection` holds, each once, in id order."
    )
    hold_up: Estimate | None = Field(
        description="This round's promotion over the parent it beat, each read on every row it met "
        "in the cycle on the `LevelSeries`' one scale, its later rows as parent included: the lift "
        "that held beside the one it was crowned on. Null on a round that promoted nothing."
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


class SampleRounds(FrozenModel):
    sample_id: int
    rounds: int = Field(description="How many of the measuring rounds measured it")


class SampleCoverage(FrozenModel):
    """Every sample a closed round measured, in id order: the set a sample-set picker offers."""

    measuring_rounds: int = Field(description="The closed rounds that measured any sample")
    samples: list[SampleRounds]
    first_measured: list[int] = Field(
        description="The same samples in the order the rounds first measured them: a trajectory's columns."
    )

    @classmethod
    def of(cls, rounds: Sequence[RoundSummary]) -> SampleCoverage:
        measuring = [set(r.selection) for r in rounds if r.selection]
        counts = Counter(sample_id for ids in measuring for sample_id in ids)
        return cls(
            measuring_rounds=len(measuring),
            samples=[SampleRounds(sample_id=s, rounds=n) for s, n in sorted(counts.items())],
            first_measured=list(dict.fromkeys(s for r in rounds for s in r.selection)),
        )


def served_rounds(
    rounds: Sequence[RoundSummary],
    verify: Mapping[ArmKey, VerifyReading],
    series: LevelSeries | None,
) -> list[ServedRound]:
    ordered = sorted(rounds, key=lambda r: r.round)
    line = line_by_individual(
        next((r.panel_reading for r in reversed(ordered) if overlap_line(r.panel_reading)), None)
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
            panel_precision_verdict=None
            if closed.panel_precision is None
            else precision_verdict(closed.panel_precision),
            selection_movement=movement,
            measured=sorted(set(closed.selection)),
            hold_up=None
            if series is None or closed.ending is None or not closed.ending.moves_line
            else series.hold(closed.round),
        )
        for closed, movement in zip(ordered, movements, strict=True)
    ]
