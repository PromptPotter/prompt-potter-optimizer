from __future__ import annotations

import enum
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.ruler import AbilityReading, DeltaRuler, ThetaCaveat
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
    "ElectionRecord",
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
    "view_fields",
]


class ResumeCheckpointKind(enum.StrEnum):
    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    PANEL_COVERAGE = "panel_coverage"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"
    FORK_CUT = "fork_cut"


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


class PhaseRecord(StrictModel):
    model_config = ConfigDict(frozen=True)

    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    # In-memory-only carrier for the live ``RoundResult`` on ``round:display`` records;
    # ``None`` on every other. ``exclude=True`` keeps it off the persisted/streamed JSON,
    # where the fat arrays already live in ``round_NNNN.json`` + ``dashboard.json::rounds[]``
    # and only live subscribers read this. No disk re-reader consumes it.
    live_round_result: Any = Field(default=None, exclude=True, repr=False)
    # ``PhaseEvent.data`` — the live handles and bulk a phase builder was called with. Same
    # ``exclude=True`` rationale and the same audit: no disk re-reader consumes it. Ledger
    # subscribers read the typed, capped ``payload['view']``; ``LiveDisplay`` alone reads this
    # for the ``env``/``state`` handles a resume-rewind rebuild needs.
    data: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    timestamp: str = Field(default_factory=utcnow_iso)


def as_view_mapping(view: Any) -> dict[str, Any]:
    """A phase view as a mapping, and the ONLY sanctioned way to read one.

    A ledger replay deserializes the view to a dict while the live in-process path still holds
    the frozen dataclass, so `getattr` works on one and silently returns the default on the
    other — reporting a fact that is present as absent. Lives beside `PhaseRecord` because both
    an `application/` reader and an `infrastructure/` projection need it; owning it in either
    would invert a layer."""
    if is_dataclass(view) and not isinstance(view, type):
        return asdict(view)
    return view if isinstance(view, dict) else {}


def view_fields(record: PhaseRecord) -> dict[str, Any]:
    """``payload["view"]`` as a mapping — the by-record arity of :func:`as_view_mapping`."""
    return as_view_mapping(record.payload.get("view"))


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
    several-fold, so a model alone cannot be priced (``shared/pricing.py::lookup_rate``).
    ``None`` on a row written before this field, where only an exact key resolves."""
    served_by: str | None = None
    """WHICH upstream host answered, where the one above is a GATEWAY that routes onward. The pair
    is the point: ``provider`` is who bills, this is whose silicon ran it, and hosts of one model
    disagree systematically. ``None`` where the provider is its own host."""
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    """How much of ``output_tokens`` the model spent thinking — a SUBSET of it, never a
    third total, because the provider bills the hidden trace as output. 0 for a
    non-reasoning model and for a provider that reports no breakdown, which is why it
    cannot be read as "this call did not think".

    It sits here rather than beside the cost because the question it answers is *latency*,
    not money: most of an optimizer call's wall clock is the hidden trace, and only this
    field makes that share legible on a SUCCEEDING call."""
    cache_read_tokens: int = 0
    """The part of ``input_tokens`` the PROVIDER served from its own prompt cache — a SUBSET, at a
    fraction of the input price. Not ``cached`` below, which says the call never reached a wire at
    all; and 0 also means "reported no breakdown", never "this prefix did not cache"."""
    cache_write_tokens: int = 0
    """The part of ``input_tokens`` billed at a premium to POPULATE that cache. A write is what
    makes the next read cheap, so all writes and no reads is paying for a prefix nothing collects."""
    duration_s: float = 0.0
    cost_usd: float | None = None
    cached: bool = False
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class SpendTombstoneRecord(StrictModel):
    """A deleted campaign's or cycle's spend, banked on the WORKSPACE ledger so the money outlives
    the data — without it the free-tier ceiling is re-earnable by deleting whatever you spent it on.
    It carries totals, not the rows: a chronology is unreadable once its cycle is gone."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["spend_tombstone"] = "spend_tombstone"
    campaign_id: str
    # Empty for a whole-campaign bank. The PAIR is the subject the re-bank guard keys on: banking
    # precedes the delete, so a crash between the two must not let a retry count the money twice.
    cycle_id: str = ""
    used_usd: float
    used_tokens: int
    unpriced_tokens: int
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
    # What `prompt_chars` is MADE OF — injection name → rendered chars, for the node's live
    # layout. The total alone says a producer-side bound failed but never which one, so the
    # over-budget warning could be read and not acted on; this is the breakdown that names the
    # panel. Empty for a node that composes no layout (`checkin`) and on a cache replay.
    injection_chars: dict[str, int] = Field(default_factory=dict)
    # What the node's ceiling REFUSED, per panel, in whole sections — and which layout panels had
    # nothing to say at all. `injection_chars` can only ever report what SURVIVED, so on its own it
    # cannot separate a panel the budget thinned from one that rendered short, nor a panel silent
    # every round of a campaign from one nobody put in the layout.
    injection_dropped: dict[str, int] = Field(default_factory=dict)
    injection_silent: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def refused_panels(self) -> list[str]:
        """Panels the composition refused WHOLE — dropped, with nothing surviving in
        ``injection_chars``. A THINNED panel showed less and said so; a refused one is absent, and
        the node reasons as though it had nothing to report."""
        return sorted(n for n in self.injection_dropped if n not in self.injection_chars)


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
    # The cycle STOPPED and a human has to act. Its reason is the layer's own sentence, and it
    # rides a warning rather than a log line so the why reaches disk with the halt.
    "layer_terminated_cycle",
    # A layer emitted `terminate_proposal` carrying no reason. Ignored — a contentless stop is a
    # volunteered field, not a decision — but never silently: it means the layer's schema let it
    # fill the kill switch with "" while its actual output was a healthy steer.
    "layer_terminate_blank",
    # `l1_generate` ran with its MANDATORY critique panel empty. The prior round either skipped
    # the distillation (it was the last of its invocation) or lost it to a terminal provider
    # failure, and the re-send at the next round's head failed too. The generator then rewrites a
    # prompt nothing told it how to fix, which is invisible on every other channel.
    "l1_critique_unavailable",
    # The odd one out, deliberately: nothing failed. The round measured cleanly and still
    # resolved nothing — no arm's blocked lift over the parent excluded 0 — which looks
    # identical to a decisive round on every other channel. Emitted by `l1/score/winner.py`.
    "round_not_separable",
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

    A correction cuts before it writes, so the version it replaces survives; whether the
    replacement matters is only knowable afterwards, and drawing an unchanged branch as a
    dead end invites pruning a line that is perfectly good. Measured, so it is the one
    direction a trigger cannot imply — it rides ``ForkSpec.direction``."""


# Derived from the trigger, never stored: every fork on disk answers this from the trigger it
# recorded, so there is nothing to migrate and no second field to fall out of step.
# Exhaustiveness is checked at import below — a new trigger must not land without an answer.
FORK_DIRECTION: dict[ForkTrigger, ForkDirection] = {
    # Exploring beside a line that keeps its meaning; nothing about the parent is invalidated.
    ForkTrigger.OPERATOR_SWEEP: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_DIAG: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_STEERED: ForkDirection.OFFSHOOT,
    # Each retargets the active pointer and abandons the tail it cut from. The parent keeps
    # that tail as the record of what ran; the run is elsewhere now.
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


# WHAT MINTED this cycle, as the operator reads it — mapped from the trigger like the direction
# above and checked the same way, so an unmapped trigger raises rather than badging as the
# operator's.
MintKind = Literal["session", "divergent_resume", "user_fork", "auto_rebase"]

MINT_KIND_FOR_TRIGGER: dict[ForkTrigger, MintKind] = {
    ForkTrigger.SCORING_DIVERGENCE: "divergent_resume",
    ForkTrigger.L2_REBASE: "auto_rebase",
    ForkTrigger.L3_REBASE: "auto_rebase",
    ForkTrigger.OPERATOR_SWEEP: "user_fork",
    ForkTrigger.OPERATOR_DIAG: "user_fork",
    ForkTrigger.OPERATOR_STEERED: "user_fork",
    ForkTrigger.OPERATOR_REWIND: "user_fork",
}

_unbadged = [t for t in ForkTrigger if t not in MINT_KIND_FOR_TRIGGER]
if _unbadged:
    raise RuntimeError(
        f"ForkTrigger members missing from MINT_KIND_FOR_TRIGGER: {_unbadged}. An unbadged "
        "trigger reads to the operator as a fork they made themselves."
    )
del _unbadged


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
    # The composite-fitness criterion (`CampaignConfig.scoring`). The one setting a mask can
    # PREVIEW against the record — a lens re-elects every round from rows already measured, so the
    # round it parts at is the round a fork carrying this is minted at. Every other field here moves
    # a ceiling or a patience count, which no measurement can be re-read under.
    scoring: str | dict[str, str] | None = None


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


# What the cycle's OWN ledger can answer about a candidate: `invalid` is rejected before it cost a
# sample, so its stored 0.0 is `INVALID_SCORES`' synthetic one. Never `winner` — election is a
# round-close fact the ledger does not carry, and an unclosed round must invent no crown.
CandidateState = Literal["minted", "measured", "invalid"]


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
    # The searchpoint id — the archive's `prompt_fields_id`, and the only key joining a node of
    # the served tree to the rows it paid for.
    sp_hash: str = ""
    # The candidate's stored evaluator namespace — what a `score:` lens re-scores against.
    evaluators: dict[str, float] = Field(default_factory=dict)
    scored_samples: int | None = None
    expected_samples: int | None = None
    # ``None`` = minted, never measured; ``0`` = measured, nothing cached.
    cached_samples: int | None = None
    # THE whisker, over this candidate's own rows — one band, one writer, no override.
    mean_fitness_ci_lo: float | None = None
    mean_fitness_ci_hi: float | None = None


class LedgerAbility(StrictModel):
    """One candidate's round-CLOSE numbers, keyed by LABEL in :class:`LedgerRoundClose`."""

    model_config = ConfigDict(frozen=True)

    theta: float | None = None
    theta_se: float | None = None
    # This ARM's own reason θ is not ability — only ever ``FLOOR_PINNED``. Rides the base rather
    # than :class:`LedgerFit` so the CLOSE carries it too: the tree prefers the close where it
    # answers, and a θ arriving without the caveat that voids it is the silent half of the state.
    theta_caveat: ThetaCaveat | None = None


class LedgerFit(LedgerAbility):
    """Everything the ELECTION stamps on one arm, keyed by LABEL in :class:`ElectionRecord`.

    A superset of :class:`LedgerAbility` rather than a sibling: the close RE-READS θ and nothing
    else, because the matched-parent floor is decided once, at the election, and never moves after
    it. Two models would put the same two fields under two names and let them drift."""

    matched_parent_accuracy: float | None = None
    matched_parent_composite: float | None = None
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None


class LedgerRoundClose(StrictModel):
    """The fit facts, RE-READ on every close — which is what lets round 0's second close carry the
    warm ruler's θ (``runner/loop.py``). The crown is on :class:`ElectionRecord` instead, because it
    never moves. ``abilities`` keys are POSITIONAL: a resume re-mints a candidate under a fresh uuid."""

    model_config = ConfigDict(frozen=True)

    round: int
    ability: AbilityReading | None = None
    abilities: dict[str, LedgerAbility] = Field(default_factory=dict)


class ElectionRecord(StrictModel):
    """What the round's ELECTION produced, at its own coordinate: ``elect_round_winner`` is the
    last thing ``l1_score`` does, so all of this exists a whole ``l1_critique`` call before the
    close it used to ride.

    ``fit`` is here for the same reason the crown is, and the argument that once kept it out —
    "already addressable in ``rounds/round_NNNN.json``" — is what this record now answers: that
    document is not addressable until the round CLOSES, two LLM calls after the election stamped
    it, and every live surface reads in that gap. Keyed by LABEL, like ``winner_label`` and like
    ``LedgerRoundClose.abilities``, because a resume re-mints candidate ids.

    ``winner_label`` empty = the round HELD; round 0 crowns the ``C0`` it adopted."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["election"] = "election"
    round: int
    winner_label: str = ""
    fit: dict[str, LedgerFit] = Field(default_factory=dict)
    # In-memory-only carrier for the live ``RoundResult``, the ``PhaseRecord.live_round_result``
    # shape and rationale: the round's OWN readings (``overlap``, the verdict, the electable count,
    # separability) have no other live carrier, and the fat arrays already live in
    # ``round_NNNN.json``. ``None`` on every replay off disk, which is why the fold guards on it.
    live_round_result: Any = Field(default=None, exclude=True, repr=False)
    timestamp: str = Field(default_factory=utcnow_iso)


class RulerRecord(StrictModel):
    """A δ ruler as it stands. Appended at LOCK and after every EXTENSION; the LAST record for a
    ``dataset_name`` wins, exactly as a re-seed supersedes. Written WHOLE rather than as a delta:
    `append` is not crash-atomic, so a torn line falls back to the previous complete ruler — a
    valid, merely smaller scale the next round re-extends — where a folded delta would lose cells
    silently. A fork inherits its parent's virtually, so its θ stay on the parent's scale.
    Not a progress event — the SSE tail skips it.

    ``dataset_name`` says whose sample ids the δ keys ARE — one ledger carries more than one,
    since an L4 outer cycle also owns the shared inner scale its cells read."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["ruler"] = "ruler"
    ruler: DeltaRuler
    dataset_name: str
    round: int
    timestamp: str = Field(default_factory=utcnow_iso)


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
    | ElectionRecord
    | ErrorRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PhaseRecord
    | RoundWarningRecord
    | RulerRecord
    | SnapshotRecord
    | SpendTombstoneRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


# The `issued_by` an operator fork carries when the client sent no identity. One SoT for the
# stamp site (`mint_operator_fork`) and the lineage suppression that turns it into a *no*
# "edited by" badge — they must agree on the literal or the badge silently breaks.
UNATTRIBUTED_OPERATOR = "operator"


class ForkSpec(StrictModel):
    """The single fork-provenance model — there is no free-string ``fork.trigger`` twin."""

    model_config = ConfigDict(frozen=True)

    trigger: ForkTrigger
    reason: str
    issued_by: str
    from_round: int | None = None
    from_candidate_id: str | None = None
    l1_layout: dict[str, str] | None = None
    seed: CycleSeed | None = None
    # The MEASURED direction, for a cut taken before its consequence was known — only a
    # correction needs it. `None` ⇒ the trigger implies the direction (`FORK_DIRECTION`),
    # which is every other cut. An override, not a fallback: only one exists at a time.
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
    """Operator YAML under ``datasets/{name}/sweep/``; the dispatcher widens it to a ``ForkSpec``.
    Every field but ``reason`` is a CONTRAST LEVER — ``reason`` is provenance and changes nothing
    the fork runs."""

    model_config = ConfigDict(frozen=True)

    reason: str = ""
    l1_layout: dict[str, str] | None = None

    @model_validator(mode="after")
    def _arm_pulls_a_lever(self) -> OperatorSweepFile:
        """A lever-less arm forks a COPY of its parent and pays a full scored round to measure it, so
        it fails here rather than at the end of the batch. The set is derived, never listed twice, and
        the test is EMPTINESS — an empty ``l1_layout`` moves no panel and coerces back to its base."""
        levers = sorted(set(type(self).model_fields) - {"reason"})
        if not any(getattr(self, lever) for lever in levers):
            raise ValueError(
                "pulls no contrast lever, so it forks a copy of its parent and pays a full "
                f"scored round to measure it; set one of: {', '.join(levers)}"
            )
        return self
