from __future__ import annotations

import enum
from collections.abc import Mapping
from typing import Annotated, Any, Literal, get_args

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.launch_limits import RoundsCap
from promptpotter.domain.opt_search_point import IndividualLineage
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import PairedReading
from promptpotter.domain.phase_views import PhaseView, RunSpendView, ViewAnchors
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    ErrorRecord,
    LaunchStage,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, NodeSearchNarrowing
from promptpotter.domain.results import (
    CandidateProposal,
    DisplayMetric,
    RoundCells,
    RoundOutcome,
    RoundResult,
    RunStanding,
    ScoredCandidate,
    ScoreSummary,
)
from promptpotter.domain.ruler import DeltaRuler, ThetaCaveat
from promptpotter.domain.scoring import Grade, MeasuredCell
from promptpotter.domain.spend import (
    SpendCeilings,
    TokenUsageKind,
    bill_or_rate_usd,
    declare_ceiling,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import MeasurementRole

__all__ = [
    "MAX_AUTO_REBASES",
    "MINT_KIND_LABELS",
    "OPERATOR_ORIGIN_SOURCES",
    "RECORD_ADAPTER",
    "BackendWarningRecord",
    "BenchCheckpointKind",
    "CandidateMintedRecord",
    "CandidateScoredRecord",
    "CandidateStartedRecord",
    "CheckinClosedRecord",
    "CheckpointKind",
    "CommandAckRecord",
    "CommandAckStatus",
    "CommandRecord",
    "ConfigOverrides",
    "CycleFinal",
    "CycleFinalRecord",
    "CycleMintedRecord",
    "CycleRecord",
    "CycleSeed",
    "CycleSeedRecord",
    "CycleSupersededRecord",
    "ElectionRecord",
    "ErrorRecord",
    "FlightRecord",
    "ForkGradedRecord",
    "ForkRemainder",
    "ForkSpec",
    "ForkTrigger",
    "InterventionRecord",
    "LLMCallProgressRecord",
    "LLMCallRecord",
    "LLMCallStartRecord",
    "LaunchClaimRecord",
    "LaunchReleasedRecord",
    "LedgerCandidate",
    "OriginSource",
    "PhaseRecord",
    "PricedKeyRecord",
    "RaceCatchUpRecord",
    "RaceStandingRecord",
    "ResumeCheckpointRecord",
    "RoundClosedRecord",
    "RoundEnteredRecord",
    "RoundStandingRecord",
    "RoundWarningKind",
    "RoundWarningRecord",
    "RunLimitsRecord",
    "RunPhaseRecord",
    "RunWiringRecord",
    "SampleOrderRecord",
    "SampleScoredRecord",
    "SampleStartedRecord",
    "ScoringLockedRecord",
    "SpawnedBy",
    "SpawnedRecord",
    "SpendHoldRecord",
    "TokenUsageRecord",
    "WallClock",
    "scored_cell",
]


class CheckpointKind(enum.StrEnum):
    """Subclassed by the bench's enum and each optimizer's, declared in its own package."""


class BenchCheckpointKind(CheckpointKind):
    FORK_CUT = "fork_cut"
    PANEL_COVERAGE = "panel_coverage"


class ResumeCheckpointRecord(StrictModel):
    """``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["decision"] = "decision"
    # A `CheckpointKind` value or a plugin's string; replayed where `replayers.py::replayers` has it.
    kind: str
    # The manifest node whose member took the decision; ``None`` for the bench's own.
    node: str | None = None
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
    view: PhaseView | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RunPhaseRecord(StrictModel):
    """The runner's own declaration: every other phase is derived off the last one, never said."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["run_phase"] = "run_phase"
    run_phase: RunPhase
    stop_reason: StopReason | None = None
    cause: PauseCause | None = None
    detail: str = ""
    spend: RunSpendView | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _paused_says_why(self) -> RunPhaseRecord:
        if self.run_phase is RunPhase.PAUSED and (self.stop_reason is None or self.cause is None):
            raise ValueError("a paused declaration names its stop reason and its cause")
        return self

    @classmethod
    def stop(
        cls,
        stop_reason: StopReason,
        *,
        cause: PauseCause | None = None,
        detail: str = "",
        spend: RunSpendView | None = None,
    ) -> RunPhaseRecord:
        if STOP_REASON_INFO[stop_reason].outcome is not StopOutcome.PAUSED:
            return cls(run_phase=RunPhase.TERMINAL, stop_reason=stop_reason, spend=spend)
        if cause is None:
            raise ValueError(f"a {stop_reason.value} stop pauses the cycle and names no cause")
        return cls(
            run_phase=RunPhase.PAUSED,
            stop_reason=stop_reason,
            cause=cause,
            detail=detail,
            spend=spend,
        )


class BackendWarningRecord(StrictModel):
    """An attempt that failed and will be retried: a transport fault or a 5xx, never a 429."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["backend_warning"] = "backend_warning"
    kind: str
    attempt: int
    max_attempts: int
    wait_s: float
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str = ""
    detail: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundEnteredRecord(StrictModel):
    """Entering N displaces N and every later round; ``rewound`` discards their proposals too."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_entered"] = "round_entered"
    round: int
    rewound: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundClosedRecord(RoundOutcome):
    """The last close per round stands and IS the round; round 0 closes again as the ruler warms."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    record_type: Literal["round_closed"] = "round_closed"
    cells: RoundCells
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of(cls, rr: RoundResult) -> RoundClosedRecord:
        return cls(
            **{name: getattr(rr, name) for name in RoundOutcome.model_fields}, cells=rr.cells()
        )


class OptimizerStateRecord(StrictModel):
    """A CLOSED round's state restated, never a close: the last after its standing close stands."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["optimizer_state"] = "optimizer_state"
    round: int
    optimizer_state: OptimizerState
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundProposedRecord(StrictModel):
    """Replayed on a resume into the round, unless a digest the round ``consumed`` has moved."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_proposed"] = "round_proposed"
    round: int
    consumed: str
    proposals: list[CandidateProposal]
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundStandingRecord(StrictModel):
    """Once per round, where a close can repeat, and displaced with its round."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_standing"] = "round_standing"
    round: int
    run_standing: RunStanding
    # Leading arm against the ORIGIN's rows: a pairing across two rounds, so it rides the standing.
    panel_precision: PanelPrecision | None = None
    anchors: ViewAnchors
    timestamp: str = Field(default_factory=utcnow_iso)


class _ArmFact(StrictModel):
    """``candidate_idx`` is ``NO_ROUND_SLOT`` for a pass that is no arm of the round."""

    model_config = ConfigDict(frozen=True)

    round: int
    candidate_idx: int
    candidate_total: int
    timestamp: str = Field(default_factory=utcnow_iso)


class CandidateStartedRecord(_ArmFact):
    """Announced before the first cell, so a live surface draws and forks it with no round file."""

    record_type: Literal["candidate_started"] = "candidate_started"
    changes_description: str = ""
    pipeline_overlay: dict[str, Any] | None = None
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    resolved_pipeline_params: dict[str, Any] | None = None
    # The turn's place in a block race — ``n`` of ``of``, ``size`` cells, ``racing`` arms live.
    block: dict[str, int] | None = None


class SampleOrderRecord(_ArmFact):
    record_type: Literal["sample_order"] = "sample_order"
    n_priors: int = 0
    sample_order: list[int]


class SampleStartedRecord(_ArmFact):
    """``stop_horizon`` is the fewest rows at which a stop rule could still cut."""

    record_type: Literal["sample_started"] = "sample_started"
    sample_idx: int
    sample_total: int
    sample_id: int
    # Capped at the writer: the whole query is a dataset fact, in every measurement row.
    query_preview: str = ""
    sample_lookahead: int = 1
    stop_horizon: int | None = None


class SampleScoredRecord(_ArmFact):
    """Read ``result`` through :func:`scored_cell`; a re-banked row has no ``sample_idx``."""

    record_type: Literal["sample_scored"] = "sample_scored"
    individual_id: str
    role: MeasurementRole
    sample_idx: int | None = None
    sample_total: int | None = None
    result: dict[str, Any]
    running: ScoreSummary | None = None


def scored_cell(result: Mapping[str, Any]) -> tuple[MeasuredCell, Grade]:
    return MeasuredCell.from_wire(result), Grade(
        result.get("fitness"), result.get("objective"), result.get("unscored")
    )


class CandidateScoredRecord(_ArmFact):
    record_type: Literal["candidate_scored"] = "candidate_scored"
    scores: ScoredCandidate
    anchors: ViewAnchors = Field(default_factory=ViewAnchors)


class FlightWaiting(StrictModel):
    model_config = ConfigDict(frozen=True)

    sample_id: int
    since: float


class FlightRecord(StrictModel):
    """The whole round's calls in flight, so it names no candidate."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["flight"] = "flight"
    round: int
    out: int
    allowed: int
    most: int
    affordable: int | None = None
    cell_usd: float | None = None
    waiting: FlightWaiting | None = None
    backpressure: BackpressureReading | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RaceStandingRecord(_ArmFact):
    """Archive-only, not divergence-gated; ``p_best`` is about ``current_id`` alone."""

    record_type: Literal["race_standing"] = "race_standing"
    member: str
    current_id: str
    n_samples: int
    p_best: float
    paired_breakdown: dict[str, dict[str, float]] = Field(default_factory=dict)
    decision_grade: bool


class RaceCatchUpRecord(_ArmFact):
    """The race's priors caught up on the just-measured sample; absence ⇒ cache covered it."""

    record_type: Literal["race_catch_up"] = "race_catch_up"
    member: str
    sample_id: int
    prior_ids: list[str]


class TokenUsageRecord(StrictModel):
    """ONE answered send, or a replay (``cached``); a send that reported no usage writes none."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["token_usage"] = "token_usage"
    kind: TokenUsageKind
    node: str
    model: str | None = None
    # Who BILLED: a rate belongs to the (provider, model) pair; ``None`` ⇒ no rate prices the call.
    provider: str | None = None
    # WHICH upstream host answered, where `provider` is a gateway; ``None`` where it is the host.
    served_by: str | None = None
    input_tokens: int
    output_tokens: int
    # A SUBSET of ``output_tokens``; 0 also means no breakdown was reported, never "did not think".
    reasoning_tokens: int = 0
    # A SUBSET of ``input_tokens`` the PROVIDER cached; 0 also means no breakdown. Not ``cached``.
    cache_read_tokens: int = 0
    # The part of ``input_tokens`` billed at a premium to POPULATE that cache.
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    # The BILL the provider reported, the only figure called spent; never filled from a rate.
    cost_usd: float | None = None
    # OUR rate's price of an unbilled call, stamped once: it counts against ceilings, never spent.
    rate_priced_usd: float | None = None
    # The `SpendHoldRecord` this call settles; ``None`` for a call nothing held (a replay).
    hold_id: str | None = None
    # A nested run's own view of its call; money is summed off the copy on the outer ledger.
    mirrored: bool = False
    cached: bool = False
    round: int | None = None
    # ``None`` outside a candidate's pass; a nested run's copy keeps ITS pass: `by_role` skips it.
    role: MeasurementRole | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def bill_or_rate_usd(self) -> float | None:
        return bill_or_rate_usd(self.cost_usd, self.rate_priced_usd)

    @property
    def spend_round(self) -> int:
        """A call carrying no round ran before any closed (init, the origin score): banks at 0."""
        return 0 if self.round is None else self.round


class SpendHoldRecord(StrictModel):
    """Written BEFORE the send, at its bound; unclosed, it binds every ceiling and is not spent."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["spend_hold"] = "spend_hold"
    hold_id: str
    kind: TokenUsageKind
    node: str
    model: str | None = None
    provider: str | None = None
    input_tokens: int
    # ``None`` where nothing capped the reply: such a send holds no tokens.
    output_tokens: int | None
    cost_usd: float | None = None
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class PricedKeyRecord(StrictModel):
    """A key the search has PRICED, so a later read of it, resumed or not, prices nothing again."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["priced_key"] = "priced_key"
    priced_key: str
    timestamp: str = Field(default_factory=utcnow_iso)


class SpendTombstoneRecord(StrictModel):
    """On the WORKSPACE ledger, so deleting what spent it cannot re-earn a spend ceiling."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["spend_tombstone"] = "spend_tombstone"
    campaign_id: str
    # Empty for a whole-campaign bank; with `campaign_id`, the key of the re-bank guard.
    cycle_id: str = ""
    used_usd: float
    # What our rate table priced the calls no provider billed (`TokenUsageRecord.rate_priced_usd`).
    rate_priced_usd: float
    used_tokens: int
    unpriced_tokens: int
    # What its unreported sends may have cost (`SpendHoldRecord`) — banked apart, as it is read.
    unreported_usd: float = 0.0
    unreported_tokens: int = 0
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallStartRecord(StrictModel):
    """Pairs with :class:`LLMCallRecord` via ``call_id``, so a long call does not look frozen."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    prompt_chars: int = 0
    # Injection name → rendered chars. Empty for a node composing no layout and on a cache replay.
    injection_chars: dict[str, int] = Field(default_factory=dict)
    # What the node's ceiling REFUSED, per panel; `injection_chars` reports only what SURVIVED.
    injection_dropped: dict[str, int] = Field(default_factory=dict)
    injection_silent: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def refused_panels(self) -> list[str]:
        """Dropped WHOLE, nothing surviving in ``injection_chars``; a THINNED panel is not one."""
        return sorted(n for n in self.injection_dropped if n not in self.injection_chars)


class LLMCallProgressRecord(StrictModel):
    """Heartbeat while the SDK call is blocked; cache replays skip it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    # Set by the inner-campaign heartbeat (``runner/inner/spawn.py``); ``None`` on an ordinary one.
    detail: str | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(StrictModel):
    """``payload_kind='synthesized'`` is a replay: its messages, response and usage are absent."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


CommandAckStatus = Literal["accepted", "applied", "rejected"]


class CommandRecord(StrictModel):
    """``CommandDispatcher`` alone writes it, to the cycle's, its root's or the workspace ledger."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command"] = "command"
    command_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    issued_by_user_id: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandAckRecord(StrictModel):
    """``applied`` = it HAPPENED; a loop command is ``accepted`` first, and stays so if untaken."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: CommandAckStatus
    detail: str = ""
    effect: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


RoundWarningKind = Literal[
    "l1_zero_candidates",
    "injection_budget_overrun",
    "layer_parse_failure",
    "layer_terminated_cycle",
    "send_refused",
    # A `terminate_proposal` carrying no reason: ignored, never silently.
    "layer_terminate_blank",
    # `l1_generate` ran with its MANDATORY critique panel empty.
    "l1_critique_unavailable",
    # Nothing failed: the round promoted an arm the origin panel does not back (`runner/round.py`).
    "round_not_advanced",
]


class RoundWarningRecord(StrictModel):
    """A mid-round SELF-HEAL the run continued past; never re-add a kind with no emitter."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    # `error` = the round produced nothing usable; `warning` = degraded but progressed.
    severity: Literal["warning", "error"] = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkTrigger(enum.StrEnum):
    """One value per caller of :func:`mint_fork`."""

    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    OPERATOR_STEERED = "operator_steered"
    OPTIMIZER_REBASE = "optimizer_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkDirection(enum.StrEnum):
    """Which side of a cut the run CONTINUES on, the half a cut alone cannot say."""

    # The CHILD is the branch; the parent stays the line it was.
    OFFSHOOT = "offshoot"
    # The CHILD is the continuation; the parent is what was left behind.
    SUPERSEDE = "supersede"
    # BOTH continue identically. MEASURED, so no trigger implies it: it rides `ForkSpec.direction`.
    EQUIVALENT = "equivalent"


# Derived from the trigger, never stored. Exhaustiveness is checked at import below.
FORK_DIRECTION: dict[ForkTrigger, ForkDirection] = {
    ForkTrigger.OPERATOR_DIAG: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_STEERED: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_REWIND: ForkDirection.SUPERSEDE,
    ForkTrigger.OPTIMIZER_REBASE: ForkDirection.SUPERSEDE,
    ForkTrigger.SCORING_DIVERGENCE: ForkDirection.SUPERSEDE,
}

_undirected = [t for t in ForkTrigger if t not in FORK_DIRECTION]
if _undirected:
    raise RuntimeError(
        f"ForkTrigger members missing from FORK_DIRECTION: {_undirected}. A cut whose "
        "direction nobody declared renders as an offshoot, which is a lie half the time."
    )
del _undirected


MintKind = Literal["session", "divergent_resume", "user_fork", "auto_rebase"]

MINT_KIND_LABELS: dict[MintKind, str] = {
    "session": "Session",
    "divergent_resume": "divergent resume",
    "user_fork": "user fork",
    "auto_rebase": "auto rebase",
}
assert MINT_KIND_LABELS.keys() == set(get_args(MintKind))

MINT_KIND_FOR_TRIGGER: dict[ForkTrigger, MintKind] = {
    ForkTrigger.SCORING_DIVERGENCE: "divergent_resume",
    ForkTrigger.OPTIMIZER_REBASE: "auto_rebase",
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
    """A fork's campaign-config delta, in which an absent field inherits the parent's value."""

    model_config = ConfigDict(frozen=True)

    max_rounds: int | None = None
    ceiling: SpendCeilings = SpendCeilings()
    # Laid key by key onto the parent's `optimization.nodes`; nothing here switches the manifest.
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    # Laid key by key over the parent's `CampaignConfig.scoring`; `{"per_cell": F}` is the `score:F` lens.
    scoring: str | dict[str, str] | None = None


class ForkRemainder(StrictModel):
    """What a cycle's rounds cap and spend cap have left, which an offshoot takes as its own caps.

    A null cap is one the cycle does not carry, or whose spend is unread: the offshoot inherits it.
    """

    model_config = ConfigDict(frozen=True)

    rounds_closed: int = Field(
        description="Rounds closed AFTER the origin — what `max_rounds` counts"
    )
    parent_max_rounds: int | None
    max_rounds: int | None = Field(
        description="`parent_max_rounds` less `rounds_closed`, never under 1"
    )
    metered_usd: float | None = Field(description="What the cycle's spend cap has counted")
    parent_ceiling: SpendCeilings
    ceiling: SpendCeilings = Field(
        description="`parent_ceiling.usd` less `metered_usd`, never under 0; the token arm "
        "is not remaindered, so it is null and the offshoot inherits the parent's"
    )

    @classmethod
    def of(
        cls,
        *,
        rounds_closed: int,
        max_rounds: int | None,
        metered_usd: float | None,
        ceiling: SpendCeilings,
    ) -> ForkRemainder:
        return cls(
            rounds_closed=rounds_closed,
            parent_max_rounds=max_rounds,
            max_rounds=None if max_rounds is None else max(1, max_rounds - rounds_closed),
            metered_usd=metered_usd,
            parent_ceiling=ceiling,
            ceiling=SpendCeilings(
                usd=None
                if ceiling.usd is None or metered_usd is None
                else max(0.0, round(ceiling.usd - metered_usd, 6))
            ),
        )

    def under(self, overrides: ConfigOverrides) -> ConfigOverrides:
        """``0`` is a cap: only ``None`` takes the remainder."""
        return overrides.model_copy(
            update={
                "max_rounds": self.max_rounds
                if overrides.max_rounds is None
                else overrides.max_rounds,
                "ceiling": declare_ceiling(self.ceiling, overrides.ceiling),
            }
        )


class OriginSource(enum.StrEnum):
    FORK_SEED = "fork_seed"
    CAMPAIGN_ORIGIN = "campaign_origin"
    # The seed carries no origin: an L2/L3 rebase replays its own C0, which has none to stamp.
    REPLAYED = ""


OPERATOR_ORIGIN_SOURCES = frozenset(OriginSource) - {OriginSource.REPLAYED}


class CycleSeed(StrictModel):
    """``pipeline_overlay`` merges ON TOP of the dataset overlay for this cycle only."""

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
    origin_source: OriginSource = Field(
        default=OriginSource.REPLAYED,
        description=(
            "Which act seeded C0 — 'fork_seed' | 'campaign_origin', naming its lineage's "
            "`changes_description`; empty when the seed carries no origin (an L2/L3 rebase "
            "replays its own)."
        ),
    )

    @model_validator(mode="after")
    def _origin_needs_provenance(self) -> CycleSeed:
        if self.origin_prompt_fields and not self.origin_source:
            raise ValueError("origin_prompt_fields set without an origin_source stamp")
        return self


class CandidateMintedRecord(StrictModel):
    """Identity must outlive a producer that dies mid-flight; ``label`` is MINTED, not read-time."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["candidate_minted"] = "candidate_minted"
    round: int
    idx: int
    candidate_id: str
    label: str
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)
    timestamp: str = Field(default_factory=utcnow_iso)


# `invalid` is rejected before it cost a sample. Never `winner`: election is a round-close fact.
CandidateState = Literal["minted", "measured", "invalid"]


class LedgerCandidate(StrictModel):
    """Derived off the ledger, never a record on it."""

    model_config = ConfigDict(frozen=True)

    round: int
    idx: int
    candidate_id: str
    label: str
    lineage: IndividualLineage = Field(default_factory=IndividualLineage)
    walk_length: int | None = None
    report: ScoredCandidate | None = None


class LedgerFit(StrictModel):
    """Keyed by LABEL in :class:`ElectionRecord`; the close re-reads θ and nothing else."""

    model_config = ConfigDict(frozen=True)

    theta: float | None = None
    theta_se: float | None = None
    # This ARM's own reason θ is not ability — only ever ``FLOOR_PINNED``.
    theta_caveat: ThetaCaveat | None = None
    vs_reference: PairedReading | None = None


class WallClock(StrictModel):
    """CLOCK legs sum to ``elapsed_s``; ``worked_s`` is CALL time, so concurrency pushes it past."""

    model_config = ConfigDict(frozen=True)

    # ``None`` where either endpoint is unparseable; every share below is then unanswerable too.
    elapsed_s: float | None
    # By ``CampaignPhase`` value; a bracket that never fired or never closed is ABSENT, never 0.0.
    phase_s: dict[str, float] = Field(default_factory=dict)
    # ``TokenUsageKind`` → node → summed call seconds; cached calls excluded.
    worked_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Same keys, in CLOCK: seconds held while no bracket and no gate was open.
    unbracketed_call_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Round (JSON key) → seconds to its FIRST close: the last is a ruler restamp or a rewind's re-run.
    round_ended_s: dict[str, float] = Field(default_factory=dict)
    # HUMAN time at the origin gate, never folded into a machine leg.
    gate_s: float = 0.0
    # ``elapsed_s`` minus every CLOCK leg.
    unattributed_s: float | None = None
    # ``None`` = no cell was measured under an envelope; 0.0 = enveloped cells waited for nothing.
    unworked_s: float | None = None


class CycleFinal(StrictModel):
    model_config = ConfigDict(frozen=True)

    started_at: str
    wall_clock: WallClock
    rounds_to_separable: int | None
    rounds_to_improved: int | None
    rounds_to_ceiling: int | None
    accuracy_ceiling: float | None
    prompt_hashes: dict[str, str]
    # On the origin's OWN samples — never round 1's matched floor, a different sample basis.
    origin_composite_fitness: float | None
    scorer_id: str
    mode: Literal["diag", "full"]
    # The round the optimizer's DECLARED pick was selected in.
    result_round: int
    result_prompt_fields: dict[str, Any]
    result_pipeline_params: dict[str, Any] | None


class ElectionRecord(StrictModel):
    """Keyed by LABEL; empty ``selected_labels`` = the round HELD; round 0 selects its ``C0``."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["election"] = "election"
    round: int
    selected_labels: list[str] = Field(default_factory=list)
    fit: dict[str, LedgerFit] = Field(default_factory=dict)
    elects_on: DisplayMetric
    # The selector's own reason; ``None`` on the origin round, which chose between nobody.
    verdict_reason: str | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RulerRecord(StrictModel):
    """Written WHOLE, last per ``dataset_name`` wins: a torn append falls back to a whole ruler."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["ruler"] = "ruler"
    ruler: DeltaRuler
    dataset_name: str
    round: int
    timestamp: str = Field(default_factory=utcnow_iso)


class RunLimitsRecord(StrictModel):
    """LAST wins; read PHYSICALLY, so a fork never inherits a ceiling that overrides its seed."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["run_limits"] = "run_limits"
    ceiling: SpendCeilings = SpendCeilings()
    rounds: RoundsCap | None = None
    # RUN-scoped: the closed-round count at which the launch in flight pauses (``step-cycle``).
    pause_at_round: int | None = None
    # ``None`` on an arm this change left alone; a launch declares none and admits its own.
    reserve: SpendCeilings = SpendCeilings()
    timestamp: str = Field(default_factory=utcnow_iso)


class RunWiringRecord(StrictModel):
    """LAST wins, over the whole chain: a fork stands on its parent's until its own lands."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["run_wiring"] = "run_wiring"
    arms_per_round: int | None
    sp_budget_round: int
    # DISPLAY only: seeds a surface's metric toggle; the selector decides on its own objective.
    display_metric: DisplayMetric
    elects_on: DisplayMetric
    max_cells_in_flight: int
    measured_unit: MeasuredUnit
    # ``None`` when Langfuse is disabled, and on a mint's record: no launch has opened a trace.
    langfuse_trace_url: str | None
    run_limits: RunLimits
    timestamp: str = Field(default_factory=utcnow_iso)


class ScoringLockedRecord(StrictModel):
    """Appended once, as the origin is scored under dials; a config already locked appends none."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["scoring_locked"] = "scoring_locked"
    declared: dict[str, str]
    locked: dict[str, str]
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSeedRecord(StrictModel):
    """A fork inherits its parent's seed VIRTUALLY yet appends its own, which one scan returns."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["cycle_seed"] = "cycle_seed"
    seed: CycleSeed
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkSpec(StrictModel):
    model_config = ConfigDict(frozen=True)

    trigger: ForkTrigger
    reason: str
    issued_by: str
    from_round: int | None = None
    from_candidate_id: str | None = None
    seed: CycleSeed | None = None
    # The MEASURED direction; `None` ⇒ the trigger implies it (`FORK_DIRECTION`).
    direction: ForkDirection | None = None

    @property
    def resolved_direction(self) -> ForkDirection:
        return self.direction or FORK_DIRECTION[self.trigger]


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure."""

    outer_cycle_id: str = Field(description="The outer cycle that owns this inner sandbox")
    outer_campaign_id: str = Field(
        description="The outer CAMPAIGN that owns this inner sandbox. Required alongside the cycle because a `cycle_id` is content-addressed on its origin and so is shared by every campaign minted from that origin — the pair is the identity, either half alone is not, and a null here is why two pooled sandboxes on disk could not be attributed after the fact.",
    )
    round: int | None = Field(
        default=None,
        description="Outer round; 0 is the origin (C0). Null when the spawn came from outside any round (the noise-floor diagnostic).",
    )
    candidate_idx: int | None = Field(
        default=None, description="Position in the outer round's population; null for the origin."
    )
    candidate_id: str | None = Field(
        default=None,
        description="The outer candidate's `OptSearchPoint.id` — stable across rounds; null for the origin.",
    )
    candidate_label: str | None = Field(
        default=None,
        description="Canonical label (`C0` for the origin, else `C{round}.{idx+1}`) — the same string the round file and console use.",
    )
    role: str | None = Field(
        default=None,
        description="WHY this cell ran (`MeasurementRole`). A backfill is spawned outside the round's shared order to fill a paired comparison, and reading one as the candidate's own panel cell makes a repaired round unreproducible. Null for the origin.",
    )
    task: str = Field(
        description="The panel cell this run measured — the outer query, e.g. `justlogic-d234/seed-0` (`inner_tasks.yaml::tasks[].id`). The candidate fields do NOT identify a run: every task runs for every candidate, so one candidate's spawns are as many as the panel has cells and are told apart only by this.",
    )


class CycleMintedRecord(StrictModel):
    """The first line of a cycle's own ledger; a ledger chain is walked off this record alone."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["cycle_minted"] = "cycle_minted"
    parent_cycle_id: str | None = None
    forked_at_offset: int | None = None
    fork: ForkSpec | None = None
    # The cycle reads ``RunPhase.CHECKIN`` until a :class:`CheckinClosedRecord` follows.
    checkin: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _fork_is_whole(self) -> CycleMintedRecord:
        parts = (self.parent_cycle_id, self.forked_at_offset, self.fork)
        if any(p is None for p in parts) and any(p is not None for p in parts):
            raise ValueError("a fork carries parent_cycle_id, forked_at_offset and fork together")
        if self.checkin and self.fork is not None:
            raise ValueError("a fork is cut from a cycle that ran; it is never minted in check-in")
        return self


class CheckinClosedRecord(StrictModel):
    """One per cycle, and nothing reopens it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["checkin_closed"] = "checkin_closed"
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleFinalRecord(StrictModel):
    """Banked only where the cycle's own runner stops it; a later ``running`` retires it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["cycle_final"] = "cycle_final"
    final: CycleFinal
    # The round the stop left unclosed; ``None`` for a stop at a boundary.
    interrupted_round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSupersededRecord(StrictModel):
    """On the LEFT-BEHIND side of a supersede cut; a later ``running`` here takes the line back."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["cycle_superseded"] = "cycle_superseded"
    successor_cycle_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkGradedRecord(StrictModel):
    """A cut's MEASURED direction, on the branch: it overrides the one its trigger implies."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["fork_graded"] = "fork_graded"
    direction: ForkDirection
    timestamp: str = Field(default_factory=utcnow_iso)


class InterventionRecord(StrictModel):
    """One is enough to make the cycle babysat for good; appended the moment it happens."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["intervention"] = "intervention"
    kind: str
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchClaimRecord(StrictModel):
    """Stands only while its process lives: ``claimant_lock`` is the OS lock that process holds."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["launch_claim"] = "launch_claim"
    stage: LaunchStage
    job_id: str
    claimant_lock: str
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchReleasedRecord(StrictModel):
    """Ends that launch's :class:`LaunchClaimRecord` with no run, and without ending the cycle."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["launch_released"] = "launch_released"
    job_id: str
    detail: str
    timestamp: str = Field(default_factory=utcnow_iso)


class SpawnedRecord(StrictModel):
    """Appended at every open, so the last names the work-item the cycle last measured for."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["spawned"] = "spawned"
    spawned_by: SpawnedBy
    timestamp: str = Field(default_factory=utcnow_iso)


CycleRecord = Annotated[
    ResumeCheckpointRecord
    | BackendWarningRecord
    | CandidateMintedRecord
    | CandidateScoredRecord
    | CandidateStartedRecord
    | FlightRecord
    | PricedKeyRecord
    | CheckinClosedRecord
    | CommandAckRecord
    | CommandRecord
    | CycleFinalRecord
    | CycleMintedRecord
    | CycleSeedRecord
    | CycleSupersededRecord
    | ElectionRecord
    | ErrorRecord
    | ForkGradedRecord
    | InterventionRecord
    | LaunchClaimRecord
    | LaunchReleasedRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | OptimizerStateRecord
    | PhaseRecord
    | RaceCatchUpRecord
    | RaceStandingRecord
    | RoundClosedRecord
    | RoundEnteredRecord
    | RoundProposedRecord
    | RoundStandingRecord
    | RoundWarningRecord
    | RulerRecord
    | RunLimitsRecord
    | RunPhaseRecord
    | RunWiringRecord
    | SampleOrderRecord
    | SampleScoredRecord
    | SampleStartedRecord
    | ScoringLockedRecord
    | SpawnedRecord
    | SpendHoldRecord
    | SpendTombstoneRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]

RECORD_ADAPTER: TypeAdapter[CycleRecord] = TypeAdapter(CycleRecord)


class RebaseRequest(StrictModel):
    """A policy change and a rewind are ONE move: the new axis is searched only on the sibling."""

    model_config = ConfigDict(frozen=True)

    fork_from_round: int
    trigger: ForkTrigger
    reason: str
    issued_by: str
    config_overrides: ConfigOverrides | None = None


# PER LEVEL: an inner campaign gets its own, so a `fork_proposal` on every fire cannot spiral.
MAX_AUTO_REBASES: Annotated[int, shapes_optimizer_prompt] = 10
