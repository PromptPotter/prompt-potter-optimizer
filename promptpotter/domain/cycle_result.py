from __future__ import annotations

from pydantic import Field

from promptpotter.domain.bench import BenchCurve, BenchScore, OwnLevel
from promptpotter.domain.paired_reading import MeasurandUnit
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import FrozenModel, StrictModel


class WallClock(FrozenModel):
    """CLOCK legs sum to ``elapsed_s``; ``worked_s`` is CALL time, so concurrency pushes it past."""

    # ``None`` where either endpoint is unparseable; every share below is then unanswerable too.
    elapsed_s: float | None
    # By ``CampaignPhase`` value; a bracket that never fired or never closed is ABSENT, never 0.0.
    phase_s: dict[str, float] = Field(default_factory=dict)
    # ``TokenUsageKind`` → node → summed call seconds; cached calls excluded.
    worked_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Same keys, in CLOCK: seconds held while no bracket and no gate was open.
    unbracketed_call_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Round (JSON key) → seconds to its FIRST close: the last is a ruler restamp or a rewind's re-run.
    round_ended_s: dict[str, float] = Field(default_factory=dict)
    # HUMAN time at a gate, never folded into a machine leg.
    gate_s: float = 0.0
    # ``elapsed_s`` minus every CLOCK leg.
    unattributed_s: float | None = None
    # ``None`` = no fresh cell was measured; 0.0 = the measured cells waited for nothing.
    unworked_s: float | None = None


class CycleResult(StrictModel):
    """What `run_cycle` returns and `CycleFinalRecord` banks whole. Its rounds are the ledger's
    closes, read back by their reader (`l4/proxies.py::floor_reason`), never banked twice."""

    # The LINE's, origin-exclusive: every round its cycles ran, a rebase's discarded ones included.
    n_rounds_after_origin: int
    result_fitness: float | None
    # The cycle scorer's: `result_fitness` and every fold read in it.
    fitness_unit: MeasurandUnit
    # The round the optimizer's DECLARED pick was selected in.
    result_round: int
    # On its OWN round-0 rows. `None`, not a 0.0 level, on a cycle that never started.
    origin: OwnLevel | None
    # The individual each round ended on, never the proposals; `None` on a cycle that never started.
    level_series: LevelSeries | None
    # The line's (`runner/bench.py::line_round_budget`), never the rounds that ran: `lives` stops a
    # cycle short. Uncapped, the rounds it spent.
    round_budget: int
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: PipelineParams | None = None
    stop_reason: StopReason
    started_at: str
    finished_at: str
    wall_clock: WallClock
    # `run_standing.py::round_clocks` over the cycle's own rounds past the origin.
    rounds_to_separable: int | None
    rounds_to_improved: int | None
    rounds_to_ceiling: int | None
    fitness_ceiling: float | None
    prompt_hashes: dict[str, str]
    scorer_id: str
    cycle_id: str | None = None
    resumed_from_round: int = 1
    # ``None`` only on an init-crash before any observer wired up.
    spend: SpendRollup | None = None
    # Set where the stop's ``STOP_REASON_INFO`` row is FAILED (``run_observers.py::end_run_on``).
    error: ErrorRecord | None = None
    bench: BenchScore
    # ``None`` where the line banked no origin pass: `bench.status` says why.
    curve: BenchCurve | None
