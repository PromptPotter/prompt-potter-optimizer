from __future__ import annotations

import enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "CandidateMintedRecord",
    "CommandAckRecord",
    "CommandRecord",
    "ConfigOverrides",
    "CycleRecord",
    "CycleSeed",
    "CycleSeedRecord",
    "DecisionRecord",
    "ErrorRecord",
    "ForkSpec",
    "ForkTrigger",
    "LLMCallProgressRecord",
    "LLMCallRecord",
    "LLMCallStartRecord",
    "LedgerCandidate",
    "LedgerRoundClose",
    "OperatorSweepFile",
    "PhaseRecord",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "RoundWarningKind",
    "RoundWarningRecord",
    "SnapshotRecord",
    "TokenUsageRecord",
]


class ResumeCheckpointKind(enum.StrEnum):
    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    PANEL_COVERAGE = "panel_coverage"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"
    FORK_CUT = "fork_cut"


class DecisionRecord(TypedDict):
    """The serialized projection of :meth:`ResumeCheckpointRecord.to_dict`, read by the
    divergence-replay walker; ``kind`` is the enum VALUE so a round file needs no optimization layer."""

    kind: str
    inputs_ref: dict[str, Any]
    outcome: Any
    data: dict[str, Any]


class ResumeCheckpointRecord(StrictModel):
    """``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["decision"] = "decision"
    kind: ResumeCheckpointKind
    inputs_ref: dict[str, Any] = Field(default_factory=dict)
    outcome: Any = None
    data: dict[str, Any] = Field(default_factory=dict)
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    def to_dict(self) -> DecisionRecord:
        return {
            "kind": self.kind.value,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }


class PhaseRecord(StrictModel):
    model_config = ConfigDict(frozen=True)

    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    # In-memory-only carrier for the live ``RoundResult`` on ``round:display``
    # records. ``exclude=True`` keeps it off the persisted/streamed JSON — the
    # disk copy keeps only the lean ``payload['round_result']`` scalars (round,
    # accuracy, composite_fitness) the SSE→webapp chat reads. The fat per-sample
    # / per-candidate arrays already live in ``round_NNNN.json`` +
    # ``dashboard.json::rounds[]``; the ledger needs no third copy. Live
    # subscribers (LiveDashboardView, LiveDisplay) read this field; no disk
    # re-reader does. ``None`` on every record but ``round:display``.
    live_round_result: Any = Field(default=None, exclude=True, repr=False)
    # In-memory-only carrier for ``PhaseEvent.data`` — the live handles and bulk a phase
    # builder was called with. Same ``exclude=True`` rationale as the field above, and the
    # same audit: NO disk re-reader consumes it. The three ledger subscribers that need
    # phase state read the typed ``payload['view']``, which is capped and human-readable;
    # ``LiveDisplay`` alone reads this, for the ``env``/``state`` handles a resume-rewind
    # rebuild needs, and those exist only on the direct in-memory path anyway. Off disk it
    # also stops carrying the resolved dataset and a candidate's full prompt twice.
    data: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    timestamp: str = Field(default_factory=utcnow_iso)


class SnapshotRecord(StrictModel):
    model_config = ConfigDict(frozen=True)

    record_type: Literal["snapshot"] = "snapshot"
    event: str
    round: int
    candidate_idx: int | None = None
    candidate_total: int | None = None
    sample_idx: int | None = None
    sample_total: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class TokenUsageRecord(StrictModel):
    """EVERY call emits one, wire or cache — ``cached`` splits the BILL from what the search
    would cost cold. Collapsing them lets a replayed L4 arm read as infinitely efficient."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["token_usage"] = "token_usage"
    kind: Literal["optimizer", "backend"]
    node: str
    model: str | None = None
    provider: str | None = None
    """Who billed the call. Not decoration beside ``model``: a rate belongs to the PAIR,
    and the rate table registers the same model under many vendors at prices that differ
    several-fold, so a model alone cannot be priced (``shared/spend.py::lookup_rate``).
    ``None`` on a row written before this field, where only an exact key resolves."""
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    """How much of ``output_tokens`` the model spent thinking — a SUBSET of it, never a
    third total, because the provider bills the hidden trace as output. 0 for a
    non-reasoning model and for a provider that reports no breakdown, which is why it
    cannot be read as "this call did not think".

    It is here rather than beside the cost because the question it answers is *latency*,
    not money: an optimizer node whose answer is capped at ~1300 characters was measured
    billing 4790 completion tokens for 1044 of them, so ~94% of that call — and of its
    108 seconds — bought reasoning. The wire reported it on every call and only the
    parse-FAILURE path kept it, so the share was legible exactly when it was useless."""
    duration_s: float = 0.0
    cost_usd: float | None = None
    cached: bool = False
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallStartRecord(StrictModel):
    """Pairs with :class:`LLMCallRecord` via ``call_id``, so a multi-minute call does not
    look frozen."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    # Sets the operator's latency expectation on the in-flight line.
    prompt_chars: int = 0
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallProgressRecord(StrictModel):
    """Heartbeat while the SDK call is blocked; cache replays skip it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    # Optional live sub-status for the tick — the inner-campaign heartbeat
    # (``runner/inner/spawn.py``) sets it to ``"inner rX/Y · best Z%"`` so the
    # outer L4 chat/dashboard stay live while a multi-minute inner cycle runs.
    # ``None`` on ordinary optimizer heartbeats (unchanged behavior).
    detail: str | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(StrictModel):
    """Ledger-resident, so ``round_NNNN.json::nodes`` is derived rather than stored.
    ``payload_kind='synthesized'`` marks a replay whose messages/response/usage are absent."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    # Opaque action-dict consumed by AuditTrailView — new fields don't churn the schema.
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandRecord(StrictModel):
    """Sole writer at the API seam is ``CommandDispatcher``. Three target ledgers: the
    target cycle's, its campaign root's, or the workspace's — never a fourth."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command"] = "command"
    command_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    issued_by_user_id: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandAckRecord(StrictModel):
    """``effect`` is what the applier CHANGED, against ``CommandRecord.payload`` which is what
    was ASKED for; the two diverge whenever the applier gates or infers. Empty on rejection."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: Literal["applied", "rejected"]
    detail: str = ""
    effect: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ErrorRecord(StrictModel):
    """Emitted from the runner's three ``except`` sites via :func:`emit_error_record` over the
    ``_CYCLE_LEDGER`` ContextVar. Sole source of ``dashboard.json::error``."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["error"] = "error"
    # Exception class name — operator-facing diagnostic, never load-bearing for routing.
    kind: str
    # Operator-readable message picked at the throw site so no downstream
    # layer maps raw ``httpx``/Pydantic exception strings.
    message: str
    # Full Python traceback; set on ``CRASHED`` / ``RENDER_ERROR``, elided
    # on ``DIVERGED`` (resume-divergence is operator-recoverable, the message
    # alone carries the full diagnostic).
    traceback: str | None = None
    stop_reason: Literal["CRASHED", "RENDER_ERROR", "DIVERGED"]
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


# The closed set of self-healed degradations `emit_round_warning` can raise —
# one source so the record schema and the emit signature can't drift.
RoundWarningKind = Literal[
    "l1_zero_candidates",
    "injection_budget_overrun",
    "layer_parse_failure",
    "optimizer_deadline_retry",
]


class RoundWarningRecord(StrictModel):
    """Mid-round SELF-HEAL events, distinct from a fatal ``ErrorRecord``: the rails recover and the run continues, so stdout
    alone leaves the operator never knowing. Never re-add a kind with no emitter."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    # `error` = the round produced nothing usable (zero candidates); `warning`
    # = degraded but the round still progressed. Drives dashboard styling.
    severity: Literal["warning", "error"] = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkTrigger(enum.StrEnum):
    """One value per caller of :func:`_mint_fork`."""

    OPERATOR_SWEEP = "operator_sweep"
    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    OPERATOR_STEERED = "operator_steered"
    L2_REBASE = "l2_rebase"
    L3_REBASE = "l3_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkDirection(enum.StrEnum):
    """Which side of a cut the run CONTINUES on — the half a cut alone cannot say.

    One mechanism mints every fork, and the child is always the new cycle id. What differs
    is the reading. An operator exploring branches OFF a line that keeps running; a resume
    correcting itself moves the active pointer to the child and abandons what it cut from.
    Same shape on disk, opposite meaning to a reader — and without this, the lineage draws
    an offshoot and a supersession identically.
    """

    OFFSHOOT = "offshoot"
    """The CHILD is the branch. The parent stays the line it was."""

    SUPERSEDE = "supersede"
    """The CHILD is the continuation. The parent is what was left behind."""

    EQUIVALENT = "equivalent"
    """BOTH sides continue, identically — the cut changed nothing any reader saw.

    A correction has to cut before it writes, so the version it replaces survives; but
    whether the replacement matters is only knowable afterwards. When it turns out nothing
    downstream read a different word, the branch is not a dead end and drawing it as one
    invites pruning a line that is perfectly good. It is the same line twice, and both
    carry the same content forward. Measured, so it is the one direction a trigger cannot
    imply — it rides ``ForkSpec.direction``."""


# Derived from the trigger, never stored: every fork already on disk answers this from the
# trigger it recorded, so there is nothing to migrate and no second field to fall out of
# step. Exhaustiveness is checked at import (below) for the same reason
# ``RESUME_CHECKPOINT_GATING`` is — a new trigger must not land without an answer.
FORK_DIRECTION: dict[ForkTrigger, ForkDirection] = {
    # The operator is exploring beside a line that keeps its meaning: a sweep arm, a
    # diagnostic probe, a steered what-if. Nothing about the parent is invalidated.
    ForkTrigger.OPERATOR_SWEEP: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_DIAG: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_STEERED: ForkDirection.OFFSHOOT,
    # Each of these retargets the active pointer and abandons the tail it cut from — a
    # rewind by hand, a layer's rebase, or a resume finding the record no longer holds.
    # The parent keeps that tail as the record of what ran; the run is elsewhere now.
    ForkTrigger.OPERATOR_REWIND: ForkDirection.SUPERSEDE,
    ForkTrigger.L2_REBASE: ForkDirection.SUPERSEDE,
    ForkTrigger.L3_REBASE: ForkDirection.SUPERSEDE,
    ForkTrigger.SCORING_DIVERGENCE: ForkDirection.SUPERSEDE,
}

_undirected = [t for t in ForkTrigger if t not in FORK_DIRECTION]
if _undirected:
    raise RuntimeError(
        f"ForkTrigger members missing from FORK_DIRECTION: {_undirected}. A cut whose "
        "direction nobody declared renders as an offshoot, which is a lie half the time."
    )
del _undirected


class ConfigOverrides(StrictModel):
    """Every field optional — absent inherits the parent — applied to the fork's snapshot at
    init; it never mutates the parent's frozen config."""

    model_config = ConfigDict(frozen=True)

    max_rounds: int | None = None
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    l1_patience: int | None = None
    l2_patience: int | None = None
    l3_patience: int | None = None
    pobb_epsilon: float | None = None
    per_round_resubset: bool | None = None
    schema_field_rename: bool | None = None


class CycleSeed(StrictModel):
    """``pipeline_overlay`` merges ON TOP of the dataset overlay for this cycle only.
    ``origin_source`` is empty when the cycle recovers its origin by replay, which has no C0 to stamp."""

    model_config = ConfigDict(frozen=True)

    origin_prompt_fields: dict[str, Any] = Field(default_factory=dict)
    pipeline_overlay: dict[str, Any] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-fork search-space lock edits (param-key subset + "
        "allowed-values) — overrides the campaign's mint-time narrowing for this "
        "cycle only, the cycle-level peer of the campaign-wide "
        "`CampaignConfig.optimizer_narrowing`. Empty for an unedited fork or a "
        "campaign-from-origin seed.",
    )
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    origin_source: str = Field(
        default="",
        description=(
            "C0 lineage provenance — 'fork_seed' | 'campaign_origin'; empty when the "
            "seed carries no origin (an L2/L3 rebase replays its own)."
        ),
    )

    @model_validator(mode="after")
    def _origin_needs_provenance(self) -> CycleSeed:
        """An unstamped origin would ``KeyError`` deep inside init, so it fails here instead — at
        the boundary that built the seed."""
        if self.origin_prompt_fields and not self.origin_source:
            raise ValueError("origin_prompt_fields set without an origin_source stamp")
        return self


class CandidateMintedRecord(StrictModel):
    """Identity is not a measurement and must not share its durability: a cycle whose producer
    dies mid-flight must still leave its candidates nameable. ``label`` is MINTED, not read-time."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["candidate_minted"] = "candidate_minted"
    round: int
    idx: int
    candidate_id: str
    parent_id: str | None = None
    label: str
    changes_description: str = ""
    source: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


# What the cycle's OWN ledger can answer about a candidate. `minted` = named, not yet scored;
# `measured` = it has a number. Never `winner`: election is a round-close fact the ledger does
# not carry, so an unclosed round's candidates stay `measured` and nothing invents a winner.
CandidateState = Literal["minted", "measured"]


class LedgerCandidate(StrictModel):
    """Derived, not a record. Every field is copied verbatim from the snapshot BY NAME
    (``_SCORED_INCLUDE``), so declaring one here is all it takes to carry it."""

    model_config = ConfigDict(frozen=True)

    round: int
    idx: int
    candidate_id: str
    parent_id: str | None = None
    label: str
    changes_description: str = ""
    source: str = ""
    accuracy: float | None = None
    composite_fitness: float | None = None
    state: CandidateState = "minted"
    # The candidate's stored evaluator namespace — what a `score:` lens re-scores against.
    evaluators: dict[str, float] = Field(default_factory=dict)
    scored_samples: int | None = None
    expected_samples: int | None = None
    # ``None`` = minted, never measured; ``0`` = measured, nothing cached.
    cached_samples: int | None = None
    # The always-on whisker, over this candidate's own rows. A warm-ruler round OVERRIDES it
    # at close with the tighter θ-implied band (`LedgerRoundClose.abilities`).
    composite_ci_lo: float | None = None
    composite_ci_hi: float | None = None


class LedgerRoundClose(StrictModel):
    """The facts no candidate can know alone. ``winner_label`` and the ``abilities`` keys are
    POSITIONAL identity, never ``candidate_id`` — a resume re-mints a candidate under a fresh uuid."""

    model_config = ConfigDict(frozen=True)

    round: int
    winner_label: str = ""
    cumulative_theta: float | None = None
    cumulative_theta_se: float | None = None
    abilities: dict[str, dict[str, float]] = Field(default_factory=dict)


class CycleSeedRecord(StrictModel):
    """A fork inherits its parent's seed VIRTUALLY but appends its own, so a scan of one cycle's
    ledger returns that cycle's seed. Not a progress event — the SSE tail skips it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["cycle_seed"] = "cycle_seed"
    seed: CycleSeed
    timestamp: str = Field(default_factory=utcnow_iso)


# Discriminated union by `record_type`; keep order alphabetical — hash-keyed snapshots go stale otherwise.
CycleRecord = Annotated[
    ResumeCheckpointRecord
    | CandidateMintedRecord
    | CommandAckRecord
    | CommandRecord
    | CycleSeedRecord
    | ErrorRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PhaseRecord
    | RoundWarningRecord
    | SnapshotRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


# The `issued_by` value an operator fork carries when the client sent no
# identity. One SoT for both the stamp site (`mint_operator_fork`) and the
# lineage suppression that turns it into a *no* "edited by" badge — they must
# agree on the literal or the badge silently breaks.
UNATTRIBUTED_OPERATOR = "operator"


class ForkSpec(StrictModel):
    """The single fork-provenance model — there is no free-string ``fork.trigger`` twin."""

    model_config = ConfigDict(frozen=True)

    trigger: ForkTrigger
    reason: str
    issued_by: str
    from_round: int | None = None
    from_candidate_id: str | None = None
    l1_layout: dict[str, list[str]] | None = None
    seed: CycleSeed | None = None
    # The MEASURED direction, when the cut had to be taken before its consequence was
    # known. Only a correction needs it: it cuts first so the version it replaces survives,
    # then finds out whether the replacement reached anything. `None` ⇒ the trigger implies
    # the direction (`FORK_DIRECTION`), which is every other cut. An override, not a
    # fallback — a measured answer outranks a derived default, and only one of them exists
    # at a time.
    direction: ForkDirection | None = None


class RebaseRequest(StrictModel):
    """A policy change and a rewind are ONE move: the parent keeps its frozen config and its
    comparability, and the new axis is searched only on the sibling."""

    model_config = ConfigDict(frozen=True)

    fork_from_round: int
    trigger: ForkTrigger
    reason: str
    issued_by: str
    config_overrides: ConfigOverrides | None = None


class OperatorSweepFile(StrictModel):
    """Operator JSON under ``datasets/{name}/sweep/``; the dispatcher widens it to a ``ForkSpec``."""

    model_config = ConfigDict(frozen=True)

    reason: str = ""
    l1_layout: dict[str, list[str]] | None = None
