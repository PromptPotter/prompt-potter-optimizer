"""``DerivedView`` — typed-record dispatch for ledger subscribers, owning the routing in ONE place so a
new record subtype touches one file. Default hooks are no-ops."""

from __future__ import annotations

from typing import get_args

from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CommandAckRecord,
    CommandRecord,
    CycleRecord,
    CycleSeedRecord,
    ElectionRecord,
    ErrorRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ResumeCheckpointRecord,
    RoundWarningRecord,
    RulerRecord,
    SnapshotRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)

__all__ = ["DerivedView"]


# Record → the hook it routes to, or ``None`` for one no projection folds. It replaced an
# ``isinstance`` chain, which could only express the first half: a record naming no arm fell off
# the end and was dispatched NOWHERE, in silence. That is not hypothetical — ``ElectionRecord``
# sat here unrouted while a second channel covered for it, so the origin folded uncrowned on its
# only arm and no surface said so. The `None` arms are the same statement made deliberately, and
# the raise below makes an unanswered record impossible rather than merely discouraged.
_ROUTES: dict[type, str | None] = {
    PhaseRecord: "_handle_phase",
    SnapshotRecord: "_handle_snapshot",
    ResumeCheckpointRecord: "_handle_decision",
    TokenUsageRecord: "_handle_token_usage",
    LLMCallStartRecord: "_handle_llm_call_start",
    LLMCallProgressRecord: "_handle_llm_call_progress",
    LLMCallRecord: "_handle_llm_call",
    ErrorRecord: "_handle_error",
    RoundWarningRecord: "_handle_round_warning",
    CandidateMintedRecord: "_handle_candidate_minted",
    ElectionRecord: "_handle_election",
    # Applied at the seam that wrote them (`middleware/command_dispatcher.py`), which answers the
    # caller inline; the ledger pair is the audit trail, not an input to any view.
    CommandRecord: None,
    CommandAckRecord: None,
    # Read once, by a scan, at the moment it is needed: the cycle seed at the runner seam
    # (`scan_ledger_cycle_seed`) and the δ rulers on resume (`scan_ledger_rulers`). Folding either
    # continuously would hold a second copy of a fact one reader wants once.
    CycleSeedRecord: None,
    RulerRecord: None,
    # Banked by `store/account_spend.py` before a delete takes the rows it stands for — a fact
    # about a cycle that no longer exists, so no live view of one can hold it.
    SpendTombstoneRecord: None,
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


class DerivedView:
    #: The ``Cut`` this view is folded to. A fold that materializes itself STAMPS this, which is
    #: the only way a state on disk can say which moment it is of. ``-1`` = nothing folded yet.
    at_offset: int = -1

    def on_record(self, record: CycleRecord, offset: int) -> None:
        self.at_offset = offset
        hook = _ROUTES.get(type(record))
        if hook is not None:
            getattr(self, hook)(record)

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
