from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchPass, BenchReading, BenchScore, BenchSubject
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.results import (
    ArmReading,
    DisplayMetric,
    RunStanding,
    VerifyPass,
    VerifyReading,
    VerifyStrategy,
)
from promptpotter.domain.spend import CeilingMeter
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "BenchEnterView",
    "BenchGradedView",
    "BenchScoredView",
    "CandidatesGeneratedView",
    "InitEnterView",
    "InitExitView",
    "MeasureEnterView",
    "OptimizerStepEnterView",
    "OptimizerStepExitView",
    "PhaseView",
    "RoundCompleteView",
    "RoundStartView",
    "RunSpendView",
    "SpDiffView",
    "VerifyEnterView",
    "VerifyGradedView",
    "ViewAnchors",
    "WarningEntry",
]


class _View(StrictModel):
    model_config = ConfigDict(frozen=True)


class ViewAnchors(_View):
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    display_metric: DisplayMetric = "accuracy"


class WarningEntry(_View):
    title: str
    detail: str


class InitEnterView(_View):
    """The formula rides here so the mask editor has one to reference DURING origin scoring."""

    kind: Literal["init_enter"] = "init_enter"
    warnings: tuple[WarningEntry, ...] = ()
    max_rounds: int = 0
    patience: int | None = None
    sp_budget_round: int = 0
    dataset_size: int = 0
    model: str = ""
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


class InitExitView(_View):
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


class RoundStartView(_View):
    """``propose:enter``; ``standing`` and ``note`` are the optimizer's own words (``RoundOpening``)."""

    kind: Literal["round_start"] = "round_start"
    node: str
    round: int
    max_rounds: int
    standing: str
    # The parent's level on the cells of the round it last ended.
    parent_accuracy: float | None
    prompt_preview: str
    arms: int | None
    note: str
    model: str
    run_standing: RunStanding | None = None


class SpDiffView(_View):
    columns: tuple[tuple[str, dict[str, str]], ...]
    node_param_keys: dict[str, list[str]] | None
    round_num: int | None
    clone_labels: tuple[str, ...]
    collapses: dict[str, int]
    proposer: str


class CandidatesGeneratedView(_View):
    """``propose:exit``."""

    kind: Literal["candidates_generated"] = "candidates_generated"
    n_candidates: int
    source: Literal["disk", "llm"]
    n_scoring_samples: int
    clone_labels: tuple[str, ...]
    sp_diff: SpDiffView


class MeasureEnterView(_View):
    kind: Literal["measure_enter"] = "measure_enter"
    node: str
    n_candidates: int
    n_samples: int


class BenchEnterView(_View):
    """Every ``sample_scored`` up to the matching ``exit`` is one of its rows; round 0 is the origin."""

    kind: Literal["bench_enter"] = "bench_enter"
    subject: BenchSubject
    label: str
    sp_hash: str
    round: int
    rows: int


class BenchScoredView(_View):
    kind: Literal["bench_scored"] = "bench_scored"
    bench: BenchScore


class BenchGradedView(_View):
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


class VerifyEnterView(_View):
    # `round` is the candidate's own, 0 for the origin.
    kind: Literal["verify_enter"] = "verify_enter"
    label: str
    round: int
    rows: int
    strategy: VerifyStrategy


class VerifyGradedView(_View):
    """The pass is the fact, the reading a cache of one grading of it."""

    kind: Literal["verify_graded"] = "verify_graded"
    verify_pass: VerifyPass
    reading: VerifyReading


class RunSpendView(_View):
    billed_usd: float
    rate_priced_usd: float
    incurred_usd: float
    meter: CeilingMeter
    metered_usd: float
    metered_tokens: int
    usd_cap: float | None
    token_cap: int | None


class RoundCompleteView(_View):
    """``select:exit``; the readout HOLDS it until the round closes: the panel gate can unwind it."""

    kind: Literal["round_complete"] = "round_complete"
    round: int
    # Less the proposals an invariant collapsed: they burned no call and would rank on a synthetic score.
    arms: tuple[ArmReading, ...]
    # WHOSE the four numbers below are: on a held round the retained parent, never the selected arm.
    ended_on: str
    # ``None`` where the round graded no cell on accuracy: every arm errored, or the backend is verifier-graded.
    accuracy: float | None
    composite_fitness: float | None
    evaluators: dict[str, float]
    total: int
    improved: bool
    verdict_reason: str | None
    composite_fitness_formula: str | None
    composite_fitness_formula_short: str | None
    # Carried, not read from config at render: a knob resolved in the renderer does not survive a disk round-trip.
    display_metric: DisplayMetric = "accuracy"


class OptimizerStepEnterView(_View):
    kind: Literal["optimizer_step_enter"] = "optimizer_step_enter"
    node: str
    activity: str
    title: str
    tag: str
    lines: tuple[str, ...]


class OptimizerStepExitView(_View):
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
