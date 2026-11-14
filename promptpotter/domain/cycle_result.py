from __future__ import annotations

from pydantic import Field

from promptpotter.domain.bench import BenchScore, OwnLevel
from promptpotter.domain.paired_reading import MeasurandUnit
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel


class CycleResult(StrictModel):
    rounds: list[RoundResult]
    # The LINE's, origin-exclusive: every round its cycles ran, a rebase's discarded ones included.
    n_rounds_after_origin: int
    result_fitness: float | None
    # The cycle scorer's: `result_fitness` and every fold read in it.
    fitness_unit: MeasurandUnit
    result_round: int
    # On its OWN round-0 rows. `None`, not a 0.0 level, on a cycle that never started.
    origin: OwnLevel | None
    # The individual each round ended on, never the proposals; `None` on a cycle that never started.
    level_series: LevelSeries | None
    # The held-out bench: the origin's headline on its rows, then per round the LINE spent and the
    # STANDING selection's. ``None`` is a round whose pass read nothing or was never sent.
    bench_origin_level: float | None = None
    bench_round_levels: list[float | None] = Field(default_factory=list)
    # The line's, from config, never the rounds that ran: `lives` stops a stalling cycle short.
    # ``None`` = no cap.
    round_budget: int | None = None
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: PipelineParams | None = None
    stop_reason: StopReason
    # A pause at the round allowance its launch was started with (``start-run {step_rounds}``).
    stepped: bool = False
    started_at: str
    finished_at: str
    cycle_id: str | None = None
    resumed_from_round: int = 1
    # ``None`` only on an init-crash before any observer wired up.
    spend: SpendRollup | None = None
    # Set where the stop's ``STOP_REASON_INFO`` row is FAILED (``runner/termination.py::end_run_on``).
    error: ErrorRecord | None = None
    bench: BenchScore
