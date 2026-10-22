from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, get_args

from promptpotter.domain.bench import PartitionRecord
from promptpotter.domain.phases import ErrorRecord
from promptpotter.domain.run_records import (
    AccountEventRecord,
    ArmRecord,
    BackendWarningRecord,
    CommandAckRecord,
    CommandRecord,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeedRecord,
    CycleSupersededRecord,
    ElectionRecord,
    FlightRecord,
    ForkGradedRecord,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchClockRecord,
    LaunchReleasedRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    OptimizerStateRecord,
    PhaseRecord,
    PricedKeyRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    ResolvedRunRecord,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    ScoringLockedRecord,
    SpawnedRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
    TrailTombstoneRecord,
)

type RouteHandler = Callable[[Projection, Any], None]

_ARMS = frozenset(get_args(get_args(CycleRecord)[0]))


class Projection:
    #: The ``Cut`` folded to, stamped by a fold that materializes itself. ``-1`` = nothing folded yet.
    at_offset: int = -1
    _routes: ClassVar[dict[type[CycleRecord], RouteHandler | None]]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        routes = cls._route_table()
        if frozenset(routes) != _ARMS:
            raise RuntimeError(
                f"{cls.__name__}._route_table must answer for every CycleRecord arm — an "
                "unanswered one is dispatched nowhere and nothing says so: "
                f"missing {sorted(a.__name__ for a in _ARMS - frozenset(routes))}, "
                f"unbacked {sorted(a.__name__ for a in frozenset(routes) - _ARMS)}."
            )
        cls._routes = routes

    @classmethod
    def _route_table(cls) -> dict[type[CycleRecord], RouteHandler | None]:
        return {
            PhaseRecord: cls._handle_phase,
            RunPhaseRecord: cls._handle_run_phase,
            RunWiringRecord: cls._handle_run_wiring,
            BackendWarningRecord: cls._handle_backend_warning,
            ProviderHoldRecord: cls._handle_provider_hold,
            ProviderEasedRecord: cls._handle_provider_eased,
            RoundEnteredRecord: cls._handle_round_entered,
            RoundClosedRecord: cls._handle_round_closed,
            ArmRecord: cls._handle_arm,
            SampleStartedRecord: cls._handle_sample_started,
            SampleScoredRecord: cls._handle_sample_scored,
            FlightRecord: cls._handle_flight,
            RaceStandingRecord: cls._handle_race_standing,
            RaceCatchUpRecord: cls._handle_race_catch_up,
            ResumeCheckpointRecord: cls._handle_decision,
            TokenUsageRecord: cls._handle_token_usage,
            LLMCallStartRecord: cls._handle_llm_call_start,
            LLMCallProgressRecord: cls._handle_llm_call_progress,
            LLMCallRecord: cls._handle_llm_call,
            ErrorRecord: cls._handle_error,
            RoundWarningRecord: cls._handle_round_warning,
            ElectionRecord: cls._handle_election,
            RoundProposedRecord: cls._handle_round_proposed,
            OptimizerStateRecord: cls._handle_optimizer_state,
            # Applied where written (`application/commands/dispatcher.py`); the pair is audit trail only.
            CommandRecord: None,
            CommandAckRecord: None,
            CycleSeedRecord: None,
            PartitionRecord: None,
            ResolvedRunRecord: None,
            StandingBudgetRecord: None,
            ScoringLockedRecord: None,
            CycleMintedRecord: None,
            CycleFinalRecord: None,
            CycleSupersededRecord: None,
            ForkGradedRecord: None,
            InterventionRecord: None,
            SpawnedRecord: None,
            LaunchClaimRecord: None,
            LaunchClockRecord: None,
            LaunchReleasedRecord: None,
            # Banked by `store/account_spend.py` for a cycle that no longer exists.
            SpendTombstoneRecord: None,
            # Banked by `store/account_trail.py`, likewise.
            TrailTombstoneRecord: None,
            AccountEventRecord: None,
            PricedKeyRecord: None,
            # Read off the ledger by `spend_book.py`: money moves on the bill alone.
            SpendHoldRecord: None,
        }

    def on_record(self, record: CycleRecord, offset: int) -> None:
        self.at_offset = offset
        handler = self._routes[type(record)]
        if handler is not None:
            handler(self, record)

    def _handle_phase(self, record: PhaseRecord) -> None: ...
    def _handle_run_phase(self, record: RunPhaseRecord) -> None: ...
    def _handle_run_wiring(self, record: RunWiringRecord) -> None: ...
    def _handle_backend_warning(self, record: BackendWarningRecord) -> None: ...
    def _handle_provider_hold(self, record: ProviderHoldRecord) -> None: ...
    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None: ...
    def _handle_round_entered(self, record: RoundEnteredRecord) -> None: ...
    def _handle_round_proposed(self, record: RoundProposedRecord) -> None: ...
    def _handle_round_closed(self, record: RoundClosedRecord) -> None: ...
    def _handle_election(self, record: ElectionRecord) -> None: ...
    def _handle_arm(self, record: ArmRecord) -> None: ...
    def _handle_sample_started(self, record: SampleStartedRecord) -> None: ...
    def _handle_sample_scored(self, record: SampleScoredRecord) -> None: ...
    def _handle_flight(self, record: FlightRecord) -> None: ...
    def _handle_race_standing(self, record: RaceStandingRecord) -> None: ...
    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None: ...
    def _handle_decision(self, record: ResumeCheckpointRecord) -> None: ...
    def _handle_token_usage(self, record: TokenUsageRecord) -> None: ...
    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None: ...
    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None: ...
    def _handle_llm_call(self, record: LLMCallRecord) -> None: ...
    def _handle_error(self, record: ErrorRecord) -> None: ...
    def _handle_round_warning(self, record: RoundWarningRecord) -> None: ...
    def _handle_optimizer_state(self, record: OptimizerStateRecord) -> None: ...

    def drain(self) -> None:
        """Settle buffered state on teardown; a no-op where every event already flushes."""
