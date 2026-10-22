from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import BenchScore, BenchSubject
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import LiveCandidate, RoundSummary, RunLimits
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.phases import DashboardState, RunPhase, StopReason
from promptpotter.domain.results import DisplayMetric, RunStanding
from promptpotter.domain.round_audit import LoopWarning, NodeBlock
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_records import DefaultsDrift, RunWiringRecord
from promptpotter.domain.scoring import fitness_bands
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel


class CatchUpLogEntry(StrictModel):
    """One race catch-up: the priors eliminator ``member`` re-measured on one sample."""

    member: str
    round: int
    arm: ArmKey
    # The arm's label as the round names it.
    label: str
    position: int
    prior_ids: list[ArmKey]


class BackendWarning(StrictModel):
    """A backend transport or 5xx retry; a 429 is ``backpressure``, never one of these."""

    ts: str
    kind: str
    attempt: int | None = None
    max_attempts: int | None = None
    wait_s: float | None = None
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str | None = None
    detail: str | None = None


class DashboardError(StrictModel):
    """The structured failure of a stop whose ``STOP_REASON_INFO`` row is FAILED, else absent."""

    kind: str
    message: str
    stop_reason: StopReason
    label: str
    next_step: str


class RacingBlock(StrictModel):
    """The round's standing in its eliminator ``member``'s race."""

    member: str
    current_id: str
    n_samples: int
    top: list[dict[str, Any]]


class BenchPassProgress(StrictModel):
    """The held-out pass in flight; its rows are no round's cells."""

    subject: BenchSubject
    arm: ArmKey = Field(description="The arm the pass grades — the join to its row.")
    label: str = Field(description="That arm's label, to render.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    round: int = Field(description="The round whose selection is graded; 0 is the origin.")
    rows: int = Field(description="Bench rows the pass sends.")
    scored: int = Field(description="Rows of it scored so far.")
    accuracy: float | None = Field(
        default=None,
        description="The gateway's running accuracy over the rows scored; null before the first.",
    )


class CurrentRound(StrictModel):
    """The round in flight, rebuilt whole on every persist."""

    round: int = 0
    active_node: str | None = None
    measurement_node: str | None = None
    candidates: list[LiveCandidate] = Field(default_factory=list)
    fitness_bands: list[int] = Field(
        description="How many scored rows across `candidates` graded into each tenth of [0, 1], "
        "lowest first, banded as a closed round's `fitness_bands` is."
    )
    nodes: dict[str, NodeBlock] = Field(default_factory=dict)
    racing: RacingBlock | None = None

    @classmethod
    def unentered(cls) -> CurrentRound:
        return cls(fitness_bands=fitness_bands(()))


class DashboardFacts(StrictModel):
    model_config = ConfigDict(validate_assignment=False)

    campaign_id: str
    cycle_id: str

    # ``-1`` until the first record lands; the SSE snapshot parks its tail one past it.
    at_offset: int = -1

    langfuse_trace_url: str | None = None

    state: DashboardState = DashboardState.INIT
    optimizer_step: str | None = None
    state_since: str

    stop_reason: StopReason | None = None

    round: int = 0
    # The arm the oldest open cell is walked for; ``None`` between walks and on a pass of no arm.
    measuring_arm: ArmName | None = None
    run_standing: RunStanding | None = None

    rounds: list[RoundSummary] = Field(default_factory=list)

    # As the run last DECLARED it; the served body reads it anew (`served_dashboard`).
    bench_score: BenchScore | None = None
    bench_pass: BenchPassProgress | None = None
    composite_fitness_formula: str | None = None
    display_metric: DisplayMetric
    elects_on: DisplayMetric

    degraded_count: int = 0
    error_count: int = 0

    backend_retry_count: int = 0
    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)
    recent_loop_warnings: list[LoopWarning] = Field(default_factory=list)

    total_queries_scored: int = 0
    total_backend_calls: int = 0

    current_query_payload: str | None = None
    open_positions: list[int] = Field(default_factory=list)

    sample_lookahead_discards: int = 0
    in_flight: int = 0
    lookahead_allowed: int = 0
    # Between phases it is the NEXT round's; ``None`` under an optimizer declaring no `arms_per_round`.
    lookahead_most: int | None = 0
    # ``None``: no book binds.
    cell_reserve_usd: float | None = None
    waiting_on: str | None = None
    backpressure: BackpressureReading | None = None
    # `1` says the look-ahead control does not apply.
    max_cells_in_flight: int
    measured_unit: MeasuredUnit

    # ``None`` = no time recorded; a cached replay's 0.0 is a real reading.
    last_query_elapsed_s: float | None = None
    # The timestamp of the record at `at_offset`.
    at_time: str

    arms_per_round: int | None
    sp_budget_round: int

    # As DECLARED; what binds now is ``ServedDashboard.run_limits``.
    run_limits: RunLimits
    defaults_drift: DefaultsDrift | None = Field(
        default=None,
        description="Set where the bench defaults or the treatment the campaign's declaration is a "
        "delta over moved since it was declared, under an unchanged origin: this launch runs "
        "today's values.",
    )

    catch_up_log: list[CatchUpLogEntry] = Field(default_factory=list)

    current_round: CurrentRound = Field(default_factory=CurrentRound.unentered)

    error: DashboardError | None = None


class LiveDashboardState(DashboardFacts):
    # Says "running" forever after a kill: the answer is ``ServedDashboard.run_phase``.
    declared_phase: RunPhase = RunPhase.RUNNING

    current_position: int | None = None
    # The last close's; served only as each `ServedRound`'s readings of it.
    level_series: LevelSeries | None = None
    # A PLAN: an eliminator can stop a candidate before the tail is reached.
    declared_sample_order: list[int] = Field(default_factory=list)

    waiting_since: float | None = None

    # ``None``: no book binds.
    lookahead_affordable: int | None = None

    spend: SpendRollup = Field(default_factory=SpendRollup)

    # PER ROUND, and NOT clamped by a rewind, unlike `rounds[]`: a re-measured round costs twice.
    spend_by_round: dict[str, SpendRollup] = Field(default_factory=dict)

    @classmethod
    def declared(cls, hop: CycleHop, wiring: RunWiringRecord) -> LiveDashboardState:
        return cls(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            at_time=wiring.timestamp,
            **cls.launched(wiring),
        )

    @staticmethod
    def launched(wiring: RunWiringRecord) -> dict[str, Any]:
        """What each launch's wiring declares, and clears of what the last launch left."""
        return {
            "arms_per_round": wiring.arms_per_round,
            "sp_budget_round": wiring.sp_budget_round,
            "display_metric": wiring.display_metric,
            "elects_on": wiring.elects_on,
            "max_cells_in_flight": wiring.max_cells_in_flight,
            "measured_unit": wiring.measured_unit,
            "langfuse_trace_url": wiring.langfuse_trace_url,
            "run_limits": wiring.run_limits,
            "defaults_drift": wiring.defaults_drift,
            "state_since": wiring.timestamp,
            "stop_reason": None,
            "error": None,
            # A resumed run grades its selection again; the prior pass graded a stale one.
            "bench_score": None,
            "bench_pass": None,
            "current_query_payload": None,
            "current_position": None,
            "open_positions": [],
            "declared_sample_order": [],
        }
