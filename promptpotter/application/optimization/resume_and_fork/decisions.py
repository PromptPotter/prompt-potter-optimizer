"""``RESUME_CHECKPOINT_GATING`` is the sole source for ``REPLAYED`` vs ``ARCHIVAL``; every kind must appear exactly
once or import fails. **Adding a kind is two edits in one commit**, plus a replayer if it is ``REPLAYED``."""

from __future__ import annotations

import enum
from typing import Any, Protocol

from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord

__all__ = [
    "RESUME_CHECKPOINT_GATING",
    "GatingMode",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "record_decision",
]


class GatingMode(enum.StrEnum):
    """Whether a decision kind drives resume-divergence checking. ``REPLAYED`` is re-derived under the active scorer and a
    mismatch halts or forks; ``ARCHIVAL`` is never compared."""

    REPLAYED = "replayed"
    ARCHIVAL = "archival"


# Single source of truth for which kinds are divergence-gated. Every
# ``ResumeCheckpointKind`` member MUST appear here exactly once. ``REPLAYED`` kinds
# also need a registered replayer (see :mod:`.replayers`); ``ARCHIVAL``
# kinds must NOT have one.
RESUME_CHECKPOINT_GATING: dict[ResumeCheckpointKind, GatingMode] = {
    ResumeCheckpointKind.ROUND_WINNER: GatingMode.REPLAYED,
    ResumeCheckpointKind.ELIMINATION_CUT: GatingMode.REPLAYED,
    ResumeCheckpointKind.LEADER_LOCK_IN: GatingMode.REPLAYED,
    # A layer trigger is a FOLD over the cycle's escalation history, not a function of
    # one round's measurements — the counter bumps once per escalation *request*, resets
    # on every fire, and compares against the best-at-entry snapshot taken at the last
    # fire (`EscalationFSM.observe_l2_escalation`). A replayer is pure over
    # `ReplayContext` (one round + the origin), so that fold is not expressible there;
    # declaring these REPLAYED once forced a re-derivation on a substrate the loop never
    # ran. Their scorer-dependence is entirely mediated by `improved`, hence by the round
    # measurements — which ARE replayed above, so a scorer change that would move a
    # trigger already shows up as a winner/cut divergence in the same round.
    ResumeCheckpointKind.L2_ESCALATION_TRIGGER: GatingMode.ARCHIVAL,
    ResumeCheckpointKind.L3_ESCALATION_TRIGGER: GatingMode.ARCHIVAL,
    # Panel coverage re-derives INVARIANTLY under everything replay varies, so replaying it
    # could only ever confirm itself. Replay re-runs the SCORER over stored rows, and
    # rescoring never turns an errored row into a measured one — the hole count is a fact
    # about which rows exist, not about how they score. Two further facts make it
    # unreachable as a REPLAYED kind even in principle: the gate halts before
    # ``persist_round``, so on the round that matters the record only ever reaches the
    # ledger and never ``round_data.decisions`` — the only thing ``replay_decisions``
    # walks. Registering a replayer here would install a guard that cannot fire. What
    # recovers a holed round is ``repair_incomplete_rounds``, which re-measures the cells
    # and then forces this walk so the kinds that CAN move are re-derived against the
    # repaired rows.
    ResumeCheckpointKind.PANEL_COVERAGE: GatingMode.ARCHIVAL,
    # Fork is observable from the parent's history (the FORK_CUT record in
    # the parent ledger names the new cycle id and the offset that the
    # fork inherits from). It's archival because the fork's identity is
    # downstream of the divergence-checked decisions, not part of the
    # gating itself — replaying it can't re-derive a different fork.
    ResumeCheckpointKind.FORK_CUT: GatingMode.ARCHIVAL,
}


# Adding a kind without choosing REPLAYED/ARCHIVAL is a programming error;
# fail at import rather than at first replay attempt.
_unmapped = [k for k in ResumeCheckpointKind if k not in RESUME_CHECKPOINT_GATING]
if _unmapped:
    raise RuntimeError(
        f"ResumeCheckpointKind members missing from RESUME_CHECKPOINT_GATING: {_unmapped}"
    )
del _unmapped


class _DecisionSink(Protocol):
    """Anything with ``append(ResumeCheckpointRecord) -> Any`` — list[ResumeCheckpointRecord] or CycleEventLog."""

    def append(self, decision: ResumeCheckpointRecord, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    kind: ResumeCheckpointKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
    round: int | None = None,
) -> Any:
    sink.append(
        ResumeCheckpointRecord(
            kind=kind,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
            round=round,
        )
    )
    return outcome
