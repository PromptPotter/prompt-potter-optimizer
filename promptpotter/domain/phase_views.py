from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.arm_reading import ArmReading, DisplayMetric
from promptpotter.domain.bench import BenchPass, BenchReading, BenchScore, BenchSubject
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.paired_reading import ArmKey, ArmName, ReadingState
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.spend import CeilingMeter
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.verify_pass import VerifyPass, VerifyReading, VerifyStrategy


class ViewAnchors(FrozenModel):
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    display_metric: DisplayMetric = "accuracy"


class WarningEntry(FrozenModel):
    title: str
    detail: str


class InitEnterView(FrozenModel):
    """The formula rides here so the mask editor has one to reference DURING origin scoring."""

    kind: Literal["init_enter"] = "init_enter"
    warnings: tuple[WarningEntry, ...] = ()
    patience: int | None = None
    sp_budget_round: int = 0
    model: str = ""
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


class InitExitView(FrozenModel):
    """``resumed_from_round`` is the NEXT L1 round; ``cached_rounds_count`` counts round artifacts."""

    kind: Literal["init_exit"] = "init_exit"
    # ``None`` where the origin graded no cell on accuracy, as on an L4 outer cycle.
    origin_acc: float | None
    cycle_id_short: str
    samples: int
    bench_samples: int
    obs_on: bool
    origin_samples: int = 0
    resumed_from_round: int = 1
    cached_rounds_count: int = 0
    task_context_keys: int = 0
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


class RoundStartView(FrozenModel):
    """``propose:enter``; ``standing`` and ``note`` are the optimizer's own words (``RoundOpening``)."""

    kind: Literal["round_start"] = "round_start"
    node: str
    round: int
    # Null where no rounds axis binds; 0 is origin only.
    budget_rounds: int | None
    standing: str
    # The parent's level on the cells of the round it last ended.
    parent_accuracy: float | None
    prompt_preview: str
    arms: int | None
    note: str
    model: str
    run_standing: RunStanding | None = None


class SpDiffView(FrozenModel):
    columns: tuple[tuple[str, dict[str, str]], ...]
    node_param_keys: dict[str, list[str]] | None
    round_num: int | None
    clone_labels: tuple[str, ...]
    collapses: dict[str, int]
    proposer: str


class CandidatesGeneratedView(FrozenModel):
    """``propose:exit``."""

    kind: Literal["candidates_generated"] = "candidates_generated"
    n_candidates: int
    source: Literal["disk", "llm"]
    clone_labels: tuple[str, ...]
    sp_diff: SpDiffView


class MeasureEnterView(FrozenModel):
    kind: Literal["measure_enter"] = "measure_enter"
    node: str
    n_candidates: int
    n_samples: int


class BenchEnterView(FrozenModel):
    """Every ``sample_scored`` up to the matching ``exit`` is one of its rows; round 0 is the origin."""

    kind: Literal["bench_enter"] = "bench_enter"
    subject: BenchSubject
    arm: ArmKey
    label: str
    sp_hash: str
    round: int
    rows: int


class BenchScoredView(FrozenModel):
    kind: Literal["bench_scored"] = "bench_scored"
    bench: BenchScore


class BenchGradedView(FrozenModel):
    """``reading`` is ``None`` exactly where ``state`` is not ``read``."""

    kind: Literal["bench_graded"] = "bench_graded"
    subject: BenchSubject
    bench_pass: BenchPass
    tolerance: int
    # `None` on a selection's pass.
    reserve_usd: float | None
    reserve_tokens: int | None
    reading: BenchReading | None
    state: ReadingState
    label: str


class VerifyEnterView(FrozenModel):
    kind: Literal["verify_enter"] = "verify_enter"
    arm: ArmName
    rows: int
    strategy: VerifyStrategy


class VerifyGradedView(FrozenModel):
    """The pass is the fact, the reading a cache of one grading of it."""

    kind: Literal["verify_graded"] = "verify_graded"
    verify_pass: VerifyPass
    reading: VerifyReading


class RunSpendView(FrozenModel):
    """The caps bound the CAMPAIGN, so they pair with its metered spend; this cycle's sits beside."""

    billed_usd: float
    rate_priced_usd: float
    incurred_usd: float
    meter: CeilingMeter
    metered_usd: float
    metered_tokens: int
    campaign_metered_usd: float
    campaign_metered_tokens: int
    usd_cap: float | None
    token_cap: int | None
    # This cycle's part of the caps, what its run's book bound at the stop.
    usd_share: float | None
    token_share: int | None


class RoundCompleteView(FrozenModel):
    """``select:exit``; the readout HOLDS it until the round closes: the panel gate can unwind it."""

    kind: Literal["round_complete"] = "round_complete"
    round: int
    # In the round's rank, less the proposals an invariant collapsed: they burned no call.
    arms: tuple[ArmReading, ...]
    # WHOSE `fold` is: on a held round the retained parent, never the selected arm.
    ended_on: str
    fold: ScoreSummary | None
    improved: bool
    verdict_reason: str | None
    composite_fitness_formula: str | None
    composite_fitness_formula_short: str | None
    # Carried, not read from config at render: a knob resolved in the renderer does not survive a disk round-trip.
    display_metric: DisplayMetric = "accuracy"


class OptimizerStepEnterView(FrozenModel):
    kind: Literal["optimizer_step_enter"] = "optimizer_step_enter"
    node: str
    activity: str
    title: str
    tag: str
    lines: tuple[str, ...]


class OptimizerStepExitView(FrozenModel):
    """``headline`` is empty where the step adopted nothing."""

    kind: Literal["optimizer_step_exit"] = "optimizer_step_exit"
    headline: str
    details: tuple[str, ...]
    audit: tuple[str, str] | None


PhaseView = Annotated[
    InitEnterView
    | InitExitView
    | RoundStartView
    | CandidatesGeneratedView
    | MeasureEnterView
    | BenchEnterView
    | BenchScoredView
    | BenchGradedView
    | VerifyEnterView
    | VerifyGradedView
    | RoundCompleteView
    | OptimizerStepEnterView
    | OptimizerStepExitView,
    Field(discriminator="kind"),
]
