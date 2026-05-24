"""Typed records for the run ledger. Frozen Pydantic, `record_type` discriminator for JSON round-trip.

The `CycleRecord` discriminated union lives here so the data shape stays with the domain;
resume-checkpoint policy (gating, helpers, exhaustiveness check) lives in
`application/optimization/resume_and_fork/decisions.py`.
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
    "LLMCallProgressRecord",
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
    """Ledger-decision kinds. Adding a member: also extend `RESUME_CHECKPOINT_GATING` in
    `resume_and_fork.decisions` or the import-time exhaustiveness check fails.
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
    """In-flight live-state snapshot — `event` discriminates (sample_started/scored,
    candidate_started/scored); `payload` carries the full data the consumer needs.
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
    """LLM token + cost telemetry → `dashboard.json::spend`.

    `kind` splits into `backend` (pipeline) vs `loop` (optimizer) so the dashboard shows
    `Backend $X • Loop $Y`. `cost_usd` comes from the provider wire (OpenRouter `usage.cost`);
    otherwise resolved via `shared/spend.py`'s rate table — token counts always present for fallback.
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
    """In-flight marker appended BEFORE the SDK call — drives `dashboard.json::in_flight` so a
    multi-minute optimizer call isn't a frozen UI. Pairs with `LLMCallRecord` via `call_id` (hex).
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    # Total prompt chars — sets the operator's latency expectation on the in-flight line
    # (12k-char l1_generate vs 800-char l3_plan look very different).
    prompt_chars: int = 0
    timestamp: str = Field(default_factory=_utcnow_iso)


class LLMCallProgressRecord(BaseModel):
    """Heartbeat appended every `HEARTBEAT_INTERVAL_S` while the SDK call is blocked, so the CLI +
    `in_flight.elapsed_s` show a live counter. Cache replays short-circuit before the heartbeat starts.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    timestamp: str = Field(default_factory=_utcnow_iso)


class LLMCallRecord(BaseModel):
    """Full I/O of one optimizer LLM call — ledger-resident so `round_NNNN.json::nodes` is derived,
    not sidecar persisted. `payload_kind` distinguishes a real call from a synthesized one (replay,
    where messages/response/usage are absent and only input/output are populated).
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    # Opaque action-dict shape consumed by AuditTrailView — new fields don't churn the schema.
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utcnow_iso)


# Discriminated union by `record_type`. Keep the order alphabetical — hash-keyed test snapshots
# go stale otherwise.
CycleRecord = Annotated[
    ResumeCheckpointRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PhaseRecord
    | SnapshotRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


class ForkTrigger(enum.StrEnum):
    """Why a fork was minted — one value per caller of :func:`_mint_fork`."""

    OPERATOR_SWEEP = "operator_sweep"
    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    L2_REBASE = "l2_rebase"
    L3_REBASE = "l3_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkPayload(BaseModel):
    """Why + what-changed at a fork cut → `FORK_CUT.data.fork`. Optional delta fields
    (today: `l1_layout`) populated only by triggers that carry that change.
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
