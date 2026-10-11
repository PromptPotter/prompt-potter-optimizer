from __future__ import annotations

from typing import Any, Literal, NamedTuple, assert_never, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.cycle_paths import CyclePath, command_address
from promptpotter.domain.phase_views import BenchEnterView
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, GateDecision, RunPhase
from promptpotter.domain.results import DegradationHealth, HealthCause, candidate_label
from promptpotter.domain.run_records import (
    BackendWarningRecord,
    CandidateMintedRecord,
    CandidateScoredRecord,
    CandidateStartedRecord,
    CheckinClosedRecord,
    CommandAckRecord,
    CommandRecord,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeedRecord,
    CycleSupersededRecord,
    ElectionRecord,
    ErrorRecord,
    FlightRecord,
    ForkGradedRecord,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchReleasedRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    OptimizerStateRecord,
    PhaseRecord,
    PricedKeyRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundStandingRecord,
    RoundWarningRecord,
    RulerRecord,
    RunLimitsRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleOrderRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    ScoringLockedRecord,
    SpawnedRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)
from promptpotter.domain.spend import TokenAccount, prefix_reading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import duration_words

__all__ = [
    "LIFETIME",
    "SILENT_RECORDS",
    "ActivityDecision",
    "ActivityFeed",
    "ActivityItem",
    "ActivityKind",
    "ActivityState",
    "DecisionAction",
    "DecisionFact",
]

# A tail never validates these (`projection_envelope.py::NON_ACTIVITY_KINDS`); `_item` is total over the rest.
SILENT_RECORDS = (
    BackendWarningRecord,
    CheckinClosedRecord,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleSupersededRecord,
    ElectionRecord,
    FlightRecord,
    ForkGradedRecord,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchReleasedRecord,
    OptimizerStateRecord,
    PricedKeyRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    ResumeCheckpointRecord,
    RoundProposedRecord,
    RulerRecord,
    RunLimitsRecord,
    RunWiringRecord,
    SampleOrderRecord,
    SampleStartedRecord,
    ScoringLockedRecord,
    SpawnedRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)

ActivityKind = Literal[
    "running",
    "done",
    "candidate",
    "round",
    "warning",
    "error",
    "merge",
    "progress",
]
ActivityTone = Literal["good", "warn", "bad", "muted"]

# `status` lasts to the next status or run-phase declaration, `round` to the next round, `run` to the next launch.
Lifetime = Literal["status", "round", "run"]
LIFETIME: dict[ActivityKind, Lifetime] = {
    "running": "status",
    "done": "status",
    "candidate": "status",
    "round": "status",
    "progress": "status",
    "warning": "round",
    "merge": "round",
    "error": "run",
}


class ActivityItem(StrictModel):
    """One record's reading: a line to show, and the round and candidate it is about."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="The record's kind and ledger offset; one record, one id.")
    kind: ActivityKind
    icon: str
    label: str
    detail: str | None = Field(default=None, description="The quieter half of the line.")
    tone: ActivityTone = "muted"
    round: int | None = Field(default=None, description="The round the record names.")
    candidate: str | None = Field(
        default=None,
        description="The candidate the record is about, by the label its course minted.",
    )


class ActivityState(StrictModel):
    """A run's current state: what it is doing, and what was said that still holds."""

    model_config = ConfigDict(frozen=True)

    status: ActivityItem | None = Field(
        description="The one line saying what the run is doing; each new one replaces it."
    )
    notices: list[ActivityItem] = Field(
        description="Warnings and controls of the round the run is in, and any error of this "
        "launch."
    )
    decision: ActivityDecision | None = Field(
        description="What the run is holding for the operator to decide; null while it holds "
        "for nothing."
    )


class DecisionFact(StrictModel):
    """One labelled line of the evidence behind a hold."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: str
    tone: ActivityTone = "muted"


class DecisionAction(StrictModel):
    """One button: a press POSTs ``payload`` to ``/commands/{kind}`` unchanged."""

    model_config = ConfigDict(frozen=True)

    label: str
    variant: Literal["primary", "ghost", "danger"]
    kind: CommandKind
    payload: dict[str, Any] = Field(
        description="The whole command payload, the cycle's address and `descend` tail included."
    )


class ActivityDecision(StrictModel):
    """A choice the run is holding for: what to say about it, and the commands that answer it."""

    model_config = ConfigDict(frozen=True)

    title: str
    lead: str = Field(description="The paragraph under the title.")
    facts: list[DecisionFact] = Field(description="The evidence, in reading order.")
    reasons: list[str] = Field(description="Why the run is holding, one fragment each.")
    actions: list[DecisionAction]


_GATE_ACTIONS: dict[GateDecision, tuple[str, Literal["primary", "ghost", "danger"]]] = {
    "rescore": ("Re-score origin", "primary"),
    "proceed": ("Proceed anyway", "ghost"),
    "abort": ("Abort", "danger"),
}
_CAUSE_LINE: dict[HealthCause, str] = {
    "origin_unmeasured": "the origin was not measured",
    "origin_incomplete": "the origin is missing cells",
    "structural": "a node failed structurally",
    "unscoreable": "no extractable answer",
    "holed": "cells returned no measurement",
    "evidence_starved": "an enricher produced no evidence",
    "structural_untested": "a structural failure with no clean prior",
    "persistent": "degraded for several rounds running",
    "degraded": "degraded on a share of samples",
}
if set(_GATE_ACTIONS) != set(get_args(GateDecision)) or set(_CAUSE_LINE) != set(
    get_args(HealthCause)
):
    raise RuntimeError("the origin gate's buttons or cause lines are out of step with their type")


def _gate_facts(health: DegradationHealth) -> list[DecisionFact]:
    """COVERAGE LEADS: the degraded rate and the advice are computed over disjoint rows."""
    panel = health.samples + health.not_attempted
    measured = f"{health.samples} of {panel}"
    if health.not_attempted > 0:
        measured += f" · {health.not_attempted} never sent"
    facts = [DecisionFact(label="Cells measured", value=measured)]
    if health.hole_count > 0:
        facts.append(DecisionFact(label="Returned nothing", value=str(health.hole_count)))
    # Holes never reach the degraded numerator, so the rate shows only where something came back.
    if health.samples - health.hole_count > 0:
        facts.append(
            DecisionFact(
                label="Degraded rate",
                value=f"{health.degraded_rate:.0%} · {health.structural_count} structural / "
                f"{health.transient_count} transient",
            )
        )
    if health.dominant_node:
        facts.append(DecisionFact(label="Worst node", value=health.dominant_node))
    if health.last_error:
        facts.append(DecisionFact(label="Last error", value=health.last_error, tone="bad"))
    if health.suggested_action:
        facts.append(DecisionFact(label="Suggested fix", value=health.suggested_action))
    return facts


def _origin_gate(address: CyclePath, health: DegradationHealth | None) -> ActivityDecision:
    grade = "unknown" if health is None else health.grade
    return ActivityDecision(
        title=f"Origin gate — verdict: {grade}",
        lead=f"The origin (round 0) scored {grade} — not healthy enough to optimize against. "
        "The run is holding before round 1. Fix the connector and re-score to re-check, proceed "
        "to optimize anyway, or abort.",
        facts=[] if health is None else _gate_facts(health),
        reasons=[] if health is None or health.cause is None else [_CAUSE_LINE[health.cause]],
        actions=[
            DecisionAction(
                label=label,
                variant=variant,
                kind=CommandKind.ORIGIN_GATE_DECISION,
                payload={**command_address(address), "decision": decision},
            )
            for decision, (label, variant) in _GATE_ACTIONS.items()
        ],
    )


class _BenchPass(NamedTuple):
    candidate: str | None
    who: str
    rows: int | None


def _rec(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _pct(value: object) -> str | None:
    number = _num(value)
    return None if number is None else f"{number:.0%}"


def _node_label(node: str, at: int | None) -> str:
    return node if at is None else f"{node}·r{at}"


class ActivityFeed:
    def __init__(self) -> None:
        self._status: ActivityItem | None = None
        self._notices: list[ActivityItem] = []
        self._round: int | None = None
        self._bench: _BenchPass | None = None
        self._at_gate = False
        self._origin_health: DegradationHealth | None = None

    def state(self, address: CyclePath) -> ActivityState:
        """*address* is passed in: a decision's commands need it, and a ledger does not name its own path."""
        return ActivityState(
            status=self._status,
            notices=list(self._notices),
            decision=_origin_gate(address, self._origin_health) if self._at_gate else None,
        )

    def read(self, sequence: int, record: CycleRecord) -> ActivityItem | None:
        self._declared(record)
        item = self._item(f"{record.record_type}-{sequence}", record)
        if item is None:
            return None
        if LIFETIME[item.kind] == "status":
            self._status = item
        else:
            self._notices.append(item)
        return item

    def _declared(self, record: CycleRecord) -> None:
        """``RoundEnteredRecord`` alone MOVES the run: every launch re-enters ``origin`` at round 0 to replay it."""
        match record:
            case RunPhaseRecord():
                self._status = None
                # The hold lasts to the next declaration, so a re-score under way is still at the gate.
                self._at_gate = record.run_phase == RunPhase.GATE
                if record.run_phase == RunPhase.RUNNING:
                    self._notices = [n for n in self._notices if LIFETIME[n.kind] != "run"]
            case RoundClosedRecord(round=0):
                # The last close stands: a re-score at the gate closes round 0 again.
                self._origin_health = record.health
            case RoundEnteredRecord():
                if self._round is not None and record.round != self._round:
                    self._notices = [n for n in self._notices if LIFETIME[n.kind] == "run"]
                self._round = record.round
            case PhaseRecord(event="enter", round=int() as opened) if self._round is None:
                self._round = opened

    def _item(self, item_id: str, record: CycleRecord) -> ActivityItem | None:
        if isinstance(record, SILENT_RECORDS):
            return None
        match record:
            case LLMCallStartRecord():
                return ActivityItem(
                    id=item_id,
                    kind="running",
                    icon="↻",
                    label=_node_label(record.node, record.round),
                    detail=record.model or "default",
                    round=record.round,
                )
            case LLMCallRecord():
                return self._call(item_id, record)
            case CandidateStartedRecord():
                label = candidate_label(record.round, record.candidate_idx)
                return ActivityItem(
                    id=item_id,
                    kind="candidate",
                    icon="◆",
                    label=label,
                    tone="good",
                    round=record.round,
                    candidate=label,
                )
            case CandidateScoredRecord():
                return self._candidate_scored(item_id, record)
            case SampleScoredRecord():
                return self._sample_scored(item_id, record)
            case PhaseRecord():
                return self._phase(item_id, record)
            case RunPhaseRecord() | RoundEnteredRecord() | RoundClosedRecord():
                # Read by `_declared`; not SILENT, since a silent record is never handed to this feed.
                return None
            case RoundStandingRecord():
                return ActivityItem(
                    id=item_id,
                    kind="round",
                    icon="★",
                    label=f"Round {record.round}",
                    detail=record.run_standing.selection_line,
                    tone="good",
                    round=record.round,
                )
            case RoundWarningRecord():
                error = record.severity == "error"
                return ActivityItem(
                    id=item_id,
                    kind="warning",
                    icon="✗" if error else "⚠",
                    label=record.message or "round degraded",
                    tone="bad" if error else "warn",
                    round=record.round,
                )
            case ErrorRecord():
                return ActivityItem(
                    id=item_id,
                    kind="error",
                    icon="✗",
                    label=record.message or "run error",
                    detail=STOP_REASON_INFO[record.stop_reason].label,
                    tone="bad",
                    round=record.round,
                )
            case CommandRecord():
                return ActivityItem(id=item_id, kind="merge", icon="⚡", label=record.kind)
            case CommandAckRecord():
                if record.status != "rejected":
                    return None
                return ActivityItem(
                    id=item_id,
                    kind="merge",
                    icon="⚠",
                    label="control rejected",
                    detail=record.detail or None,
                    tone="warn",
                )
            case LLMCallProgressRecord():
                if not record.detail:
                    return None
                return ActivityItem(
                    id=item_id,
                    kind="progress",
                    icon="·",
                    label=record.detail,
                    detail=duration_words(record.elapsed_s),
                    round=record.round,
                )
            case CandidateMintedRecord():
                return ActivityItem(
                    id=item_id,
                    kind="candidate",
                    icon="◆",
                    label=record.label,
                    tone="good",
                    round=record.round,
                    candidate=record.label,
                )
            case CycleSeedRecord():
                return ActivityItem(
                    id=item_id,
                    kind="merge",
                    icon="⚡",
                    label="cycle seeded",
                    detail=record.seed.origin_source or None,
                )
            case _:
                assert_never(record)

    @staticmethod
    def _call(item_id: str, record: LLMCallRecord) -> ActivityItem:
        inner = record.payload
        replayed = bool(inner.get("cached"))
        usage = TokenAccount.from_payload(inner.get("usage"))
        bits: list[str] = []
        if (duration := _num(inner.get("duration_s"))) is not None:
            bits.append(f"{duration:.1f}s")
        if usage.total > 0:
            bits.append(f"{usage.total} tok")
        prefix = prefix_reading(usage.cache_share(replayed=replayed), replayed=replayed)
        if prefix.state == "unreported":
            bits.append("prefix not reported")
        elif prefix.share is not None:
            bits.append(f"{prefix.share:.0%} prefix cached")
        # "replayed", not "cached": OUR archive served it, and `cached` names the provider's discount.
        if replayed:
            bits.append("replayed")
        return ActivityItem(
            id=item_id,
            kind="done",
            icon="✓",
            label=_node_label(record.node, record.round),
            detail=" · ".join(bits) or None,
            round=record.round,
        )

    def _candidate_scored(self, item_id: str, record: CandidateScoredRecord) -> ActivityItem:
        return ActivityItem(
            id=item_id,
            kind="candidate",
            icon="◆",
            label=record.scores.label,
            detail=_pct(record.scores.composite_fitness),
            tone="good",
            round=record.round,
            candidate=record.scores.label,
        )

    def _sample_scored(self, item_id: str, record: SampleScoredRecord) -> ActivityItem:
        at, slot = record.round, record.candidate_idx
        scored, total = (record.sample_idx or 0) + 1, record.sample_total
        count = f"{scored}/{total}" if total else None
        running = record.running
        # A slotless measurement (a bench pass, the parent's re-score) is no candidate of the round.
        if slot < 0:
            if self._bench is None:
                return ActivityItem(
                    id=item_id,
                    kind="progress",
                    icon="·",
                    label=f"scoring {count}" if count else "scoring",
                    round=at,
                )
            # The candidate and the count lead: a ray chip truncates.
            candidate, who, rows = self._bench
            rows = total or rows
            tally = f"bench {scored}/{'?' if rows is None else rows}"
            so_far = None if running is None else _pct(running.accuracy)
            return ActivityItem(
                id=item_id,
                kind="progress",
                icon="·",
                label=f"{candidate} · {tally}" if candidate else tally,
                detail=f"{who} · {so_far} so far" if so_far else who,
                candidate=candidate,
            )
        label = candidate_label(at, slot)
        return ActivityItem(
            id=item_id,
            kind="progress",
            icon="·",
            label=f"{label} · scoring {count}" if count else f"{label} · scoring",
            detail=None if running is None else _pct(running.composite_fitness),
            round=at,
            candidate=label,
        )

    def _phase(self, item_id: str, record: PhaseRecord) -> ActivityItem | None:
        if record.phase != CampaignPhase.BENCH:
            return None
        # Without this bracket the pass's row ticks read as the last candidate scoring again.
        view = record.view
        if not isinstance(view, BenchEnterView):
            if record.event == "exit":
                self._bench = None
            return None
        candidate = view.label or None
        who = "origin" if view.subject == "origin" else f"R{view.round} selection"
        self._bench = _BenchPass(candidate, who, view.rows)
        return ActivityItem(
            id=item_id,
            kind="round",
            icon="◇",
            label=f"{candidate} · bench pass" if candidate else "bench pass",
            detail=f"{who} · {view.rows} held-out rows",
            candidate=candidate,
        )
