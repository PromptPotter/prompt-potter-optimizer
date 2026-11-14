from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar, Literal, NamedTuple, assert_never, get_args

from promptpotter.domain.arms import ArmState, Cut, Measured, Proposed
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.paired_reading import ArmKey, MeasurandUnit, level_text
from promptpotter.domain.phase_views import VerificationEnterView
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase
from promptpotter.domain.records.cell_beat import CellBeatRecord
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    ArmsGate,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundStatement,
    RoundWarningRecord,
    Running,
    RunPhaseRecord,
    SampleScoredRecord,
)
from promptpotter.domain.said import Said, Tone
from promptpotter.domain.spend import prefix_reading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import duration_words

# What the narration reads, `Narration.read` total over it.
NarratedRecord = (
    LLMCallStartRecord
    | LLMCallRecord
    | LLMCallProgressRecord
    | CellBeatRecord
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

# `status` lasts to the next status or run-phase declaration (`tick` too, a heartbeat no ray gives a
# slot), `hold` while its sender is held, `round` to the next round, `run` to the next launch.
Lifetime = Literal["status", "tick", "hold", "round", "run"]


class Narrated(NamedTuple):
    """One record's line, composed once: the readout writes it, the chat keeps it while it lives."""

    said: Said
    lifetime: Lifetime
    round: int | None
    arm: ArmKey | None
    # The narration's clocks when it was said, which `Narration.lives` reads.
    declared: int
    launched: int
    entered: int


class _Line(NamedTuple):
    tone: Tone
    label: str
    detail: str | None = None
    lifetime: Lifetime = "status"
    round: int | None = None
    arm: ArmKey | None = None


class _Announced(NamedTuple):
    state: ArmState
    running: ScoreSummary | None


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


def _joined(bits: Iterable[str | None]) -> str | None:
    return " · ".join(b for b in bits if b) or None


class Narration:
    """The ledger narrated once: each record kind's row, and the arms, labels and holds every row reads.

    Each announced arm's last state and running fold is held by ``(round, key)``; a re-read continues it.
    """

    records: ClassVar[tuple[type[StrictModel], ...]] = get_args(NarratedRecord)

    def __init__(self) -> None:
        self.round: int | None = None
        self.gate: ArmsGate | None = None
        self._arms: dict[tuple[int, ArmKey], _Announced] = {}
        # Each arm's label by its key, off its records: a sample names its individual alone.
        self._labels: dict[ArmKey, str] = {}
        # Each held sender's reading and the row that said it.
        self._held: dict[str, tuple[BackpressureReading, str]] = {}
        self._bench: _BenchPass | None = None
        self._declared = 0
        self._launched = 0
        self._entered = 0

    def read(self, sequence: int, record: NarratedRecord) -> Narrated | None:
        key = f"{record.record_type}-{sequence}"
        if (line := self._line(key, record)) is None:
            return None
        tone, label, detail, lifetime, at, arm = line
        said = Said(key=key, tone=tone, label=label, detail=detail)
        return Narrated(said, lifetime, at, arm, self._declared, self._launched, self._entered)

    def _line(self, key: str, record: NarratedRecord) -> _Line | None:
        """The table: one row per record kind."""
        match record:
            case LLMCallStartRecord():
                return self._call_start(record)
            case LLMCallRecord():
                return self._call(record)
            case LLMCallProgressRecord():
                return self._progress(record, _node_label(record.node, record.round))
            case CellBeatRecord():
                who = None if record.arm is None else self._labels.get(record.arm)
                return self._progress(record, f"{who or 'scoring'} · row {record.position}")
            case ArmRecord():
                return self._arm(record)
            case SampleScoredRecord():
                return self._sample_scored(record)
            case PhaseRecord():
                return self._phase(record)
            case RunPhaseRecord():
                self._run_phase(record)
                return None
            case RoundEnteredRecord():
                self.enter(record.round)
                return None
            case RoundClosedRecord():
                lead = record.run_standing.lead_line
                return _Line("success", f"Round {record.round}", lead, round=record.round)
            case RoundWarningRecord():
                tone: Tone = "danger" if record.severity == "error" else "warn"
                label = record.message or "round degraded"
                return _Line(tone, label, lifetime="round", round=record.round)
            case ErrorRecord():
                stop = STOP_REASON_INFO[record.stop_reason].label
                return _Line("danger", record.message, stop, "run", record.round)
            case ProviderHoldRecord():
                return self._hold(key, record)
            case ProviderEasedRecord():
                if self._held.pop(record.sender, None) is None:
                    return None
                after = f"after {duration_words(record.held_s)}"
                return _Line("success", f"{record.sender} answers again", after)
            case _:
                assert_never(record)

    def lives(self, row: Narrated) -> bool:
        match row.lifetime:
            case "status" | "tick":
                return row.declared == self._declared
            case "hold":
                return any(said == row.said.key for _, said in self._held.values())
            case "round":
                return row.entered == self._entered
            case "run":
                return row.launched == self._launched
            case _:
                assert_never(row.lifetime)

    # -- the arms every reader of a round's walk shares --

    def __iter__(self) -> Iterator[tuple[int, ArmKey]]:
        return iter(self._arms)

    def announce(self, record: ArmRecord) -> None:
        held = self._arms.get((record.round, record.arm))
        self._arms[record.round, record.arm] = _Announced(
            record.state, None if held is None else held.running
        )
        self._labels[record.arm] = record.state.label

    def take(self, record: SampleScoredRecord) -> ArmKey | None:
        """The arm *record*'s cell continues; ``None`` for a pass no arm walks."""
        if (arm := record.arm) is None:
            return None
        if (held := self._arms.get((record.round, arm))) is None:
            raise LookupError(
                f"a round {record.round} cell names arm {arm}, which its own cycle file never "
                "announced: the SSE tail and the ray fold one file, so each file announces its arms"
            )
        if record.running is not None:
            self._arms[record.round, arm] = held._replace(running=record.running)
        return arm

    def close(self, record: RoundStatement, keys: Iterable[ArmKey]) -> None:
        """The statement is each arm's last record: its fold covers the reference reads too."""
        for key in keys:
            self._arms[record.round, key] = _Announced(record.arms[key], record.folds.get(key))

    def enter(self, round_num: int) -> None:
        """Every launch re-enters ``origin`` at round 0 to replay it."""
        self._arms = {at: held for at, held in self._arms.items() if at[0] < round_num}
        if self.round is not None and round_num != self.round:
            self._entered += 1
        self.round = round_num

    def state(self, round_num: int, key: ArmKey) -> ArmState:
        return self._arms[round_num, key].state

    def fold(self, round_num: int, key: ArmKey) -> ScoreSummary | None:
        held = self._arms[round_num, key]
        return None if isinstance(held.state, Proposed) else held.running

    def label(self, key: ArmKey) -> str | None:
        return self._labels.get(key)

    # -- one row per kind --

    def _run_phase(self, record: RunPhaseRecord) -> None:
        # A declaration starts or stops the sends a hold describes; the hold lasts to the next.
        self._declared += 1
        self.gate = record.state if isinstance(record.state, ArmsGate) else None
        if isinstance(record.state, Running):
            self._launched += 1
        self._held.clear()

    def _call_start(self, record: LLMCallStartRecord) -> _Line:
        # The alarm is a REFUSED panel, never a big prompt: a mandatory floor is admitted whatever it costs.
        refused, chars = record.refused_panels, record.prompt_chars
        detail = _joined(
            [
                record.model or "default",
                f"{chars:,}c prompt" if chars > 0 else None,
                f"NO ROOM: {', '.join(refused[:3])}" if refused else None,
            ]
        )
        label = _node_label(record.node, record.round)
        return _Line("warn" if refused else "live", label, detail, round=record.round)

    def _call(self, record: LLMCallRecord) -> _Line:
        payload = record.payload
        replayed = bool(payload.get("cached"))
        bits: list[str] = []
        if (duration := _num(payload.get("duration_s"))) is not None:
            bits.append(f"{duration:.1f}s")
        if (usage := record.usage) is not None:
            if usage.total > 0:
                bits.append(f"{usage.total} tok")
            if usage.reasoning > 0 and usage.output > 0:
                bits.append(f"{usage.reasoning / usage.output:.0%} reasoning")
            prefix = prefix_reading(usage.cache_share(replayed=replayed), replayed=replayed)
            if prefix.state == "unreported":
                bits.append("prefix not reported")
            elif prefix.share is not None:
                bits.append(f"{prefix.share:.0%} prefix cached")
        # "replayed", not "cached": OUR archive served it, and `cached` names the provider's discount.
        if replayed:
            bits.append("replayed")
        flags: list[str] = []
        if retries := len(payload.get("schema_repair_errors") or ()):
            flags.append(f"re-asked {retries}x")
        if payload.get("finish_reason") == "length":
            flags.append("TRUNCATED at max_tokens")
        label = _node_label(record.node, record.round)
        return _Line(
            "warn" if flags else "success", label, _joined([*bits, *flags]), round=record.round
        )

    def _progress(self, record: LLMCallProgressRecord | CellBeatRecord, who: str) -> _Line:
        """``detail`` is said VERBATIM: a bare tick proves only liveness, and a healthy inner campaign reads as frozen."""
        inner = record.inner
        inner_at = None if inner is None else f"inner {inner.campaign_id}/{inner.cycle_id}"
        detail = _joined([duration_words(record.elapsed_s), record.detail, inner_at])
        label = f"{who} still waiting"
        lifetime: Lifetime = "status" if record.detail or inner is not None else "tick"
        return _Line("quiet", label, detail, lifetime, record.round)

    def _hold(self, key: str, record: ProviderHoldRecord) -> _Line | None:
        sender, held = record.sender, record.reading
        shown = self._held.get(sender)
        if shown is not None and shown[0] == held:
            return None
        self._held[sender] = (held, key)
        going = shown is not None and shown[0].since == held.since
        bits = [f"throttle {record.strikes}" if going else record.scope]
        if held.detail and not going:
            bits.append(f'"{held.detail}"')
        if held.at_once is not None:
            bits.append(f"{held.at_once} at once")
        if held.since is not None:
            bits.append(
                f"the run stops if throttled past {duration_words(record.gives_up_at - held.since)}"
            )
        label = f"{'still ' if going else ''}rate-limited · {sender}"
        return _Line("warn", label, _joined(bits), "hold")

    def _arm(self, record: ArmRecord) -> _Line:
        self.announce(record)
        arm, detail = record.state, None
        match arm:
            case Proposed():
                detail = " → ".join(
                    f"{v.node} ({v.mode}): {', '.join(v.loci) or 'nothing'}"
                    for v in arm.individual.lineage.variations
                )
            case Cut() | Measured():
                fold = self.fold(record.round, record.arm)
                detail = None if fold is None else _level(fold.composite_fitness, "score")
        tone: Tone = "success" if isinstance(arm, Cut | Measured) else "quiet"
        return _Line(tone, arm.label, detail or None, round=record.round, arm=record.arm)

    def _sample_scored(self, record: SampleScoredRecord) -> _Line:
        at, running = record.round, record.running
        scored, total = (record.sample_idx or 0) + 1, record.sample_total
        count = f"{scored}/{total}" if total else None
        # A pass no arm walks (a bench pass, a verify) is no candidate of the round.
        if (arm := self.take(record)) is not None:
            label = f"{self._labels[arm]} · scoring"
            level = None if running is None else _level(running.composite_fitness, "score")
            return _Line("quiet", f"{label} {count}" if count else label, level, round=at, arm=arm)
        if self._bench is None:
            return _Line("quiet", f"scoring {count}" if count else "scoring", round=at)
        # The candidate and the count lead: a ray chip truncates.
        arm, candidate, who, rows = self._bench
        tally = f"bench {scored}/{total or ('?' if rows is None else rows)}"
        so_far = None if running is None else _level(running.fitness, running.fitness_unit)
        label = f"{candidate} · {tally}" if candidate else tally
        return _Line("quiet", label, f"{who} · {so_far} so far" if so_far else who, arm=arm)

    def _phase(self, record: PhaseRecord) -> _Line | None:
        if record.event == "enter" and record.round is not None and self.round is None:
            self.round = record.round
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
        label = f"{candidate} · bench pass" if candidate else "bench pass"
        return _Line("quiet", label, f"{who} · {view.rows} held-out rows", arm=view.arm.key)
