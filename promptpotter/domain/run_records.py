"""Typed records for the run ledger — facts about a campaign cycle.

The ledger spine in ``application/ledger.py`` accepts a ``RunRecord`` union and
fans out to projection writers. Each record subtype is a frozen Pydantic model
with a ``record_type`` discriminator so JSON round-trips through the spine
without ambiguity.

``DecisionKind`` is the enum that gates resume-divergence checking. Every
member must appear in ``DECISION_GATING``: ``REPLAYED`` kinds drive the
divergence walker, ``ARCHIVAL`` kinds are written for audit only. The pairing
is enforced by ``tests/test_decision_kinds_registry.py`` so a new kind cannot
land without an explicit gating choice.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DECISION_GATING",
    "Decision",
    "DecisionKind",
    "GatingMode",
    "Phase",
    "RunRecord",
    "Snapshot",
    "SweepPayload",
    "record_decision",
]


class DecisionKind(enum.StrEnum):
    """Kinds of decisions written to the ledger.

    Adding a member: append here AND extend ``DECISION_GATING`` in the same
    commit. The registry test fails otherwise.
    """

    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"
    PROBE_ROUND_COMMITMENT = "probe_round_commitment"
    FORK_CUT = "fork_cut"


class GatingMode(enum.StrEnum):
    """Whether a decision kind drives resume-divergence checking.

    REPLAYED: re-derived under the active scorer on resume; mismatch halts
    or forks. ARCHIVAL: archived only; never compared on resume.
    """

    REPLAYED = "replayed"
    ARCHIVAL = "archival"


# Single source of truth for which kinds are divergence-gated. Every
# ``DecisionKind`` member MUST appear here exactly once. ``REPLAYED`` kinds
# also need a registered replayer (see ``application/optimization/cycle.py``);
# ``ARCHIVAL`` kinds must NOT have one.
DECISION_GATING: dict[DecisionKind, GatingMode] = {
    DecisionKind.ROUND_WINNER: GatingMode.REPLAYED,
    DecisionKind.ELIMINATION_CUT: GatingMode.REPLAYED,
    DecisionKind.LEADER_LOCK_IN: GatingMode.REPLAYED,
    DecisionKind.L2_ESCALATION_TRIGGER: GatingMode.REPLAYED,
    DecisionKind.L3_ESCALATION_TRIGGER: GatingMode.REPLAYED,
    DecisionKind.PROBE_ROUND_COMMITMENT: GatingMode.ARCHIVAL,
    # Fork is observable from the parent's history (the FORK_CUT record in
    # the parent ledger names the new cycle id and the offset that the
    # fork inherits from). It's archival because the fork's identity is
    # downstream of the divergence-checked decisions, not part of the
    # gating itself — replaying it can't re-derive a different fork.
    DecisionKind.FORK_CUT: GatingMode.ARCHIVAL,
}


# Adding a kind without choosing REPLAYED/ARCHIVAL is a programming error;
# fail at import rather than at first replay attempt.
_unmapped = [k for k in DecisionKind if k not in DECISION_GATING]
if _unmapped:
    raise RuntimeError(f"DecisionKind members missing from DECISION_GATING: {_unmapped}")
del _unmapped


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class Decision(BaseModel):
    """One recorded decision: ``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["decision"] = "decision"
    kind: DecisionKind
    inputs_ref: dict[str, Any] = Field(default_factory=dict)
    outcome: Any = None
    data: dict[str, Any] = Field(default_factory=dict)
    round: int | None = None
    timestamp: str = Field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        """Wire shape for trial-JSON ``decisions`` payload."""
        return {
            "kind": self.kind.value,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }


class Phase(BaseModel):
    """A campaign-phase boundary event (round-start, l2-fired, baseline-complete, …)."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


class Snapshot(BaseModel):
    """An in-flight live-state snapshot — per-query / per-candidate / per-round.

    Snapshots are display state for the live dashboard and per-query log.
    The ``event`` discriminator says what the snapshot represents
    (``sample_started``, ``sample_scored``, ``candidate_started``,
    ``candidate_scored``); ``payload`` carries the full data the consumer
    needs (full result dict, full score report, query text, etc.).
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["snapshot"] = "snapshot"
    event: str
    round: int
    candidate_idx: int | None = None
    candidate_total: int | None = None
    sample_idx: int | None = None
    sample_total: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


# Discriminated union — Pydantic uses ``record_type`` to pick the right model
# when parsing a dict back into a RunRecord (e.g. when iterating a ledger
# from disk). Keep the order alphabetical so hash-keyed test snapshots are
# stable across additions.
RunRecord = Annotated[
    Decision | Phase | Snapshot,
    Field(discriminator="record_type"),
]


class _DecisionSink(Protocol):
    """Anything with ``append(Decision) -> Any`` — list[Decision] or RunLedger."""

    def append(self, decision: Decision, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    kind: DecisionKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
    round: int | None = None,
) -> Any:
    """Build a Decision and append to *sink*; return outcome for passthrough."""
    sink.append(
        Decision(
            kind=kind,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
            round=round,
        )
    )
    return outcome


class SweepPayload(BaseModel):
    """Operator sweep candidate — L1-surface override applied at fork bootstrap.

    One JSON file per candidate under ``datasets/{name}/sweep/``. Parsed by
    the sweep batch orchestrator; ``apply_sweep_payload_to_osp`` stamps the
    deltas onto a fresh fork's ``OptSearchPoint`` before the round loop runs.
    Field set is the same L1-surface owned by L2 (see
    ``OptSearchPoint.l1_section_overrides`` and peers); operator authors a
    payload by hand to test a specific L1-prompt hypothesis without firing
    L2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = ""
    directive: str | None = None
    l1_section_overrides: dict[str, bool] | None = None
    l1_section_overrides_text: dict[str, str] | None = None
    l1_template_override: str | None = None
