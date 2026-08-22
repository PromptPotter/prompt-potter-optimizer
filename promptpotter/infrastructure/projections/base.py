"""``DerivedView`` — typed-record dispatch for ledger subscribers, owning the ``isinstance`` routing in ONE place so a
new record subtype touches one file. Default hooks are no-ops."""

from __future__ import annotations

from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CycleRecord,
    ElectionRecord,
    ErrorRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ResumeCheckpointRecord,
    RoundWarningRecord,
    SnapshotRecord,
    TokenUsageRecord,
)

__all__ = ["DerivedView"]


class DerivedView:
    #: Ledger offset of the last record folded — the ADDRESS of the state this view holds,
    #: and the same number as ``ProjectionEnvelope.sequence`` and ``RayItem.offset``. A fold
    #: that materializes itself STAMPS this, so a reader can ask which moment the file is
    #: of and a check can re-fold ``ledger[0..at_offset]`` against it. ``-1`` = nothing
    #: folded yet. It was discarded here (``del offset``) for as long as this class existed,
    #: which is why no materialized state on disk could say when it was true.
    at_offset: int = -1

    def on_record(self, record: CycleRecord, offset: int) -> None:
        self.at_offset = offset
        if isinstance(record, PhaseRecord):
            self._handle_phase(record)
        elif isinstance(record, SnapshotRecord):
            self._handle_snapshot(record)
        elif isinstance(record, ResumeCheckpointRecord):
            self._handle_decision(record)
        elif isinstance(record, TokenUsageRecord):
            self._handle_token_usage(record)
        elif isinstance(record, LLMCallStartRecord):
            self._handle_llm_call_start(record)
        elif isinstance(record, LLMCallProgressRecord):
            self._handle_llm_call_progress(record)
        elif isinstance(record, LLMCallRecord):
            self._handle_llm_call(record)
        elif isinstance(record, ErrorRecord):
            self._handle_error(record)
        elif isinstance(record, RoundWarningRecord):
            self._handle_round_warning(record)
        elif isinstance(record, CandidateMintedRecord):
            self._handle_candidate_minted(record)
        elif isinstance(record, ElectionRecord):
            self._handle_election(record)

    def _handle_phase(self, record: PhaseRecord) -> None: ...
    def _handle_election(self, record: ElectionRecord) -> None: ...
    def _handle_snapshot(self, record: SnapshotRecord) -> None: ...
    def _handle_decision(self, record: ResumeCheckpointRecord) -> None: ...
    def _handle_token_usage(self, record: TokenUsageRecord) -> None: ...
    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None: ...
    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None: ...
    def _handle_llm_call(self, record: LLMCallRecord) -> None: ...
    def _handle_error(self, record: ErrorRecord) -> None: ...
    def _handle_round_warning(self, record: RoundWarningRecord) -> None: ...
    def _handle_candidate_minted(self, record: CandidateMintedRecord) -> None: ...

    def drain(self) -> None:
        """Settle buffered state to disk on teardown, so the ledger's truth is mirrored even when no ``round:complete`` arrived.
        A no-op for projections that already flush every event."""
