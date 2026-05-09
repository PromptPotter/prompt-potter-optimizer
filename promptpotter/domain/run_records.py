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
    "HumanReviewRecord",
    "LLMCallRecord",
    "PhaseRecord",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "SnapshotRecord",
    "SweepPayload",
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
    """A campaign-phase boundary event (round-start, l2-fired, baseline-complete, …)."""

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
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    # The action-dict shape that AuditTrailView currently consumes;
    # carries every field _action_to_node_block reads (template_fields,
    # variables, template_name, messages, response, usage, model, config,
    # duration_s, cached, …). Stored as an opaque mapping so adding a
    # field doesn't churn the record schema.
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


class HumanReviewRecord(BaseModel):
    """Operator review event — L2-equivalent fields supplied by a human.

    Written to the ledger when ``Session.hitl_mode`` is on and the operator
    has produced a response file at the round's pause point. The payload
    parallels the auto-L2 output shape: ``task_context`` refinement, optional
    ``l1_layout`` edits, optimizer-param tweaks. **Pipeline_params are NOT a
    valid payload field** — those belong to L1's surface (see
    ``promptpotter/CLAUDE.md`` on layer ownership).

    ``mode`` distinguishes:

    - ``"every_round"`` — HITL on, escalation off; operator runs as L2 every
      round.
    - ``"l2_review"`` — HITL on, escalation on; operator reviews/corrects
      L2's auto-proposal after it fires.

    ``accepted_auto_proposal`` is True when the operator's response file was
    empty (interpreted as "accept the auto-L2 output verbatim"). When False,
    the operator's payload supersedes (or augments) L2's output.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["human_review"] = "human_review"
    round: int
    mode: Literal["every_round", "l2_review"]
    accepted_auto_proposal: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    bundle_path: str | None = None
    response_path: str | None = None
    timestamp: str = Field(default_factory=_utcnow_iso)


# Discriminated union — Pydantic uses ``record_type`` to pick the right model
# when parsing a dict back into a CycleRecord (e.g. when iterating a ledger
# from disk). Keep the order alphabetical so hash-keyed test snapshots are
# stable across additions.
CycleRecord = Annotated[
    HumanReviewRecord
    | ResumeCheckpointRecord
    | LLMCallRecord
    | PhaseRecord
    | SnapshotRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


class SweepPayload(BaseModel):
    """Operator sweep candidate — L1-surface override applied at fork bootstrap.

    One JSON file per candidate under ``datasets/{name}/sweep/``. Parsed by
    the sweep batch orchestrator; ``apply_sweep_payload_to_osp`` stamps the
    deltas onto a fresh fork's ``OptSearchPoint`` before the round loop runs.
    Field set is the same L1-surface owned by L2 (see
    ``OptSearchPoint.l1_layout``); operator authors a payload by hand to
    test a specific L1-prompt hypothesis without firing L2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = ""
    l1_layout: dict[str, list[str]] | None = None
