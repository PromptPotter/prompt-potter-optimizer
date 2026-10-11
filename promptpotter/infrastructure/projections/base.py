from __future__ import annotations

from typing import get_args

from promptpotter.domain.run_records import (
    BackendWarningRecord,
    CandidateMintedRecord,
    CandidateScoredRecord,
    CandidateStartedRecord,
    CheckinClosedRecord,
    CommandAckRecord,
    CommandRecord,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeedRecord,
    CycleSupersededRecord,
    ElectionRecord,
    ErrorRecord,
    FlightRecord,
    ForkGradedRecord,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchReleasedRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    OptimizerStateRecord,
    PhaseRecord,
    PricedKeyRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundStandingRecord,
    RoundWarningRecord,
    RulerRecord,
    RunLimitsRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleOrderRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    ScoringLockedRecord,
    SpawnedRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)

__all__ = ["Projection"]


_ROUTES: dict[type, str | None] = {
    PhaseRecord: "_handle_phase",
    RunPhaseRecord: "_handle_run_phase",
    RunWiringRecord: "_handle_run_wiring",
    BackendWarningRecord: "_handle_backend_warning",
    RoundEnteredRecord: "_handle_round_entered",
    RoundClosedRecord: "_handle_round_closed",
    RoundStandingRecord: "_handle_round_standing",
    CandidateStartedRecord: "_handle_candidate_started",
    SampleOrderRecord: "_handle_sample_order",
    SampleStartedRecord: "_handle_sample_started",
    SampleScoredRecord: "_handle_sample_scored",
    CandidateScoredRecord: "_handle_candidate_scored",
    FlightRecord: "_handle_flight",
    RaceStandingRecord: "_handle_race_standing",
    RaceCatchUpRecord: "_handle_race_catch_up",
    ResumeCheckpointRecord: "_handle_decision",
    TokenUsageRecord: "_handle_token_usage",
    LLMCallStartRecord: "_handle_llm_call_start",
    LLMCallProgressRecord: "_handle_llm_call_progress",
    LLMCallRecord: "_handle_llm_call",
    ErrorRecord: "_handle_error",
    RoundWarningRecord: "_handle_round_warning",
    CandidateMintedRecord: "_handle_candidate_minted",
    ElectionRecord: "_handle_election",
    # Applied where written (`application/commands/dispatcher.py`); the pair is audit trail only.
    CommandRecord: None,
    CommandAckRecord: None,
    CycleSeedRecord: None,
    RulerRecord: None,
    RunLimitsRecord: None,
    ScoringLockedRecord: None,
    CycleMintedRecord: None,
    CheckinClosedRecord: None,
    CycleFinalRecord: None,
    CycleSupersededRecord: None,
    ForkGradedRecord: None,
    InterventionRecord: None,
    SpawnedRecord: None,
    LaunchClaimRecord: None,
    LaunchReleasedRecord: None,
    # Banked by `store/account_spend.py` for a cycle that no longer exists.
    SpendTombstoneRecord: None,
    PricedKeyRecord: None,
    # Read off the ledger by `spend_book.py`: money moves on the bill alone.
    SpendHoldRecord: None,
    RoundProposedRecord: None,
    OptimizerStateRecord: "_handle_optimizer_state",
}

_arms = frozenset(get_args(get_args(CycleRecord)[0]))
if frozenset(_ROUTES) != _arms:
    raise RuntimeError(
        "_ROUTES must answer for every CycleRecord arm — an unanswered one is dispatched "
        "nowhere and nothing says so: "
        f"missing {sorted(a.__name__ for a in _arms - frozenset(_ROUTES))}, "
        f"unbacked {sorted(a.__name__ for a in frozenset(_ROUTES) - _arms)}."
    )
del _arms


class Projection:
    #: The ``Cut`` folded to, stamped by a fold that materializes itself. ``-1`` = nothing folded yet.
    at_offset: int = -1

    def on_record(self, record: CycleRecord, offset: int) -> None:
        self.at_offset = offset
        hook = _ROUTES.get(type(record))
        if hook is not None:
            getattr(self, hook)(record)

    def _handle_phase(self, record: PhaseRecord) -> None: ...
    def _handle_run_phase(self, record: RunPhaseRecord) -> None: ...
    def _handle_run_wiring(self, record: RunWiringRecord) -> None: ...
    def _handle_backend_warning(self, record: BackendWarningRecord) -> None: ...
    def _handle_round_entered(self, record: RoundEnteredRecord) -> None: ...
    def _handle_round_closed(self, record: RoundClosedRecord) -> None: ...
    def _handle_round_standing(self, record: RoundStandingRecord) -> None: ...
    def _handle_election(self, record: ElectionRecord) -> None: ...
    def _handle_candidate_started(self, record: CandidateStartedRecord) -> None: ...
    def _handle_sample_order(self, record: SampleOrderRecord) -> None: ...
    def _handle_sample_started(self, record: SampleStartedRecord) -> None: ...
    def _handle_sample_scored(self, record: SampleScoredRecord) -> None: ...
    def _handle_candidate_scored(self, record: CandidateScoredRecord) -> None: ...
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
    def _handle_candidate_minted(self, record: CandidateMintedRecord) -> None: ...
    def _handle_optimizer_state(self, record: OptimizerStateRecord) -> None: ...

    def drain(self) -> None:
        """Settle buffered state on teardown; a no-op where every event already flushes."""
