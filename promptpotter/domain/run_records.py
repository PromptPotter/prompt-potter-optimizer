"""Typed records for the run ledger — facts about a campaign cycle.

The ledger spine in ``infrastructure/ledger.py`` accepts a ``CycleRecord``
union and fans out to projection writers. Each record subtype is a frozen
Pydantic model with a ``record_type`` discriminator so JSON round-trips
through the spine without ambiguity.

Resume-checkpoint policy (``RESUME_CHECKPOINT_GATING``, ``GatingMode``,
``record_decision`` helper, the import-time exhaustiveness check) lives
in :mod:`promptpotter.application.optimization.resume_and_fork.decisions`
— the data shape (``ResumeCheckpointKind`` + ``ResumeCheckpointRecord``) stays here so
the ``CycleRecord`` discriminated union owns it.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CycleRecord",
    "ForkPayload",
    "ForkTrigger",
    "LLMCallRecord",
    "LLMCallStartRecord",
    "OperatorSweepFile",
    "PhaseRecord",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "SnapshotRecord",
    "TokenUsageRecord",
]


class ResumeCheckpointKind(enum.StrEnum):
    """Kinds of decisions written to the ledger.

    Adding a member: append here AND extend ``RESUME_CHECKPOINT_GATING`` in
    ``resume_and_fork.decisions`` in the same commit. The registry test
    fails otherwise.
    """

    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"
    PROBE_ROUND_COMMITMENT = "probe_round_commitment"
    FORK_CUT = "fork_cut"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class ResumeCheckpointRecord(BaseModel):
    """One recorded decision: ``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["decision"] = "decision"
    kind: ResumeCheckpointKind
    inputs_ref: dict[str, Any] = Field(default_factory=dict)
    outcome: Any = None
    data: dict[str, Any] = Field(default_factory=dict)
    round: int | None = None
    timestamp: str = Field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        """Wire shape for round_data-JSON ``decisions`` payload."""
        return {
            "kind": self.kind.value,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }


class PhaseRecord(BaseModel):
    """A campaign-phase boundary event (round-start, l2-fired, origin-complete, …)."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


class SnapshotRecord(BaseModel):
    """An in-flight live-state snapshot — per-sample / per-candidate / per-round.

    Snapshots are display state for the live dashboard and per-sample log.
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


class TokenUsageRecord(BaseModel):
    """One LLM call's token + cost telemetry — fans into ``dashboard.json::spend``.

    ``kind`` splits the spend rollup into ``backend`` (in-pipeline LLM
    nodes) and ``loop`` (optimizer meta-prompts: l1/l2/l3/critique). The
    dashboard projection sums each bucket independently so the operator
    sees ``Backend $X • Loop $Y`` rather than a fused total.

    ``cost_usd`` is set when the provider returned USD on the wire
    (OpenRouter's ``usage.cost``); otherwise the projection resolves it
    via ``shared/spend.py``'s rate table. Token counts are always
    populated so the chip can fall back to a token-count display when
    no rate is on file for the model.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["token_usage"] = "token_usage"
    kind: Literal["optimizer", "backend"]
    node: str
    model: str | None = None
    input_tokens: int
    output_tokens: int
    duration_s: float = 0.0
    cost_usd: float | None = None
    round: int | None = None
    timestamp: str = Field(default_factory=_utcnow_iso)


class LLMCallStartRecord(BaseModel):
    """One optimizer LLM call's IN-FLIGHT marker — appended BEFORE the SDK call.

    Lets the live dashboard project an ``in_flight`` field on
    ``dashboard.json`` while a multi-minute optimizer call is still
    running, so the operator (and any AI reading the file) can see
    *which* call is in progress and *for how long*, instead of staring
    at a frozen UI for nine minutes.

    Pairs with :class:`LLMCallRecord` via ``call_id``; the live
    dashboard clears the in-flight slot when the paired completion
    record arrives. ``call_id`` is a hex string (caller-provided,
    typically a ``uuid4().hex``) so the ledger stays JSON-serializable
    without a custom encoder.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    timestamp: str = Field(default_factory=_utcnow_iso)


class LLMCallRecord(BaseModel):
    """One optimizer LLM call's full I/O — rendered prompt + parsed output.

    Persists every ``l1_generate`` / ``l1_critique`` / ``l2_context`` /
    ``l3_plan`` LLM call into the ledger so the round audit trail
    (``.runtime/cache/rounds/round_NNNN.json::nodes.<node>``) is a
    derived view, not a sidecar persistence channel. Call shape mirrors
    today's audit-trail action dict so :class:`AuditTrailView`
    can shape it into the ``nodes`` block without semantic loss.

    ``payload_kind`` distinguishes a real LLM call (carries
    ``messages``/``response``/``usage``) from a synthesized event (e.g.
    persisted-candidate replay where llm_call did not fire — carries
    only ``input``/``output`` fields).

    ``call_id`` pairs the record with a prior :class:`LLMCallStartRecord`
    so the live dashboard's in-flight projection can clear the slot.
    Empty string when no start record was emitted (synthesized calls,
    legacy replay).
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    # The action-dict shape that AuditTrailView currently consumes;
    # carries every field _action_to_node_block reads (template_fields,
    # variables, template_name, messages, response, usage, model, config,
    # duration_s, cached, …). Stored as an opaque mapping so adding a
    # field doesn't churn the record schema.
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


# Discriminated union — Pydantic uses ``record_type`` to pick the right model
# when parsing a dict back into a CycleRecord (e.g. when iterating a ledger
# from disk). Keep the order alphabetical so hash-keyed test snapshots are
# stable across additions.
CycleRecord = Annotated[
    ResumeCheckpointRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PhaseRecord
    | SnapshotRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


class ForkTrigger(enum.StrEnum):
    """Why a fork was minted — one value per caller of :func:`_mint_fork`.

    Three are wired today; the rest are M11 deliverables. Adding a trigger
    is an enum addition; the mint mechanism does not change.
    """

    OPERATOR_SWEEP = "operator_sweep"
    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    L2_REBASE = "l2_rebase"
    L3_REBASE = "l3_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkPayload(BaseModel):
    """Why + what-changed at a fork cut. Lands on ``FORK_CUT.data.fork``.

    Optional delta fields (today: ``l1_layout``) are populated only by
    triggers that carry that kind of change. M11 LLM-rebase emission adds
    its delta fields here when wiring lands; M10 keeps the surface to
    what's actually written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: ForkTrigger
    reason: str
    issued_by: str
    l1_layout: dict[str, list[str]] | None = None


class OperatorSweepFile(BaseModel):
    """Operator JSON shape under ``datasets/{name}/sweep/``. The dispatcher
    widens this into a ``ForkPayload(trigger=OPERATOR_SWEEP, ...)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = ""
    l1_layout: dict[str, list[str]] | None = None
