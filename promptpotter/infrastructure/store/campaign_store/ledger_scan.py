from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, get_args

from promptpotter.domain.bench import BenchPasses, BenchReading
from promptpotter.domain.opt_search_point import IndividualLineage
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.phase_views import BenchGradedView, VerifyEnterView, VerifyGradedView
from promptpotter.domain.phases import CampaignPhase, RunPhase
from promptpotter.domain.results import (
    ArmOutcome,
    IndividualWalk,
    RoundCells,
    RunStanding,
    ScoredCandidate,
    VerifyPass,
    VerifyReading,
)
from promptpotter.domain.ruler import DeltaRuler
from promptpotter.domain.run_records import (
    LOOP_COMMAND_KINDS,
    CandidateMintedRecord,
    CandidateScoredRecord,
    CandidateStartedRecord,
    CandidateState,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleSeed,
    CycleSeedRecord,
    CycleSupersededRecord,
    ElectionRecord,
    ForkDirection,
    ForkGradedRecord,
    ForkSpec,
    InterventionRecord,
    LaunchClaimRecord,
    LedgerCandidate,
    OptimizerStateRecord,
    PhaseRecord,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundProposedRecord,
    RoundStandingRecord,
    RunLimitsRecord,
    RunPhaseRecord,
    RunWiringRecord,
    ScoringLockedRecord,
    SpawnedBy,
    SpawnedRecord,
    TokenUsageRecord,
    WallClock,
    scored_cell,
)
from promptpotter.domain.scoring import MeasuredCell, WalkedCell
from promptpotter.domain.spend import CloseSpend, SpendRollup, TokenUsageKind
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
    iter_jsonl,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.measurement_context import MeasurementRole


class _CycleSeed:
    probes: ClassVar[frozenset[str]] = frozenset({"cycle_seed"})

    def __init__(self) -> None:
        self._found: CycleSeed | None = None

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") == "cycle_seed":
            self._found = CycleSeedRecord.model_validate(rec).seed

    def value(self) -> CycleSeed | None:
        return self._found


class _RunLimits:
    probes: ClassVar[frozenset[str]] = frozenset({"run_limits"})

    def __init__(self) -> None:
        self._found = RunLimitsRecord()

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") == "run_limits":
            self._found = RunLimitsRecord.model_validate(rec)

    def value(self) -> RunLimitsRecord:
        return self._found.model_copy()


class CycleFacts(NamedTuple):
    minted: CycleMintedRecord | None = None
    checkin_closed: bool = False
    ended: RunPhaseRecord | None = None
    paused: RunPhaseRecord | None = None
    final: CycleFinalRecord | None = None
    superseded_by: str | None = None
    crash_traceback: str | None = None
    direction: ForkDirection | None = None
    interventions: tuple[InterventionRecord, ...] = ()
    spawned_by: SpawnedBy | None = None
    formula: str | None = None
    updated_at: str = ""

    @property
    def checkin(self) -> bool:
        return self.minted is not None and self.minted.checkin and not self.checkin_closed

    @property
    def fork(self) -> ForkSpec | None:
        spec = None if self.minted is None else self.minted.fork
        if spec is None or self.direction is None:
            return spec
        return spec.model_copy(update={"direction": self.direction})


class _CycleFacts:
    probes: ClassVar[frozenset[str]] = frozenset(
        {
            "cycle_minted",
            "checkin_closed",
            "cycle_final",
            "cycle_superseded",
            "fork_graded",
            "intervention",
            "spawned",
            "run_phase",
            "phase",
            "round_standing",
            "error",
        }
    )

    def __init__(self) -> None:
        self._held = CycleFacts()

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        held = self._held
        match rec.get("record_type"):
            case "cycle_minted":
                held = held._replace(minted=CycleMintedRecord.model_validate(rec))
            case "checkin_closed":
                held = held._replace(checkin_closed=True)
            case "run_phase":
                declared = RunPhaseRecord.model_validate(rec)
                if declared.run_phase is RunPhase.TERMINAL:
                    held = held._replace(ended=declared, paused=None)
                else:
                    held = held._replace(
                        ended=None,
                        paused=declared if declared.run_phase is RunPhase.PAUSED else None,
                        final=None,
                        superseded_by=None,
                        crash_traceback=None,
                    )
            case "error":
                trace = rec.get("traceback")
                held = held._replace(crash_traceback=trace if isinstance(trace, str) else None)
            case "cycle_final":
                held = held._replace(final=CycleFinalRecord.model_validate(rec))
            case "cycle_superseded":
                moved = CycleSupersededRecord.model_validate(rec)
                held = held._replace(superseded_by=moved.successor_cycle_id)
            case "fork_graded":
                held = held._replace(direction=ForkGradedRecord.model_validate(rec).direction)
            case "intervention":
                act = InterventionRecord.model_validate(rec)
                held = held._replace(interventions=(*held.interventions, act))
            case "spawned":
                held = held._replace(spawned_by=SpawnedRecord.model_validate(rec).spawned_by)
            case "phase":
                view = rec.get("view")
                formula = view.get("composite_fitness_formula") if isinstance(view, dict) else None
                if rec.get("phase") != CampaignPhase.INIT or not isinstance(formula, str):
                    return
                held = held._replace(formula=formula)
            case "round_standing":
                pass
            case _:
                return
        stamp = rec.get("timestamp")
        self._held = held._replace(updated_at=stamp if isinstance(stamp, str) else held.updated_at)

    def value(self) -> CycleFacts:
        return self._held


class PauseAsk(NamedTuple):
    command_id: str
    issued_by: str
    at: str


class LookaheadAsk(NamedTuple):
    command_id: str
    cells: int
    auto: bool
    taken: bool = False


class Controls(NamedTuple):
    pause: PauseAsk | None = None
    skips: tuple[str, ...] = ()
    lookahead: LookaheadAsk | None = None
    gate_decisions: tuple[tuple[str, str], ...] = ()


class _Controls:
    probes: ClassVar[frozenset[str]] = frozenset({"command", "command_ack", "run_phase"})

    def __init__(self) -> None:
        self._held = Controls()
        self._asked: dict[str, dict[str, Any]] = {}
        self._kinds: dict[str, str] = {}
        self._declared = ""

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        match rec.get("record_type"):
            case "command":
                command_id = rec.get("command_id")
                if rec.get("kind") in LOOP_COMMAND_KINDS and isinstance(command_id, str):
                    self._asked[command_id] = rec
            case "command_ack":
                command_id, status = rec.get("command_id"), rec.get("status")
                if not isinstance(command_id, str):
                    return
                if status == "accepted" and (asked := self._asked.pop(command_id, None)):
                    self._arm(command_id, asked)
                elif status == "rejected":
                    self._asked.pop(command_id, None)
                elif status == "applied":
                    if asked := self._asked.pop(command_id, None):
                        # Applied with no accept before it: a look-ahead DISARM, nothing to take.
                        self._arm(command_id, asked)
                    elif kind := self._kinds.pop(command_id, None):
                        self._take(command_id, kind)
            case "run_phase":
                declared = str(rec.get("run_phase", ""))
                launched = declared == RunPhase.RUNNING and self._declared != RunPhase.GATE
                if launched or declared in (RunPhase.PAUSED, RunPhase.TERMINAL):
                    # A stop or a new launch empties the inbox; an ``auto`` look-ahead is a mode.
                    kept = self._held.lookahead
                    kept = kept if kept is not None and kept.auto else None
                    self._held = Controls(lookahead=kept)
                    self._kinds = {} if kept is None else {kept.command_id: _LOOKAHEAD}
                self._declared = declared

    def _arm(self, command_id: str, asked: dict[str, Any]) -> None:
        kind, held = str(asked.get("kind")), self._held
        payload = asked.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        match kind:
            case "pause-cycle":
                if held.pause is not None:
                    return
                held = held._replace(
                    pause=PauseAsk(
                        command_id,
                        str(asked.get("issued_by_user_id") or ""),
                        str(asked.get("timestamp") or ""),
                    )
                )
            case "skip-searchpoint":
                held = held._replace(skips=(*held.skips, command_id))
            case "origin-gate-decision":
                held = held._replace(
                    gate_decisions=(
                        *held.gate_decisions,
                        (command_id, str(payload.get("decision"))),
                    )
                )
            case "set-sample-lookahead":
                cells, auto = payload.get("cells"), payload.get("auto") is True
                if held.lookahead is not None:
                    self._kinds.pop(held.lookahead.command_id, None)
                depth = cells if isinstance(cells, int) and not isinstance(cells, bool) else 1
                armed = auto or depth > 1
                held = held._replace(
                    lookahead=LookaheadAsk(command_id, max(1, depth), auto) if armed else None
                )
                if not armed:
                    self._held = held
                    return
            case _:
                return
        self._kinds[command_id] = kind
        self._held = held

    def _take(self, command_id: str, kind: str) -> None:
        held = self._held
        match kind:
            case "pause-cycle":
                held = held._replace(pause=None)
            case "skip-searchpoint":
                held = held._replace(skips=tuple(s for s in held.skips if s != command_id))
            case "origin-gate-decision":
                held = held._replace(
                    gate_decisions=tuple(d for d in held.gate_decisions if d[0] != command_id)
                )
            case "set-sample-lookahead":
                ask = held.lookahead
                if ask is not None and ask.auto:
                    held = held._replace(lookahead=ask._replace(taken=True))
                else:
                    held = held._replace(lookahead=None)
        self._held = held

    def value(self) -> Controls:
        return self._held


_LOOKAHEAD = "set-sample-lookahead"


class _LaunchClaim:
    probes: ClassVar[frozenset[str]] = frozenset({"launch_claim", "launch_released", "run_phase"})

    def __init__(self) -> None:
        self._found: LaunchClaimRecord | None = None

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        kind = rec.get("record_type")
        if kind == "run_phase":
            self._found = None
        elif kind == "launch_claim":
            self._found = LaunchClaimRecord.model_validate(rec)
        elif self._found is not None and rec.get("job_id") == self._found.job_id:
            self._found = None

    def value(self) -> LaunchClaimRecord | None:
        return self._found


class _Rulers:
    probes: ClassVar[frozenset[str]] = frozenset({"ruler"})

    def __init__(self) -> None:
        self._raw: dict[str, dict[str, Any]] = {}

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        name, data = rec.get("dataset_name"), rec.get("ruler")
        if rec.get("record_type") == "ruler" and isinstance(name, str) and isinstance(data, dict):
            self._raw[name] = data

    def value(self) -> dict[str, dict[str, Any]]:
        return dict(self._raw)


class Progress(NamedTuple):
    progressed_at: float | None = None
    waiting_since: float | None = None


class _Progress:
    # Screens nothing, so it rides an index of its own (``PROGRESS_FOLDS``), never LEDGER_FOLDS.
    probes: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        self._held = Progress()

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        kind = rec.get("record_type")
        # A heartbeat proves the process alive, not that it progressed.
        if kind == "llm_call_progress":
            return
        held = self._held
        if kind == "flight":
            waiting = rec.get("waiting")
            since = waiting.get("since") if isinstance(waiting, dict) else None
            held = held._replace(
                waiting_since=float(since) if isinstance(since, int | float) else None
            )
        elif kind == "run_phase":
            held = held._replace(waiting_since=None)
        at = epoch_seconds(rec.get("timestamp"))
        self._held = held if at is None else held._replace(progressed_at=at)

    def value(self) -> Progress:
        return self._held


class ArmWalk(NamedTuple):
    cells: dict[str, WalkedCell]
    # ``None`` for an arm the round CARRIED: never minted into it, named by its score report.
    minted: CandidateMintedRecord | None = None
    announced: CandidateStartedRecord | None = None
    report: ScoredCandidate | None = None
    walk_length: int | None = None

    @property
    def candidate_id(self) -> str:
        if self.report is not None:
            return self.report.candidate_id
        return "" if self.minted is None else self.minted.candidate_id

    @property
    def label(self) -> str:
        if self.report is not None:
            return self.report.label
        return "" if self.minted is None else self.minted.label

    @property
    def state(self) -> CandidateState:
        if self.report is None:
            return "minted"
        return "invalid" if self.report.outcome == ArmOutcome.INVALID else "measured"


class StandingRound(NamedTuple):
    close: RoundClosedRecord
    at_offset: int
    standing: RunStanding | None = None


_Pass = tuple[int, str, MeasurementRole]


def _before[V](held: Mapping[Any, V], rnd: int | None) -> dict[Any, V]:
    if rnd is None:
        return dict(held)
    return {k: v for k, v in held.items() if (k if isinstance(k, int) else k[0]) < rnd}


class StandingRounds(NamedTuple):
    rounds: dict[int, StandingRound]
    proposals: dict[int, RoundProposedRecord]
    restated: dict[int, OptimizerState]
    arms: dict[tuple[int, int], ArmWalk]
    passes: dict[_Pass, dict[str, WalkedCell]]
    # No entry = never elected; elected-and-crowned-nobody is an entry with no selected label.
    elections: dict[int, ElectionRecord]
    # Keyed on the record's round STAMP, never ledger position: round 0 closes twice.
    decisions: dict[int, list[ResumeCheckpointRecord]]
    entered: int | None = None
    rewound: int | None = None

    @classmethod
    def none(cls) -> StandingRounds:
        return cls({}, {}, {}, {}, {}, {}, {})

    def displaced(self, entered: int | None, rewound: int | None) -> StandingRounds:
        return self._replace(
            rounds=_before(self.rounds, entered),
            proposals=_before(self.proposals, rewound),
            restated=_before(self.restated, entered),
            arms=_before(self.arms, entered),
            passes=_before(self.passes, entered),
            elections=_before(self.elections, entered),
            decisions=_before(self.decisions, entered),
        )

    def then(self, later: StandingRounds) -> StandingRounds:
        prefix = self.displaced(later.entered, later.rewound)
        for n, state in later.restated.items():
            if (held := prefix.rounds.get(n)) is not None:
                prefix.rounds[n] = held._replace(
                    close=held.close.model_copy(update={"optimizer_state": state})
                )
        for n, made in later.decisions.items():
            prefix.decisions[n] = [*prefix.decisions.get(n, []), *made]
        # A round closed again without being entered (round 0, ruler warm) keeps its first standing.
        closed = {
            n: held._replace(standing=kept.standing)
            if held.standing is None and (kept := prefix.rounds.get(n)) is not None
            else held
            for n, held in later.rounds.items()
        }
        return StandingRounds(
            rounds=dict(sorted({**prefix.rounds, **closed}.items())),
            proposals={**prefix.proposals, **later.proposals},
            restated={},
            arms=dict(sorted({**prefix.arms, **later.arms}.items())),
            passes={**prefix.passes, **later.passes},
            elections={**prefix.elections, **later.elections},
            decisions=prefix.decisions,
            entered=later.entered if self.entered is None else self.entered,
            rewound=later.rewound if self.rewound is None else self.rewound,
        )

    @property
    def standing(self) -> RunStanding | None:
        return self.rounds[max(self.rounds)].standing if self.rounds else None

    @property
    def crowns(self) -> dict[int, str]:
        return {
            n: election.selected_labels[0]
            for n, election in self.elections.items()
            if election.selected_labels
        }

    def candidates(self) -> list[LedgerCandidate]:
        """``(round, idx)`` is the join, NOT the id: an individual can be an arm of several rounds."""
        return [
            LedgerCandidate(
                round=rnd,
                idx=idx,
                candidate_id=arm.candidate_id,
                label=arm.label,
                lineage=IndividualLineage() if arm.minted is None else arm.minted.lineage,
                walk_length=arm.walk_length,
                report=arm.report,
            )
            for (rnd, idx), arm in self.arms.items()
            if arm.candidate_id and arm.label
        ]


def _scored_facts(rec: dict[str, Any]) -> MeasuredCell:
    return scored_cell(rec["result"])[0]


def _walked_cell(rec: dict[str, Any]) -> WalkedCell | None:
    facts = _scored_facts(rec)
    if not facts.sample_key or facts.answer is None:
        return None
    return facts.sample_key, facts.sample_id, facts.answer, facts.cached


def _earliest(held: int | None, rnd: int | None) -> int | None:
    if held is None or rnd is None:
        return rnd if held is None else held
    return min(held, rnd)


def _take(taken: dict[str, WalkedCell], cell: WalkedCell) -> None:
    # Popped first: the cell moves to where the walk last read it.
    taken.pop(cell[0], None)
    taken[cell[0]] = cell


class _Standing:
    probes: ClassVar[frozenset[str]] = frozenset(
        {
            "round_entered",
            "candidate_minted",
            "candidate_started",
            "candidate_scored",
            "sample_scored",
            "election",
            "decision",
            "round_closed",
            "round_standing",
            "round_proposed",
            "optimizer_state",
        }
    )

    def __init__(self) -> None:
        self._held = StandingRounds.none()

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        kind, rnd = rec.get("record_type"), rec.get("round")
        if not isinstance(rnd, int):
            return
        held = self._held
        match kind:
            case "round_entered":
                rewound = rnd if rec.get("rewound") is True else None
                self._held = held.displaced(rnd, rewound)._replace(
                    entered=_earliest(held.entered, rnd),
                    rewound=_earliest(held.rewound, rewound),
                )
            case "election":
                held.elections[rnd] = ElectionRecord.model_validate(rec)
            case "decision":
                made = ResumeCheckpointRecord.model_validate(rec)
                held.decisions.setdefault(rnd, []).append(made)
            case "round_closed":
                close = RoundClosedRecord.model_validate(rec)
                before = held.rounds.get(rnd)
                held.rounds[rnd] = StandingRound(
                    close, offset, None if before is None else before.standing
                )
                held.restated.pop(rnd, None)
            case "round_standing":
                stood = RoundStandingRecord.model_validate(rec)
                if (closed := held.rounds.get(rnd)) is not None:
                    held.rounds[rnd] = closed._replace(standing=stood.run_standing)
            case "round_proposed":
                held.proposals[rnd] = RoundProposedRecord.model_validate(rec)
            case "optimizer_state":
                restated = OptimizerStateRecord.model_validate(rec)
                if (closed := held.rounds.get(rnd)) is None:
                    held.restated[rnd] = restated.optimizer_state
                else:
                    held.rounds[rnd] = closed._replace(
                        close=closed.close.model_copy(
                            update={"optimizer_state": restated.optimizer_state}
                        )
                    )
            case "candidate_minted" | "candidate_started" | "candidate_scored" | "sample_scored":
                self._walk(kind, rnd, rec)

    def _walk(self, kind: str, rnd: int, rec: dict[str, Any]) -> None:
        held = self._held
        idx = rec.get("idx") if kind == "candidate_minted" else rec.get("candidate_idx")
        if not isinstance(idx, int):
            return
        cell = _walked_cell(rec) if kind == "sample_scored" else None
        if idx < 0:
            if cell is not None:
                passed = (rnd, str(rec["individual_id"]), MeasurementRole(rec["role"]))
                _take(held.passes.setdefault(passed, {}), cell)
            return
        key = (rnd, idx)
        if kind == "candidate_minted":
            # A mint REPLACES its slot: the origin is minted again on every launch.
            held.arms[key] = ArmWalk({}, CandidateMintedRecord.model_validate(rec))
            return
        arm = held.arms.setdefault(key, ArmWalk({}))
        if kind == "candidate_started":
            held.arms[key] = arm._replace(announced=CandidateStartedRecord.model_validate(rec))
        elif kind == "candidate_scored":
            held.arms[key] = arm._replace(report=CandidateScoredRecord.model_validate(rec).scores)
        elif cell is not None:
            _take(arm.cells, cell)
            if isinstance(length := rec.get("sample_total"), int):
                held.arms[key] = arm._replace(walk_length=length)

    def value(self) -> StandingRounds:
        held = self._held
        return held._replace(
            rounds=dict(held.rounds),
            proposals=dict(held.proposals),
            restated=dict(held.restated),
            arms={key: arm._replace(cells=dict(arm.cells)) for key, arm in held.arms.items()},
            passes={k: dict(taken) for k, taken in held.passes.items()},
            elections=dict(held.elections),
            decisions={n: list(made) for n, made in held.decisions.items()},
        )


class _DeclaredPhase:
    probes: ClassVar[frozenset[str]] = frozenset({"run_phase"})

    def __init__(self) -> None:
        self._declared = ""

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") == "run_phase":
            self._declared = str(rec.get("run_phase", ""))

    def value(self) -> str:
        return self._declared


class VerifyLedger(NamedTuple):
    graded: dict[str, tuple[VerifyPass, VerifyReading]]
    open: VerifyEnterView | None
    # EVERY graded pass, oldest first: a later one buys other cells and retires none.
    passes: list[VerifyPass]


class _Verify:
    probes: ClassVar[frozenset[str]] = frozenset({"phase"})

    def __init__(self) -> None:
        self._graded: dict[str, tuple[VerifyPass, VerifyReading]] = {}
        self._open: VerifyEnterView | None = None
        self._passes: list[VerifyPass] = []

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") != "phase" or rec.get("phase") != CampaignPhase.VERIFY:
            return
        record = PhaseRecord.model_validate(rec)
        match record.view:
            case VerifyEnterView() as entered:
                self._open = entered
            case VerifyGradedView(verify_pass=banked, reading=reading):
                self._graded[banked.label] = banked, reading
                self._passes.append(banked)
        if record.event == "exit":
            self._open = None

    def value(self) -> VerifyLedger:
        return VerifyLedger(dict(self._graded), self._open, list(self._passes))


class LedgerSpend(NamedTuple):
    spend: SpendRollup
    calls: int
    worked_s: float


class _RoundSpend(NamedTuple):
    by_round: dict[int, SpendRollup]
    calls: int
    worked_s: float


class _Spend:
    probes: ClassVar[frozenset[str]] = frozenset({"token_usage"})

    def __init__(self) -> None:
        self._by_round: dict[int, SpendRollup] = {}
        self._calls = 0
        self._worked_s = 0.0

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") != "token_usage":
            return
        usage = TokenUsageRecord.model_validate(rec)
        self._by_round.setdefault(usage.spend_round, SpendRollup()).bank(usage)
        if not usage.cached:
            self._calls += 1
            self._worked_s += usage.duration_s

    def value(self) -> _RoundSpend:
        return _RoundSpend(
            {n: held.model_copy(deep=True) for n, held in self._by_round.items()},
            self._calls,
            self._worked_s,
        )


class _RunWiring:
    probes: ClassVar[frozenset[str]] = frozenset({"run_wiring"})

    def __init__(self) -> None:
        self._found: RunWiringRecord | None = None

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") == "run_wiring":
            self._found = RunWiringRecord.model_validate(rec)

    def value(self) -> RunWiringRecord | None:
        return self._found


class _Bench:
    probes: ClassVar[frozenset[str]] = frozenset({"phase"})

    def __init__(self) -> None:
        self._graded: list[BenchGradedView] = []

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") != "phase" or rec.get("phase") != CampaignPhase.BENCH:
            return
        if rec.get("event") != "graded":
            return
        record = PhaseRecord.model_validate(rec)
        if isinstance(record.view, BenchGradedView):
            self._graded.append(record.view)

    def value(self) -> list[BenchGradedView]:
        return list(self._graded)


class _ScoringLock:
    probes: ClassVar[frozenset[str]] = frozenset({"scoring_locked"})

    def __init__(self) -> None:
        self._found: ScoringLockedRecord | None = None

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        if rec.get("record_type") == "scoring_locked":
            self._found = ScoringLockedRecord.model_validate(rec)

    def value(self) -> ScoringLockedRecord | None:
        return self._found


LEDGER_FOLDS = (
    _CycleSeed,
    _RunLimits,
    _CycleFacts,
    _Controls,
    _LaunchClaim,
    _Rulers,
    _Standing,
    _DeclaredPhase,
    _Verify,
    _Spend,
    _RunWiring,
    _Bench,
    _ScoringLock,
)


def _view[V](ledger_path: Path, fold: Callable[[], LedgerFold[V]]) -> V:
    return LedgerIndex.of(ledger_path, LEDGER_FOLDS).view(fold)


def scan_ledger_cycle_seed(ledger_path: Path) -> CycleSeed | None:
    return _view(ledger_path, _CycleSeed)


def scan_ledger_run_limits(ledger_path: Path) -> RunLimitsRecord:
    return _view(ledger_path, _RunLimits)


def scan_ledger_controls(ledger_path: Path) -> Controls:
    return _view(ledger_path, _Controls)


def scan_launch_claim(ledger_path: Path) -> LaunchClaimRecord | None:
    return _view(ledger_path, _LaunchClaim)


def scan_ledger_scoring_lock(ledger_path: Path) -> ScoringLockedRecord | None:
    return _view(ledger_path, _ScoringLock)


def scan_bench_passes(spans: Iterable[LedgerSpan]) -> BenchPasses | None:
    held: BenchPasses | None = None
    for span in spans:
        for view in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Bench, span.until):
            taken = view.bench_pass
            if view.subject == "selected":
                if held is not None:
                    held = held.model_copy(
                        update={"selections": {**held.selections, taken.round: taken}}
                    )
            elif held is None or held.origin != taken:
                # A different origin pass is a new reference: no earlier selection pairs on it.
                held = BenchPasses(
                    tolerance=view.tolerance,
                    origin=taken,
                    reserve_usd=view.reserve_usd or 0.0,
                    reserve_tokens=view.reserve_tokens or 0,
                    selections={},
                )
    return held


def scan_bench_readings(span: LedgerSpan) -> dict[tuple[int, str], BenchReading]:
    return {
        (view.reading.round, view.label): view.reading
        for view in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Bench, span.until)
        if view.reading is not None
    }


PROGRESS_FOLDS = (_Progress,)


def scan_cycle_facts(ledger_path: Path, until: int | None = None) -> CycleFacts:
    return LedgerIndex.of(ledger_path, LEDGER_FOLDS).view(_CycleFacts, until)


def scan_cell_formula(spans: Iterable[LedgerSpan]) -> str | None:
    formula: str | None = None
    for span in spans:
        held = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_CycleFacts, span.until)
        formula = held.formula or formula
    return formula


def scan_ledger_progress(ledger_path: Path) -> Progress:
    return LedgerIndex.of(ledger_path, PROGRESS_FOLDS).view(_Progress)


def scan_ledger_ruler(spans: Iterable[LedgerSpan], dataset_name: str) -> DeltaRuler | None:
    raw: dict[str, Any] | None = None
    for span in spans:
        held = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Rulers, span.until)
        raw = held.get(dataset_name, raw)
    return None if raw is None else DeltaRuler.model_validate(raw)


def scan_standing_rounds(spans: Iterable[LedgerSpan]) -> StandingRounds:
    standing = StandingRounds.none()
    for span in spans:
        standing = standing.then(
            LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Standing, span.until)
        )
    return standing


def scan_ledger_walks(spans: Iterable[LedgerSpan]) -> list[IndividualWalk[WalkedCell]]:
    chain = list(spans)
    standing = scan_standing_rounds(chain)
    closed = standing.rounds
    walks = [walk for held in closed.values() for walk in held.close.cells.walks(held.close.opt_sp)]
    for (round_num, _), arm in standing.arms.items():
        if round_num in closed or not arm.candidate_id:
            continue
        walks += RoundCells(arms={arm.candidate_id: list(arm.cells.values())}).walks(None)
    walks += [
        IndividualWalk(individual_id, frozenset({role}), list(taken.values()))
        for (round_num, individual_id, role), taken in standing.passes.items()
        if round_num not in closed
    ]
    # The whole chain: a fork inherits its parent's verify CELLS, though not its assurance.
    for span in chain:
        banked = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Verify, span.until)
        walks += [verify_pass.walk() for verify_pass in banked.passes]
    return walks


def scan_ledger_declared_phase(ledger_path: Path) -> str:
    return _view(ledger_path, _DeclaredPhase)


_Span = tuple[float, float]
_CallSpan = tuple[str, str, float, float]


def scan_ledger_verify(ledger_path: Path) -> VerifyLedger:
    """Own ledger only: a fork inherits its parent's measurements, not its assurance."""
    return _view(ledger_path, _Verify)


def _phase_spans(
    rows: list[dict[str, Any]], optimizer_phases: frozenset[str]
) -> list[tuple[str, float, float]]:
    brackets = {p.value for p in CampaignPhase} | optimizer_phases
    open_at: dict[tuple[str, object], float] = {}
    out: list[tuple[str, float, float]] = []
    for rec in rows:
        phase = rec.get("phase")
        if rec.get("record_type") != "phase" or phase not in brackets:
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is None:
            continue
        key = (str(phase), rec.get("round"))
        if rec.get("event") == "enter":
            open_at[key] = at
        elif rec.get("event") == "exit" and (entered := open_at.pop(key, None)) is not None:
            out.append((str(phase), entered, max(entered, at)))
    return out


def _gate_spans(rows: list[dict[str, Any]], *, until: float | None) -> list[_Span]:
    out: list[_Span] = []
    opened: float | None = None
    for rec in rows:
        if rec.get("record_type") != "run_phase":
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is None:
            continue
        if opened is not None:
            out.append((opened, max(opened, at)))
            opened = None
        if rec.get("run_phase") == RunPhase.GATE.value:
            opened = at
    # An abandoned gate (abort, vanished producer) closes at *until*: it was held that long.
    if opened is not None and until is not None:
        out.append((opened, max(opened, until)))
    return out


def _call_spans(rows: list[dict[str, Any]], *, opened: float | None) -> list[_CallSpan]:
    out: list[_CallSpan] = []
    for rec in rows:
        if rec.get("record_type") != "token_usage" or rec.get("cached"):
            continue
        kind = rec.get("kind")
        seconds = rec.get("duration_s")
        if kind not in get_args(TokenUsageKind):
            continue
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is None:
            continue
        # A bill is stamped when it LANDS, so the span ends at its record.
        start = at - float(seconds) if opened is None else max(at - float(seconds), opened)
        if start < at:
            out.append((str(kind), str(rec.get("node")), start, at))
    return out


def _merged(spans: list[_Span]) -> list[_Span]:
    out: list[_Span] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _unbracketed_call_seconds(
    calls: list[_CallSpan], covered: list[_Span]
) -> dict[str, dict[str, float]]:
    events = sorted(
        [(start, 1, (bucket, node)) for bucket, node, start, _ in calls]
        + [(end, -1, (bucket, node)) for bucket, node, _, end in calls]
    )
    active: dict[tuple[str, str], int] = {}
    out: dict[str, dict[str, float]] = {}
    cursor, prev = 0, None
    for at, delta, key in events:
        running = sum(active.values())
        if prev is not None and running and at > prev:
            while cursor < len(covered) and covered[cursor][1] <= prev:
                cursor += 1
            free, j = at - prev, cursor
            while j < len(covered) and covered[j][0] < at:
                free -= min(covered[j][1], at) - max(covered[j][0], prev)
                j += 1
            for (bucket, node), n in active.items() if free > 0 else ():
                by_node = out.setdefault(bucket, {})
                by_node[node] = by_node.get(node, 0.0) + free * n / running
        active[key] = active.get(key, 0) + delta
        if not active[key]:
            del active[key]
        prev = at
    return out


def _round_ended_seconds(rows: list[dict[str, Any]], *, opened: float | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if opened is None:
        return out
    for rec in rows:
        rnd = rec.get("round")
        if rec.get("record_type") != "round_closed" or not isinstance(rnd, int):
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is not None:
            # FIRST close wins: round 0 closes again at ruler warm, and a rewind re-runs a round.
            out.setdefault(str(rnd), max(0.0, at - opened))
    return out


def _unworked_seconds(rows: list[dict[str, Any]]) -> float | None:
    # ``None``, never 0.0, where no cell carried an envelope: nothing watched for a suspend.
    total: float | None = None
    for rec in rows:
        if rec.get("record_type") != "sample_scored":
            continue
        facts = _scored_facts(rec)
        if facts.cached:
            continue
        seconds = facts.pipeline.unworked_s
        if seconds is None:
            continue
        total = (total or 0.0) + max(0.0, seconds)
    return total


def scan_ledger_answers(ledger_paths: Iterable[Path]) -> set[str]:
    found: set[str] = set()
    for path in ledger_paths:
        for rec in iter_jsonl(path, record_types=frozenset({"sample_scored"})):
            # The screen is a raw-line substring probe, so the kind is asked again here.
            if rec.get("record_type") != "sample_scored":
                continue
            if (answer := _scored_facts(rec).answer) is not None:
                found.add(answer)
    return found


def scan_ledger_priced_keys(ledger_paths: Iterable[Path]) -> set[str]:
    found: set[str] = set()
    for path in ledger_paths:
        for rec in iter_jsonl(path, record_types=frozenset({"priced_key"})):
            value = rec.get("priced_key")
            if isinstance(value, str):
                found.add(value)
    return found


def scan_ledger_spend(spans: Iterable[LedgerSpan]) -> LedgerSpend:
    spend = SpendRollup()
    calls, worked_s = 0, 0.0
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Spend, span.until)
        for held in own.by_round.values():
            spend.absorb(held)
        calls += own.calls
        worked_s += own.worked_s
    return LedgerSpend(spend, calls, worked_s)


def scan_ledger_spend_by_round(spans: Iterable[LedgerSpan]) -> dict[int, SpendRollup]:
    """Not clamped by a rewind: a round measured twice cost money both times."""
    by_round: dict[int, SpendRollup] = {}
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Spend, span.until)
        for n, held in own.by_round.items():
            by_round.setdefault(n, SpendRollup()).absorb(held)
    return dict(sorted(by_round.items()))


def scan_ledger_run_wiring(spans: Iterable[LedgerSpan]) -> RunWiringRecord | None:
    found: RunWiringRecord | None = None
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_RunWiring, span.until)
        found = found if own is None else own
    return found


def close_spend(spans: Iterable[LedgerSpan]) -> CloseSpend:
    spend, calls, worked_s = scan_ledger_spend(spans)
    return CloseSpend(
        search_usd=spend.search_incurred_usd,
        billed_usd=spend.total_used_usd,
        rate_priced_usd=spend.total_rate_priced_usd,
        calls=calls,
        tokens=spend.total_tokens_used,
        worked_s=round(worked_s, 3),
    )


def scan_ledger_wall_clock(
    ledger_paths: Sequence[Path],
    *,
    started_at: str,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> WallClock:
    """The endpoints are the RUNNER's: the ledger's first record is already past ``init_services``."""
    rows = [
        row
        for ledger_path in ledger_paths
        for row in iter_jsonl(
            ledger_path,
            record_types=frozenset(
                {"phase", "run_phase", "round_closed", "token_usage", "sample_scored"}
            ),
        )
    ]
    opened, closed = epoch_seconds(started_at), epoch_seconds(finished_at)
    # A resumed ledger holds earlier launches; the endpoints are THIS launch's, so only its rows fold.
    if opened is not None:
        dated = [
            (at, r)
            for r in rows
            if (at := epoch_seconds(r.get("timestamp"))) is not None and at >= opened
        ]
        rows = [r for _, r in sorted(dated, key=lambda pair: pair[0])]
    elapsed = None if opened is None or closed is None else max(0.0, closed - opened)
    phases = _phase_spans(rows, optimizer_phases)
    gates = _gate_spans(rows, until=closed)
    calls = _call_spans(rows, opened=opened)
    covered = _merged([(start, end) for _, start, end in phases] + gates)
    phase_s: dict[str, float] = {}
    for phase, start, end in phases:
        phase_s[phase] = phase_s.get(phase, 0.0) + end - start
    worked_s: dict[str, dict[str, float]] = {}
    for bucket, node, start, end in calls:
        by_node = worked_s.setdefault(bucket, {})
        by_node[node] = by_node.get(node, 0.0) + end - start
    unbracketed = _unbracketed_call_seconds(calls, covered)
    held = sum(end - start for start, end in covered)
    held += sum(s for by_node in unbracketed.values() for s in by_node.values())
    return WallClock(
        elapsed_s=elapsed,
        phase_s=phase_s,
        worked_s=worked_s,
        unbracketed_call_s=unbracketed,
        round_ended_s=_round_ended_seconds(rows, opened=opened),
        gate_s=sum(end - start for start, end in gates),
        unattributed_s=None if elapsed is None else max(0.0, elapsed - held),
        unworked_s=_unworked_seconds(rows),
    )


__all__ = [
    "ArmWalk",
    "Controls",
    "CycleFacts",
    "LedgerSpend",
    "LookaheadAsk",
    "PauseAsk",
    "Progress",
    "StandingRound",
    "StandingRounds",
    "VerifyLedger",
    "close_spend",
    "scan_bench_passes",
    "scan_bench_readings",
    "scan_cell_formula",
    "scan_cycle_facts",
    "scan_launch_claim",
    "scan_ledger_controls",
    "scan_ledger_cycle_seed",
    "scan_ledger_declared_phase",
    "scan_ledger_progress",
    "scan_ledger_ruler",
    "scan_ledger_run_wiring",
    "scan_ledger_scoring_lock",
    "scan_ledger_spend",
    "scan_ledger_spend_by_round",
    "scan_ledger_verify",
    "scan_ledger_walks",
    "scan_ledger_wall_clock",
    "scan_standing_rounds",
]
