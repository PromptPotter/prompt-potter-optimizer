from __future__ import annotations

import copy
from typing import ClassVar, Literal, NamedTuple, assert_never, get_args

from pydantic import Field

from promptpotter.domain.arm_reading import level_text
from promptpotter.domain.arms import Cut, Measured, candidate_label
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.paired_reading import ArmKey, MeasurandUnit
from promptpotter.domain.phase_views import VerificationEnterView
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    ErrorRecord,
    GateKind,
    RunPhase,
)
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundWarningKind,
    RoundWarningRecord,
    RunPhaseRecord,
    SampleScoredRecord,
)
from promptpotter.domain.said import Said, Tone
from promptpotter.domain.spend import prefix_reading
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.clock import duration_words

# What a feed reads, `_item` total over it; a tail never validates any other arm.
ActivityRecord = (
    LLMCallStartRecord
    | LLMCallRecord
    | LLMCallProgressRecord
    | ArmRecord
    | SampleScoredRecord
    | PhaseRecord
    | RunPhaseRecord
    | RoundEnteredRecord
    | RoundClosedRecord
    | RoundWarningRecord
    | ErrorRecord
    | ProviderHoldRecord
    | ProviderEasedRecord
)

ActivityKind = Literal[
    "running",
    "done",
    "candidate",
    "round",
    "warning",
    "error",
    "progress",
]

# `status` lasts to the next status or run-phase declaration, `round` to the next round, `run` to the next launch.
Lifetime = Literal["status", "round", "run"]
LIFETIME: dict[ActivityKind, Lifetime] = {
    "running": "status",
    "done": "status",
    "candidate": "status",
    "round": "status",
    "progress": "status",
    "warning": "round",
    "error": "run",
}


class ActivityItem(FrozenModel):
    """One record's reading: a line to show, and the round and candidate it is about."""

    id: str = Field(description="The record's kind and ledger offset; one record, one id.")
    kind: ActivityKind
    icon: str
    label: str
    detail: str | None = Field(default=None, description="The quieter half of the line.")
    tone: Tone = "quiet"
    round: int | None = Field(default=None, description="The round the record names.")
    arm: ArmKey | None = Field(
        default=None, description="The arm the record is about, by its key; `label` renders it."
    )
    healed: RoundWarningKind | None = Field(
        default=None, description="The self-heal a round warning reports; null on every other item."
    )


class ActivityState(FrozenModel):
    """A run's current state: what it is doing, and what was said that still holds."""

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


class KeepDraftsAction(FrozenModel):
    """One button: a press sends `keep` as `kind` to the cycle holding for its round's arms."""

    label: str
    variant: Literal["primary", "ghost", "danger"]
    kind: Literal[CommandKind.SUBMIT_ARMS]
    keep: list[int] = Field(description="The drafts measured as proposed, by index at the hold.")


class ActivityDecision(FrozenModel):
    """A choice the run is holding for: what to say about it, and the commands that answer it."""

    title: str
    lead: str = Field(description="The paragraph under the title.")
    facts: list[Said] = Field(description="The evidence, in reading order.")
    reasons: list[str] = Field(description="Why the run is holding, one fragment each.")
    actions: list[KeepDraftsAction]


def _arms_gate(round_num: int | None, drafts: list[Said]) -> ActivityDecision:
    """One button keeps every draft; an arm of the operator's own is written text."""
    held = "The round" if round_num is None else f"Round {round_num}"
    drafted = (
        "Keep a draft below with `--keep <index>`, add arms of your own in the file, or both."
        if drafts
        else "No proposer ran: every arm this round measures is yours."
    )
    return ActivityDecision(
        title=f"{held} holds for your arms",
        lead=f"{held} measures nothing until you answer with `submit-arms`. {drafted}",
        facts=drafts,
        reasons=[],
        actions=[
            KeepDraftsAction(
                label="Measure the drafts",
                variant="primary",
                kind=CommandKind.SUBMIT_ARMS,
                keep=list(range(len(drafts))),
            )
        ]
        if drafts
        else [],
    )


class _BenchPass(NamedTuple):
    arm: ArmKey
    label: str | None
    who: str
    rows: int | None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _level(value: float | None, unit: MeasurandUnit) -> str | None:
    return None if value is None else level_text(value, unit)


def _node_label(node: str, at: int | None) -> str:
    return node if at is None else f"{node}·r{at}"


class ActivityFeed:
    records: ClassVar[tuple[type[StrictModel], ...]] = get_args(ActivityRecord)

    def __init__(self) -> None:
        self.at_offset = -1
        self._status: ActivityItem | None = None
        self._notices: list[ActivityItem] = []
        self._round: int | None = None
        self._bench: _BenchPass | None = None
        self._gate: GateKind | None = None
        self._offered: list[Said] = []
        # Each held sender's notice id.
        self._held: dict[str, str] = {}
        # Each arm's label by its key, off its records: a sample names its individual alone.
        self._labels: dict[ArmKey, str] = {}
        self._arms = AnnouncedArms()

    def state(self) -> ActivityState:
        decision = _arms_gate(self._round, self._offered) if self._gate == "arms" else None
        return ActivityState(status=self._status, notices=list(self._notices), decision=decision)

    def feed(self, record: ActivityRecord) -> None:
        self.read(self.at_offset, record)

    def value(self) -> ActivityFeed:
        """A private copy, which a tail reads on from."""
        return copy.deepcopy(self)

    def read(self, sequence: int, record: ActivityRecord) -> ActivityItem | None:
        self._declared(record)
        item = self._item(f"{record.record_type}-{sequence}", record)
        if item is None:
            return None
        if LIFETIME[item.kind] == "status":
            self._status = item
        else:
            self._notices.append(item)
        return item

    def _declared(self, record: ActivityRecord) -> None:
        """``RoundEnteredRecord`` alone MOVES the run: every launch re-enters ``origin`` at round 0 to replay it."""
        match record:
            case RunPhaseRecord():
                self._status = None
                # The hold lasts to the next declaration.
                self._gate = record.gate
                self._offered = [
                    Said(
                        key=f"draft_{idx}",
                        tone="quiet",
                        label=f"draft {idx} · {candidate_label(self._round or 0, idx)}",
                        value=draft.individual.lineage.changes_description or "—",
                    )
                    for idx, draft in enumerate(record.drafts)
                ]
                if record.run_phase == RunPhase.RUNNING:
                    self._notices = [n for n in self._notices if LIFETIME[n.kind] != "run"]
                # A declaration starts or stops the sends a hold notice describes.
                self._notices = [n for n in self._notices if n.id not in self._held.values()]
                self._held.clear()
            case RoundEnteredRecord():
                self._arms.enter(record)
                if self._round is not None and record.round != self._round:
                    self._notices = [n for n in self._notices if LIFETIME[n.kind] == "run"]
                self._round = record.round
            case PhaseRecord(event="enter", round=int() as opened) if self._round is None:
                self._round = opened
            case (
                PhaseRecord()
                | LLMCallStartRecord()
                | LLMCallRecord()
                | LLMCallProgressRecord()
                | ArmRecord()
                | SampleScoredRecord()
                | RoundClosedRecord()
                | RoundWarningRecord()
                | ErrorRecord()
                | ProviderHoldRecord()
                | ProviderEasedRecord()
            ):
                pass
            case _:
                assert_never(record)

    def _item(self, item_id: str, record: ActivityRecord) -> ActivityItem | None:
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
            case ArmRecord():
                return self._arm(item_id, record)
            case SampleScoredRecord():
                return self._sample_scored(item_id, record)
            case PhaseRecord():
                return self._phase(item_id, record)
            case RunPhaseRecord() | RoundEnteredRecord():
                # Read by `_declared` alone, so a feed still takes them.
                return None
            case RoundClosedRecord():
                return ActivityItem(
                    id=item_id,
                    kind="round",
                    icon="★",
                    label=f"Round {record.round}",
                    detail=record.run_standing.lead_line,
                    tone="success",
                    round=record.round,
                )
            case RoundWarningRecord():
                error = record.severity == "error"
                return ActivityItem(
                    id=item_id,
                    kind="warning",
                    icon="✗" if error else "⚠",
                    label=record.message or "round degraded",
                    tone="danger" if error else "warn",
                    round=record.round,
                    healed=record.kind,
                )
            case ErrorRecord():
                return ActivityItem(
                    id=item_id,
                    kind="error",
                    icon="✗",
                    label=record.message,
                    detail=STOP_REASON_INFO[record.stop_reason].label,
                    tone="danger",
                    round=record.round,
                )
            case ProviderHoldRecord():
                return self._hold(item_id, record)
            case ProviderEasedRecord():
                return self._eased(item_id, record)
            case LLMCallProgressRecord():
                waiting = record.detail or (
                    None if record.inner is None else f"inner campaign {record.inner.campaign_id}"
                )
                if not waiting:
                    return None
                return ActivityItem(
                    id=item_id,
                    kind="progress",
                    icon="·",
                    label=waiting,
                    detail=duration_words(record.elapsed_s),
                    round=record.round,
                )
            case _:
                assert_never(record)

    @staticmethod
    def _call(item_id: str, record: LLMCallRecord) -> ActivityItem:
        inner = record.payload
        replayed = bool(inner.get("cached"))
        bits: list[str] = []
        if (duration := _num(inner.get("duration_s"))) is not None:
            bits.append(f"{duration:.1f}s")
        if (usage := record.usage) is not None:
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

    def _eased(self, item_id: str, record: ProviderEasedRecord) -> ActivityItem | None:
        notice = self._held.pop(record.sender, None)
        if notice is None:
            return None
        self._notices = [n for n in self._notices if n.id != notice]
        return ActivityItem(
            id=item_id,
            kind="done",
            icon="✓",
            label=f"{record.sender} answers again",
            detail=f"after {duration_words(record.held_s)}",
            tone="success",
        )

    def _hold(self, item_id: str, record: ProviderHoldRecord) -> ActivityItem | None:
        if record.sender in self._held:
            return None
        self._held[record.sender] = item_id
        held = record.reading
        bits = [record.scope, held.detail] if held.detail else [record.scope]
        if held.since is not None:
            window = duration_words(record.gives_up_at - held.since)
            bits.append(f"the run stops if throttled past {window}")
        return ActivityItem(
            id=item_id,
            kind="warning",
            icon="⚠",
            label=f"rate-limited · {record.sender}",
            detail=" · ".join(bits),
            tone="warn",
        )

    def _arm(self, item_id: str, record: ArmRecord) -> ActivityItem:
        arm = record.state
        label = arm.label
        self._labels[record.arm] = label
        self._arms.announce(record)
        fold = self._arms.fold(record.round, record.arm)
        return ActivityItem(
            id=item_id,
            kind="candidate",
            icon="◆",
            label=label,
            detail=_level(fold.composite_fitness, "score")
            if isinstance(arm, Cut | Measured) and fold is not None
            else None,
            tone="success",
            round=record.round,
            arm=record.arm,
        )

    def _sample_scored(self, item_id: str, record: SampleScoredRecord) -> ActivityItem:
        at = record.round
        scored, total = (record.sample_idx or 0) + 1, record.sample_total
        count = f"{scored}/{total}" if total else None
        running = record.running
        label = None if (arm := self._arms.take(record)) is None else self._labels[arm]
        # A pass no arm walks (a bench pass, a verify) is no candidate of the round.
        if label is None:
            if self._bench is None:
                return ActivityItem(
                    id=item_id,
                    kind="progress",
                    icon="·",
                    label=f"scoring {count}" if count else "scoring",
                    round=at,
                )
            # The candidate and the count lead: a ray chip truncates.
            arm, candidate, who, rows = self._bench
            rows = total or rows
            tally = f"bench {scored}/{'?' if rows is None else rows}"
            so_far = None if running is None else _level(running.fitness, running.fitness_unit)
            return ActivityItem(
                id=item_id,
                kind="progress",
                icon="·",
                label=f"{candidate} · {tally}" if candidate else tally,
                detail=f"{who} · {so_far} so far" if so_far else who,
                arm=arm,
            )
        return ActivityItem(
            id=item_id,
            kind="progress",
            icon="·",
            label=f"{label} · scoring {count}" if count else f"{label} · scoring",
            detail=None if running is None else _level(running.composite_fitness, "score"),
            round=at,
            arm=record.arm,
        )

    def _phase(self, item_id: str, record: PhaseRecord) -> ActivityItem | None:
        if record.phase != CampaignPhase.BENCH:
            return None
        # Without this bracket the pass's row ticks read as the last candidate scoring again.
        view = record.view
        if not isinstance(view, VerificationEnterView):
            if record.event == "exit":
                self._bench = None
            return None
        candidate = view.arm.label or None
        who = "origin" if view.subject == "origin" else f"R{view.arm.round} selection"
        self._bench = _BenchPass(view.arm.key, candidate, who, view.rows)
        return ActivityItem(
            id=item_id,
            kind="round",
            icon="◇",
            label=f"{candidate} · bench pass" if candidate else "bench pass",
            detail=f"{who} · {view.rows} held-out rows",
            arm=view.arm.key,
        )
