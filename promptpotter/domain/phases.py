from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import duration_words, utcnow_iso
from promptpotter.shared.errors import ErrorCategory

if TYPE_CHECKING:
    from promptpotter.domain.run_records import RebaseRequest

__all__ = [
    "DASHBOARD_STATE_PAUSE_WORDS",
    "INNER_GATE_REFUSAL",
    "INNER_LAUNCH_REFUSAL",
    "INNER_PAUSE_REFUSAL",
    "INNER_SKIP_REFUSAL",
    "PRODUCER_STATE_INFO",
    "QUEUE_STARTS_ITSELF",
    "REFUSAL_STOPS",
    "RUN_PHASE_INFO",
    "CampaignPhase",
    "DashboardState",
    "DeclaredRunPhase",
    "ErrorRecord",
    "GateDecision",
    "LaunchStage",
    "PauseCause",
    "PauseReading",
    "ProducerAlert",
    "ProducerReading",
    "ProducerState",
    "RunAction",
    "RunAdmission",
    "RunPhase",
    "RunPhaseInfo",
    "RunState",
    "StopCategory",
    "StopLoop",
    "StopOutcome",
    "StopReason",
    "WalkEnd",
    "run_phase_label",
    "stop_next_step",
    "stop_reason_outcome",
]


class CampaignPhase(enum.StrEnum):
    """The bench's phases. An optimizer's own are its llm nodes' (``LlmNode.phase``)."""

    INIT = "init"
    ORIGIN = "origin"
    PROPOSE = "propose"
    MEASURE = "measure"
    SELECT = "select"
    ADAPT = "adapt"
    # The held-out pass grading the selection, bracketed once per pass with no round.
    BENCH = "bench"
    VERIFY = "verify"


class StopReason(enum.StrEnum):
    """Why a cycle's run ended or paused."""

    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    LIVES_EXHAUSTED = "lives_exhausted"
    PAUSED = "paused"
    PANEL_CUT = "panel_cut"
    CRASHED = "crashed"
    DIVERGED = "diverged"
    OPTIMIZER_ABORT = "optimizer_abort"
    CONVERGED = "converged"
    # The optimizer's own word; ``CONVERGED`` is the bench's reading of the standing.
    OPTIMIZER_EXHAUSTED = "optimizer_exhausted"
    HARD_CAP = "hard_cap_reached"
    DIAG_COMPLETE = "diag_complete"
    TARGET_HIT = "target_hit"
    SPEND_BUDGET = "spend_budget"
    TOKEN_BUDGET = "token_budget"
    ORIGIN_GATE = "origin_gate"
    # Raised at the first cell the backend could not reach once its own retries were spent.
    BACKEND_UNREACHABLE = "backend_unreachable"
    PROVIDER_CREDIT = "provider_credit_exhausted"
    PROVIDER_THROTTLED = "provider_throttled"
    PRICE_LIST_UNREACHABLE = "price_list_unreachable"
    NO_RATE = "no_rate"
    RENDER_ERROR = "render_error"
    OPTIMIZER_TIMEOUT = "optimizer_timeout"
    REBASED = "rebased_to_fork"
    PRODUCER_VANISHED = "producer_vanished"
    INPUT_REFUSED = "input_refused"
    NOT_ADMITTED = "not_admitted"


class WalkEnd(enum.StrEnum):
    """Why one walk ended before its last cell while its round went on."""

    # The operator's early-abort: the partial is accepted.
    SKIP = "skip"
    STOP_RULE = "stop_rule"
    BUDGET = "budget"
    CLIENT_ERROR = "client_error"
    PIPELINE_ERROR = "pipeline_error"
    CONSECUTIVE_ERRORS = "consecutive_errors"


assert not {end.value for end in WalkEnd} & {reason.value for reason in StopReason}


class RunPhase(enum.StrEnum):
    """The one run-state vocabulary every surface reads.

    - ``checkin``: still authoring its origin; holds no machine slot.
    - ``queued``: a launch claimed the cycle and waits for a machine slot.
    - ``starting``: a claimed launch past the queue, its process coming up.
    - ``running``: a process is attached and driving the cycle.
    - ``paused``: the worker exited and the cycle stays active and resumable.
    - ``gate``: holding at the round-0 origin gate for an operator decision.
    - ``detached``: active with no process holding its lock; derived at the read, never stored.
    - ``terminal``: the cycle finished, and its stop reason says why.
    """

    CHECKIN = "checkin"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    GATE = "gate"
    DETACHED = "detached"
    TERMINAL = "terminal"


# What a RUNNER can say of itself (``RunPhaseRecord``): the other four are derived at the read.
DeclaredRunPhase = Literal[RunPhase.RUNNING, RunPhase.PAUSED, RunPhase.GATE, RunPhase.TERMINAL]

LaunchStage = Literal[RunPhase.QUEUED, RunPhase.STARTING]

GateDecision = Literal["rescore", "proceed", "abort"]


class PauseCause(enum.StrEnum):
    """What paused a run, beside the stop reason that says how the cycle was left."""

    COMMAND = "command"
    # The run this one measures FOR was paused (an L4 inner cycle under its outer's pause).
    ENCLOSING = "enclosing"
    # An interrupt signal reached the process with no command behind it.
    INTERRUPT = "interrupt"
    # Cancelled by its host: the L4 outer sample deadline, an embedder, a withdrawn launch.
    CANCELLED = "cancelled"
    # The launch reached the round allowance it was started with (``step-cycle``).
    STEP = "step"
    # A declared bound cut the round's panel (``StopReason.PANEL_CUT``).
    BOUND = "bound"


class PauseReading(StrictModel):
    """Why a cycle reads paused: its declaration, or an accepted pause the loop has yet to reach."""

    model_config = ConfigDict(frozen=True)

    stop_reason: StopReason | None
    cause: PauseCause
    detail: str = Field(description="Who or what asked, in the declarer's own words.")
    next_step: str
    at: str


class ProducerState(enum.StrEnum):
    """Whether a process is attached to a cycle, and whether it is getting anywhere."""

    LIVE = "live"
    # Attached, heartbeats alone past that window — legitimate while a cell is open.
    IDLE = "idle"
    # Attached, getting nowhere: no heartbeat, or only heartbeats past the wedge window, no cell open.
    WEDGED = "wedged"
    # Attached and waiting on the operator at the origin gate; it appends nothing until answered.
    HELD = "held"
    # No process holds the cycle, and its last word was that it was running: it died where it stood.
    SILENT = "silent"
    # No process runs the cycle yet and a live launch has CLAIMED it.
    CLAIMED = "claimed"
    # No producer and none owed: check-in, never launched, paused, ended, or lost at the gate.
    ABSENT = "absent"


_APPENDING = frozenset({ProducerState.LIVE, ProducerState.IDLE, ProducerState.WEDGED})

# An open cell younger than this is an ordinary call; past it, a surface names it as the hold.
CALL_HOLDS_AFTER_S = 10.0


class ProducerStateInfo(NamedTuple):
    """``alert`` is ``""`` where a state raises none. No defaults: a new state states every column."""

    label: str
    stepping: bool
    alert: str
    alert_detail: str


PRODUCER_STATE_INFO: dict[ProducerState, ProducerStateInfo] = {
    ProducerState.LIVE: ProducerStateInfo("Running", True, "", ""),
    ProducerState.IDLE: ProducerStateInfo("Running", False, "", ""),
    ProducerState.WEDGED: ProducerStateInfo("Wedged", False, "Run wedged", "heartbeats only"),
    ProducerState.HELD: ProducerStateInfo("Running", False, "", ""),
    ProducerState.SILENT: ProducerStateInfo(
        "Running",
        False,
        "Run went silent",
        "its producer is gone — Start relaunches from the last closed round",
    ),
    ProducerState.CLAIMED: ProducerStateInfo("Starting", False, "", ""),
    ProducerState.ABSENT: ProducerStateInfo("Running", False, "", ""),
}

# Where the state's own word says too little: on an open cell with no recent step; on a child run.
HEAD_MEASURING_LABEL = "Measuring"
HEAD_ON_CHILD_LABEL = "Waiting"

_missing_producer_info = set(ProducerState) - set(PRODUCER_STATE_INFO)
if _missing_producer_info:
    raise RuntimeError(
        f"PRODUCER_STATE_INFO is missing rows for "
        f"{sorted(s.value for s in _missing_producer_info)} (domain/phases.py)."
    )


class ProducerAlert(StrictModel):
    """What a producer's state raises on its own, worded once for every surface."""

    model_config = ConfigDict(frozen=True)

    title: str
    detail: str


class ProducerReading(StrictModel):
    """One cycle's producer as served: its state, its two server-measured clocks and its words."""

    model_config = ConfigDict(frozen=True)

    state: ProducerState
    label: str = Field(
        description="The state as a walking run's head reads it: the state's own word, or the "
        "measuring word while it sits on an open cell with no recent step."
    )
    label_on_child: str = Field(
        description="The same head while the run's newest step is a child run's — the one word "
        "for a parent whose call is open on it."
    )
    stepping: bool = Field(
        description="A record landed inside the recent-step window, so the run's newest step is "
        "where it stands now."
    )
    stalled: str = Field(
        description="How long a wedged producer has made no progress, as a sentence; empty on "
        "every other state."
    )
    alert: ProducerAlert | None = Field(
        description="What this state raises on its own (`silent`, `wedged`); null where nothing "
        "is wrong with the producer."
    )
    attached: bool = Field(
        description="A process holds this cycle — running it, or launching it (`claimed`) — so "
        "the cycle is neither deleted, compacted, resumed nor billed by a second party."
    )
    appending: bool = Field(
        description="That process is walking the loop, so the transient indicators are live."
    )
    silent_for_s: float | None = Field(
        description="Seconds since the last ledger record that was not a heartbeat; null unless "
        "appending."
    )
    open_for_s: float | None = Field(
        description="Seconds the cell the round waits on has been open; null where none is."
    )
    call_holds: bool = Field(
        description="That cell has been open past `CALL_HOLDS_AFTER_S`: calls are taken in walk "
        "order, so it is what holds every call behind it."
    )

    @classmethod
    def of(
        cls,
        state: ProducerState,
        *,
        silent_for_s: float | None = None,
        open_for_s: float | None = None,
    ) -> ProducerReading:
        appending = state in _APPENDING
        open_s = open_for_s if appending else None
        silent_s = silent_for_s if appending else None
        info = PRODUCER_STATE_INFO[state]
        wedged = state is ProducerState.WEDGED
        clocked = "" if silent_s is None else f" for {duration_words(silent_s)}"
        return cls(
            state=state,
            label=HEAD_MEASURING_LABEL
            if state is ProducerState.IDLE and open_s is not None
            else info.label,
            label_on_child=HEAD_ON_CHILD_LABEL,
            stepping=info.stepping,
            stalled=""
            if not wedged
            else "no progress recorded"
            if silent_s is None
            else f"no progress{clocked}",
            alert=ProducerAlert(
                title=info.alert, detail=info.alert_detail + (clocked if wedged else "")
            )
            if info.alert
            else None,
            attached=appending or state in (ProducerState.HELD, ProducerState.CLAIMED),
            appending=appending,
            silent_for_s=silent_s,
            open_for_s=open_s,
            call_holds=open_s is not None and open_s >= CALL_HOLDS_AFTER_S,
        )


# Read by the served admission and the refusing command alike (``CyclePayload.inner_refusal``).
INNER_LAUNCH_REFUSAL = "An inner run is launched by the outer cell that measures it."
INNER_PAUSE_REFUSAL = (
    "An inner run stops with the outer run that spawned it. Pause the outer run instead."
)
INNER_GATE_REFUSAL = "An inner run never holds at the origin gate."
INNER_SKIP_REFUSAL = (
    "An inner run is one cell of an outer measurement, and a cut searchpoint would change what "
    "that cell measured."
)


class RunState(NamedTuple):
    """``inner``: the cycle is an L4 inner run, which answers every run verb for itself."""

    run_phase: RunPhase
    producer: ProducerReading
    pause: PauseReading | None = None
    inner: bool = False

    @property
    def resumable(self) -> bool:
        """A launch may take this cycle up, and a rewrite of its ledger races nobody."""
        return not self.producer.attached and self.run_phase is not RunPhase.CHECKIN

    @property
    def launch_refusal(self) -> str:
        """Why `start-run` / `step-cycle` / `resume` is refused; empty where one is admitted."""
        if self.inner:
            return INNER_LAUNCH_REFUSAL
        if self.resumable:
            return ""
        if (info := RUN_PHASE_INFO[self.run_phase]).authoring:
            return info.no_action
        return "A run of this cycle is in flight. Pause it or let it end, then ask again."

    @property
    def pause_refusal(self) -> str:
        """Why `pause-cycle` is refused; empty where it is admitted."""
        if self.inner:
            return INNER_PAUSE_REFUSAL
        if self.producer.state is ProducerState.CLAIMED:
            # No loop exists yet to take a pause: one accepted here would be dropped.
            return "This launch has not started its run yet — cancel it from the queue instead."
        return "" if self.producer.attached else "No run of this cycle is in flight to pause."

    @property
    def gate_refusal(self) -> str:
        """Why `origin-gate-decision` is refused; empty where it is admitted."""
        if self.inner:
            return INNER_GATE_REFUSAL
        if RUN_PHASE_INFO[self.run_phase].awaits_operator:
            return ""
        return "This cycle is not holding at its origin gate."

    @property
    def admission(self) -> RunAdmission:
        return RunAdmission.of(self)


# ``resume`` and ``start`` both launch: a paused cycle's worker has exited.
RunAction = Literal["pause", "resume", "start"]


class RunPhaseInfo(NamedTuple):
    """What one run phase is, for every surface that names or tests it."""

    label: str
    walks: bool
    settled: bool
    authoring: bool
    awaits_operator: bool
    dock_priority: int
    action: RunAction | None
    no_action: str
    parked: str
    parked_attached: str


QUEUE_STARTS_ITSELF = "it starts by itself when a slot frees"

RUN_PHASE_INFO: dict[RunPhase, RunPhaseInfo] = {
    RunPhase.CHECKIN: RunPhaseInfo(
        "Check-in",
        False,
        False,
        True,
        False,
        3,
        None,
        "Still in check-in — start it from the setup panel.",
        "",
        "",
    ),
    RunPhase.QUEUED: RunPhaseInfo(
        "Queued",
        False,
        False,
        False,
        False,
        2,
        None,
        f"Queued — {QUEUE_STARTS_ITSELF}.",
        "waiting for a machine slot",
        "waiting for a machine slot",
    ),
    RunPhase.STARTING: RunPhaseInfo(
        "Starting",
        False,
        False,
        False,
        False,
        1,
        None,
        "Starting up…",
        "coming up",
        "coming up",
    ),
    RunPhase.RUNNING: RunPhaseInfo("Running", True, False, False, False, 1, "pause", "", "", ""),
    # Asked to pause, an attached producer runs on to its checkpoint.
    RunPhase.PAUSED: RunPhaseInfo(
        "Paused",
        False,
        False,
        False,
        False,
        2,
        "resume",
        "",
        "resumable",
        "stopping at its next checkpoint",
    ),
    # Blocked on the operator, so it leads the dock.
    RunPhase.GATE: RunPhaseInfo(
        "Origin gate",
        False,
        False,
        False,
        True,
        0,
        None,
        "At origin gate — decide in the chat.",
        "awaiting your decision",
        "awaiting your decision",
    ),
    RunPhase.DETACHED: RunPhaseInfo(
        "Detached", False, False, False, False, 3, "start", "", "producer gone", "producer gone"
    ),
    RunPhase.TERMINAL: RunPhaseInfo(
        "Ended", False, True, False, False, 3, "start", "", "", "writing its last files"
    ),
}

_missing_phase_info = set(RunPhase) - set(RUN_PHASE_INFO)
if _missing_phase_info:
    raise RuntimeError(
        f"RUN_PHASE_INFO is missing rows for {sorted(p.value for p in _missing_phase_info)} — "
        "every RunPhase needs a label + its facts (domain/phases.py)."
    )


def run_phase_label(run_phase: RunPhase, stop_reason: StopReason | None) -> str:
    if run_phase is not RunPhase.TERMINAL:
        return RUN_PHASE_INFO[run_phase].label
    return "—" if stop_reason is None else STOP_REASON_INFO[stop_reason].label


class RunAdmission(StrictModel):
    """Which run verbs a cycle admits now: the one answer the run control and the dispatcher read.

    Each ``*_refusal`` is the sentence that verb is refused with, empty where it is admitted."""

    model_config = ConfigDict(frozen=True)

    offers: RunAction | None = Field(
        description="The verb the one run control offers; null where it offers none."
    )
    refusal: str = Field(description="Why the control offers none; empty where it offers one.")
    skip_refusal: str = Field(description="`skip-searchpoint`.")
    lookahead_refusal: str = Field(
        description="`set-sample-lookahead` for ONE round. `auto` is a mode and a disarm removes "
        "an arming, so neither needs a walk and neither is refused here."
    )

    @classmethod
    def of(cls, run: RunState) -> RunAdmission:
        info = RUN_PHASE_INFO[run.run_phase]
        # Both are spent by a walk: accepted anywhere else, the next launch would drop them.
        walking = info.walks and run.producer.appending
        if run.inner:
            skip = INNER_SKIP_REFUSAL
        else:
            skip = "" if walking else "No searchpoint is being scored to skip."
        lookahead = "" if walking else "No run of this cycle is in flight to hold calls ahead for."
        declined = run.pause_refusal if info.action == "pause" else run.launch_refusal
        if info.action is None:
            offers, refusal = None, info.no_action
        elif run.inner:
            offers, refusal = None, declined
        elif declined:
            # The phase names a verb its producer does not admit yet.
            offers, refusal = None, f"{info.parked_attached.capitalize()}."
        else:
            offers, refusal = info.action, ""
        return cls(
            offers=offers,
            refusal=refusal,
            skip_refusal=skip,
            lookahead_refusal=lookahead,
        )


class DashboardState(enum.StrEnum):
    """The fine-grained activity vocabulary of ``dashboard.json::state``, apart from the run phase."""

    INIT = "init"
    ORIGIN = "origin"
    PROPOSING = "proposing"
    SCORING = "scoring"
    BETWEEN_SAMPLES = "between_samples"
    BETWEEN_CANDIDATES = "between_candidates"
    OPTIMIZER_STEP = "optimizer_step"
    BENCH = "bench"
    STOPPED = "stopped"


DASHBOARD_STATE_PAUSE_WORDS: dict[DashboardState, str] = {
    DashboardState.INIT: "starting up",
    DashboardState.ORIGIN: "scoring origin",
    DashboardState.PROPOSING: "generating candidates",
    DashboardState.SCORING: "scoring samples",
    DashboardState.BETWEEN_SAMPLES: "scoring samples",
    DashboardState.BETWEEN_CANDIDATES: "scoring samples",
    DashboardState.OPTIMIZER_STEP: "",
    DashboardState.BENCH: "grading on the bench set",
    DashboardState.STOPPED: "",
}
assert set(DASHBOARD_STATE_PAUSE_WORDS) == set(DashboardState)


class StopOutcome(enum.StrEnum):
    """``PAUSED`` is the ONLY non-terminal outcome: the worker exited, the cycle keeps no ``finished_at``."""

    SUCCESS = "success"
    HALTED = "halted"
    FAILED = "failed"
    PAUSED = "paused"

    @property
    def exit_code(self) -> int:
        """All a shell, a CI wrapper or a spawning server reads; a pause exits as an interrupt does."""
        return {StopOutcome.FAILED: 1, StopOutcome.PAUSED: 130}.get(self, 0)


class StopCategory(enum.StrEnum):
    SEARCH = "search"
    LIMIT = "limit"
    BUDGET = "budget"
    OPERATOR = "operator"
    EXTERNAL = "external"
    FAILURE = "failure"


class StopReasonInfo(NamedTuple):
    """``halts_mid_round``: a round this reason CUTS is left on disk PARTIAL. No defaults, on purpose."""

    label: str
    outcome: StopOutcome
    category: StopCategory
    halts_mid_round: bool
    has_traceback: bool
    next_step: str
    grades_selection: bool


# `halts_mid_round` follows WHERE a stop is raised, never severity; `next_step` is filled only where the label names no verb.
STOP_REASON_INFO: dict[StopReason, StopReasonInfo] = {
    StopReason.PERFECT: StopReasonInfo(
        "Perfect score",
        StopOutcome.SUCCESS,
        StopCategory.SEARCH,
        False,
        False,
        "`verify` the winner on more cells — this is one round's panel, not the dataset.",
        True,
    ),
    StopReason.MAX_ROUNDS: StopReasonInfo(
        "Max rounds",
        StopOutcome.SUCCESS,
        StopCategory.LIMIT,
        False,
        False,
        "`set-limits --max-rounds <more>` then `resume` if the curve was still moving; else read "
        "`review.md`.",
        True,
    ),
    StopReason.TARGET_HIT: StopReasonInfo(
        "Target reached", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True
    ),
    StopReason.LIVES_EXHAUSTED: StopReasonInfo(
        "Out of lives", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True
    ),
    StopReason.HARD_CAP: StopReasonInfo(
        "Arm cap", StopOutcome.SUCCESS, StopCategory.LIMIT, False, False, "", True
    ),
    StopReason.DIAG_COMPLETE: StopReasonInfo(
        "Diagnostic complete", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True
    ),
    StopReason.CONVERGED: StopReasonInfo(
        "Converged", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True
    ),
    StopReason.OPTIMIZER_EXHAUSTED: StopReasonInfo(
        "Optimizer out of moves", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True
    ),
    StopReason.REBASED: StopReasonInfo(
        "Rebased to fork", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", False
    ),
    StopReason.PAUSED: StopReasonInfo(
        "Paused",
        StopOutcome.PAUSED,
        StopCategory.OPERATOR,
        True,
        False,
        "`resume` picks it up at the next checkpoint.",
        False,
    ),
    # Unlike PAUSED the round is discarded, not persisted partial: `resume` alone re-buys the cut.
    StopReason.PANEL_CUT: StopReasonInfo(
        "Panel cut by a declared bound",
        StopOutcome.PAUSED,
        StopCategory.LIMIT,
        False,
        False,
        "Give the cut cells room (`Connector.cell_envelope_s`, or the backend deadline their "
        "rows name) before `resume`, or "
        "`optimization.panel_gate: off` to elect on the holed panel.",
        False,
    ),
    StopReason.OPTIMIZER_ABORT: StopReasonInfo(
        "Optimizer abort", StopOutcome.HALTED, StopCategory.SEARCH, False, False, "", True
    ),
    # The counter is CUMULATIVE across resume: a new ceiling must clear what it already counted.
    StopReason.SPEND_BUDGET: StopReasonInfo(
        "Spend budget reached",
        StopOutcome.HALTED,
        StopCategory.BUDGET,
        True,
        False,
        "`set-limits --max-usd <above what the cap already counts>` then `resume`.",
        True,
    ),
    StopReason.TOKEN_BUDGET: StopReasonInfo(
        "Token budget reached",
        StopOutcome.HALTED,
        StopCategory.BUDGET,
        True,
        False,
        "`set-limits --max-tokens <above what is already spent>` then `resume`.",
        True,
    ),
    StopReason.ORIGIN_GATE: StopReasonInfo(
        "Origin gate (unhealthy origin)",
        StopOutcome.HALTED,
        StopCategory.OPERATOR,
        False,
        False,
        "",
        True,
    ),
    StopReason.BACKEND_UNREACHABLE: StopReasonInfo(
        "Backend unreachable",
        StopOutcome.HALTED,
        StopCategory.EXTERNAL,
        True,
        False,
        "The unreached cell is a hole, not a score: restore the backend or the network it "
        "needs, then `resume` re-measures it.",
        True,
    ),
    # Not SPEND_BUDGET: that ceiling is ours and `set-limits` moves it. This one is the provider's.
    StopReason.PROVIDER_CREDIT: StopReasonInfo(
        "Provider out of credit",
        StopOutcome.HALTED,
        StopCategory.EXTERNAL,
        True,
        False,
        "Raise the provider key's limit or top up its credit, then `resume`; a refused cell is a "
        "hole it re-measures.",
        True,
    ),
    # Not BACKEND_UNREACHABLE: the provider answered, with a refusal the run already waited out.
    StopReason.PROVIDER_THROTTLED: StopReasonInfo(
        "Provider rate-limited",
        StopOutcome.HALTED,
        StopCategory.EXTERNAL,
        True,
        False,
        "Use your own key for that provider (OpenRouter BYOK) or route to another host "
        "(`route_order`), or wait out its quota, then `resume`; the refused cell is a hole it "
        "re-measures.",
        True,
    ),
    # Not BACKEND_UNREACHABLE: the send was refused before it left, for want of a price.
    StopReason.PRICE_LIST_UNREACHABLE: StopReasonInfo(
        "Price list unreachable",
        StopOutcome.HALTED,
        StopCategory.EXTERNAL,
        True,
        False,
        "Nothing was sent and the backend is untouched: the gateway's host price list did not "
        "answer, so no send under the USD ceiling could be priced. `resume` once it answers; a "
        "refused cell is a hole it re-measures.",
        True,
    ),
    # Not SPEND_BUDGET: no ceiling was reached; the send has no price to hold.
    StopReason.NO_RATE: StopReasonInfo(
        "No rate under the spend cap",
        StopOutcome.HALTED,
        StopCategory.BUDGET,
        True,
        False,
        "Nothing was sent: no rate bounds the call, so the dollar cap cannot be enforced for it. "
        "Run it on a (provider, model) pair the rate table prices (`nodes.{node}.config`), or "
        "drop the dollar cap and bound the run in tokens (`ceiling: {usd: null, tokens: N}`); "
        "then `resume`.",
        True,
    ),
    StopReason.CRASHED: StopReasonInfo(
        "Crashed",
        StopOutcome.FAILED,
        StopCategory.FAILURE,
        True,
        True,
        "Read `index.json::crash_traceback` for the cause; `python -m promptpotter resume` "
        "re-runs from the last closed round.",
        False,
    ),
    # Not CRASHED: the run declined the operator's own input (`PayloadInvalidError`).
    StopReason.INPUT_REFUSED: StopReasonInfo(
        "Input refused",
        StopOutcome.FAILED,
        StopCategory.FAILURE,
        True,
        False,
        "`dashboard.json::error` names the input the run declined; refused at run init, nothing "
        "was searched. A spend cap under one round is raised with `set-limits --max-usd` or met "
        "by narrowing the round in `optimization.nodes`; then `resume`.",
        False,
    ),
    # A JOB's ending, never a cycle's: the launch left before it held a run.
    StopReason.NOT_ADMITTED: StopReasonInfo(
        "Not admitted", StopOutcome.HALTED, StopCategory.LIMIT, False, False, "", False
    ),
    # Declared by the REAPER; mid-round because a vanished process died at an arbitrary point.
    StopReason.PRODUCER_VANISHED: StopReasonInfo(
        "Producer vanished", StopOutcome.FAILED, StopCategory.FAILURE, True, False, "", False
    ),
    # Like OPTIMIZER_TIMEOUT, caught by TYPE at the round loop: the closed rounds' selection stands.
    StopReason.RENDER_ERROR: StopReasonInfo(
        "Render error", StopOutcome.FAILED, StopCategory.FAILURE, True, True, "", True
    ),
    StopReason.DIVERGED: StopReasonInfo(
        "Diverged",
        StopOutcome.FAILED,
        StopCategory.FAILURE,
        False,
        False,
        "`resume --fork-on-divergence` to branch here, or revert the config edit to continue.",
        False,
    ),
    StopReason.OPTIMIZER_TIMEOUT: StopReasonInfo(
        "Optimizer timeout", StopOutcome.FAILED, StopCategory.FAILURE, True, False, "", True
    ),
}

_missing_stop_info = set(StopReason) - set(STOP_REASON_INFO)
if _missing_stop_info:
    raise RuntimeError(
        f"STOP_REASON_INFO is missing rows for {sorted(r.value for r in _missing_stop_info)} — "
        "every StopReason needs a label + outcome (domain/phases.py)."
    )


def stop_reason_outcome(reason: StopReason) -> StopOutcome:
    return STOP_REASON_INFO[reason].outcome


def stop_next_step(reason: StopReason | None) -> str:
    return "" if reason is None else STOP_REASON_INFO[reason].next_step


# One table for a refused send (`SendRefusedError`) and a banked hole (`CellInfrastructureError`).
REFUSAL_STOPS: dict[ErrorCategory, StopReason] = {
    ErrorCategory.CONNECTION: StopReason.BACKEND_UNREACHABLE,
    ErrorCategory.PROVIDER_CREDIT: StopReason.PROVIDER_CREDIT,
    ErrorCategory.PROVIDER_THROTTLED: StopReason.PROVIDER_THROTTLED,
    ErrorCategory.SPEND_CEILING: StopReason.SPEND_BUDGET,
    ErrorCategory.TOKEN_CEILING: StopReason.TOKEN_BUDGET,
    ErrorCategory.PRICE_LIST_UNREACHABLE: StopReason.PRICE_LIST_UNREACHABLE,
    ErrorCategory.NO_RATE: StopReason.NO_RATE,
}


class StopLoop(Exception):  # noqa: N818 — control-flow signal, not an error
    def __init__(
        self,
        reason: StopReason,
        *,
        unmeasured: int | None = None,
        fork: RebaseRequest | None = None,
    ) -> None:
        if (reason is StopReason.REBASED) != (fork is not None):
            raise ValueError("a REBASED stop, and only one, carries the fork it asks for")
        self.reason = reason
        # Cells of the walk this stop left unmeasured, where the raiser is a walk.
        self.unmeasured = unmeasured
        self.fork = fork
        super().__init__(reason.value)


class ErrorRecord(StrictModel):
    """An arm of ``CycleRecord`` declared here, since ``CycleResult.error`` sits below that union."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["error"] = "error"
    # Exception class name — operator-facing diagnostic, never load-bearing for routing.
    kind: str
    message: str
    traceback: str | None = None
    stop_reason: StopReason
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)
