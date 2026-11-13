from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple

from pydantic import Field

from promptpotter.domain.said import Tone
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.run_records import RebaseRequest


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

    ROUNDS_BUDGET = "rounds_budget"
    LIVES_EXHAUSTED = "lives_exhausted"
    PAUSED = "paused"
    PANEL_CUT = "panel_cut"
    # The round before's adapter landed nothing, and its re-runs before this walk failed.
    ADAPTER_OWED = "adapter_output_owed"
    CRASHED = "crashed"
    DIVERGED = "diverged"
    OPTIMIZER_ABORT = "optimizer_abort"
    OPTIMIZER_EXHAUSTED = "optimizer_exhausted"
    HARD_CAP = "hard_cap_reached"
    TARGET_HIT = "target_hit"
    SPEND_BUDGET = "spend_budget"
    TOKEN_BUDGET = "token_budget"
    HOST_ALLOWANCE = "host_allowance"
    WALL_CLOCK = "wall_clock"
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
    PARAMETER_REFUSED = "parameter_refused"
    PIPELINE_ERROR = "pipeline_error"
    CONSECUTIVE_ERRORS = "consecutive_errors"


assert not {end.value for end in WalkEnd} & {reason.value for reason in StopReason}


class RunPhase(enum.StrEnum):
    """The one run-state vocabulary every surface reads.

    - ``queued``: a launch claimed the cycle and waits for a machine slot.
    - ``starting``: a claimed launch past the queue, its process coming up.
    - ``running``: a process is attached and driving the cycle.
    - ``scoring``: a paid pass (bench, verify, noise floor) holds the cycle outside its loop.
    - ``paused``: the worker exited and the cycle stays active and resumable.
    - ``gate``: holding for the operator, at the round-0 origin gate or for a round's arms.
    - ``detached``: active with no process holding its lock; derived at the read, never stored.
    - ``terminal``: the cycle finished, and its stop reason says why.
    """

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SCORING = "scoring"
    PAUSED = "paused"
    GATE = "gate"
    DETACHED = "detached"
    TERMINAL = "terminal"


LaunchStage = Literal[RunPhase.QUEUED, RunPhase.STARTING]

# What a ``gate`` declaration holds for: the origin's verdict, or the arms a round measures.
GateKind = Literal["origin", "arms"]


class PauseCause(enum.StrEnum):
    """What paused a run, beside the stop reason that says how the cycle was left."""

    COMMAND = "command"
    # The run this one measures FOR was paused (an L4 inner cycle under its outer's pause).
    ENCLOSING = "enclosing"
    # An interrupt signal reached the process with no command behind it.
    INTERRUPT = "interrupt"
    # Cancelled by its host: the L4 outer sample deadline, an embedder, a withdrawn launch.
    CANCELLED = "cancelled"
    # The launch reached the round allowance it was started with (``start-run {step_rounds}``).
    STEP = "step"
    # A declared bound cut the round's panel (``StopReason.PANEL_CUT``).
    BOUND = "bound"
    # An adapter's round output stayed owed past its re-runs (``StopReason.ADAPTER_OWED``).
    OWED = "owed"


class PauseReading(FrozenModel):
    """Why a cycle reads paused: its declaration, or an accepted pause the loop has yet to reach."""

    stop_reason: StopReason | None = Field(
        description="Null on an accepted pause the loop has yet to reach."
    )
    cause: PauseCause
    detail: str = Field(description="Who or what asked, in the declarer's own words.")
    at: str


class ProducerState(enum.StrEnum):
    """Whether a process is attached to a cycle, and whether it is getting anywhere."""

    LIVE = "live"
    # Attached, heartbeats alone past that window — legitimate while a cell is open.
    IDLE = "idle"
    # Attached, getting nowhere: no heartbeat, or only heartbeats past the wedge window, no cell open.
    WEDGED = "wedged"
    # Attached and waiting on the operator at a gate; it appends nothing until answered.
    HELD = "held"
    # No process holds the cycle, and its last word was that it was running: it died where it stood.
    SILENT = "silent"
    # No process runs the cycle yet and a live launch has CLAIMED it.
    CLAIMED = "claimed"
    # No producer and none owed: never launched, paused, ended, or lost at the gate.
    ABSENT = "absent"


_APPENDING = frozenset({ProducerState.LIVE, ProducerState.IDLE, ProducerState.WEDGED})

# An open cell younger than this is an ordinary call; past it, a surface names it as the hold.
CALL_HOLDS_AFTER_S = 10.0


class ProducerStateInfo(NamedTuple):
    """No defaults: a new state states every column."""

    tone: Tone
    label: str
    detail: str | None


PRODUCER_STATE_INFO: dict[ProducerState, ProducerStateInfo] = {
    ProducerState.LIVE: ProducerStateInfo("live", "Running", None),
    ProducerState.IDLE: ProducerStateInfo("live", "Running", None),
    ProducerState.WEDGED: ProducerStateInfo("warn", "Run wedged", "heartbeats only"),
    ProducerState.HELD: ProducerStateInfo("attention", "Waiting on the operator", None),
    ProducerState.SILENT: ProducerStateInfo(
        "warn",
        "Run went silent",
        "its producer is gone — Start relaunches from the last closed round",
    ),
    ProducerState.CLAIMED: ProducerStateInfo("live", "Starting", None),
    ProducerState.ABSENT: ProducerStateInfo("quiet", "No producer", None),
}

_missing_producer_info = set(ProducerState) - set(PRODUCER_STATE_INFO)
if _missing_producer_info:
    raise RuntimeError(
        f"PRODUCER_STATE_INFO is missing rows for "
        f"{sorted(s.value for s in _missing_producer_info)} (domain/phases.py)."
    )


class ProducerReading(FrozenModel):
    """One cycle's producer as served: its state and its two server-measured clocks. Its words are
    `PRODUCER_STATE_INFO[state]`, pinned where its tone is loud enough (`producer_row`)."""

    state: ProducerState
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
    # Off the wire: its one reader is the served `held_call`, which words it.
    open_for_s: float | None = Field(None, exclude=True)

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
        return cls(
            state=state,
            attached=appending or state in (ProducerState.HELD, ProducerState.CLAIMED),
            appending=appending,
            silent_for_s=silent_s,
            open_for_s=open_s,
        )

    @property
    def call_holds(self) -> bool:
        """The waited-on cell has been open past `CALL_HOLDS_AFTER_S`: calls are taken in walk order,
        so it holds every call behind it."""
        return self.open_for_s is not None and self.open_for_s >= CALL_HOLDS_AFTER_S


# Read by the served admission and the refusing command alike (``CyclePayload.inner_refusal``).
INNER_LAUNCH_REFUSAL = "An inner run is launched by the outer cell that measures it."
INNER_PAUSE_REFUSAL = (
    "An inner run stops with the outer run that spawned it. Pause the outer run instead."
)
INNER_ARMS_REFUSAL = "An inner run never holds for an operator's arms."
INNER_SKIP_REFUSAL = (
    "An inner run is one cell of an outer measurement, and a cut searchpoint would change what "
    "that cell measured."
)


class RunState(NamedTuple):
    """``inner``: the cycle is an L4 inner run, which answers every run verb for itself.

    ``gate`` and ``drafts`` are the standing hold's own, set only while the phase is ``gate``."""

    run_phase: RunPhase
    producer: ProducerReading
    pause: PauseReading | None = None
    inner: bool = False
    gate: GateKind | None = None
    drafts: int = 0

    @property
    def resumable(self) -> bool:
        """A launch may take this cycle up, and a rewrite of its ledger races nobody."""
        return not self.producer.attached

    @property
    def start_refusal(self) -> str:
        """Why `start-run` / `resume` is refused; empty where one is admitted."""
        if self.inner:
            return INNER_LAUNCH_REFUSAL
        if self.resumable:
            return ""
        return "A run of this cycle is in flight. Pause it or let it end, then ask again."

    @property
    def pause_refusal(self) -> str:
        """Why `pause` is refused; empty where it is admitted."""
        if self.inner:
            return INNER_PAUSE_REFUSAL
        return "" if self.producer.attached else "No run of this cycle is in flight to pause."

    def arms_refusal(self, keep: Sequence[int]) -> str:
        """Why `submit-arms` keeping the drafts at *keep* is refused; empty where it is admitted."""
        if self.inner:
            return INNER_ARMS_REFUSAL
        if self.gate != "arms":
            return "This cycle is not holding for a round's arms."
        if not keep:
            return ""
        if not self.drafts:
            return "No proposer ran this round, so there is no draft to keep."
        if stray := sorted(i for i in keep if i >= self.drafts):
            return f"This round holds drafts 0 to {self.drafts - 1}; there is no draft {stray}."
        return ""

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
    awaits_operator: bool
    dock_priority: int
    action: RunAction | None
    no_action: str
    parked: str


QUEUE_STARTS_ITSELF = "it starts by itself when a slot frees"

RUN_PHASE_INFO: Worded[RunPhase, RunPhaseInfo] = Worded(
    {
        RunPhase.QUEUED: RunPhaseInfo(
            "Queued",
            False,
            False,
            False,
            2,
            None,
            f"Queued — {QUEUE_STARTS_ITSELF}.",
            "waiting for a machine slot",
        ),
        RunPhase.STARTING: RunPhaseInfo(
            "Starting",
            False,
            False,
            False,
            1,
            None,
            "Starting up…",
            "coming up",
        ),
        RunPhase.RUNNING: RunPhaseInfo("Running", True, False, False, 1, "pause", "", ""),
        # No loop walks it, so a skip or a look-ahead has nothing to spend; the one stop reaches it.
        RunPhase.SCORING: RunPhaseInfo("Scoring", False, False, False, 1, "pause", "", ""),
        # Asked to pause, an attached producer runs on to its checkpoint.
        RunPhase.PAUSED: RunPhaseInfo(
            "Paused",
            False,
            False,
            False,
            2,
            "resume",
            "",
            "stopping at its next checkpoint",
        ),
        # Blocked on the operator, so it leads the dock.
        RunPhase.GATE: RunPhaseInfo(
            "Holding",
            False,
            False,
            True,
            0,
            None,
            "Holding for you — the chat says what it waits on.",
            "awaiting your decision",
        ),
        RunPhase.DETACHED: RunPhaseInfo(
            "Detached", False, False, False, 3, "start", "", "producer gone"
        ),
        RunPhase.TERMINAL: RunPhaseInfo(
            "Ended", False, True, False, 3, "start", "", "writing its last files"
        ),
    }
)

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


class RunAdmission(FrozenModel):
    """Which run verbs a cycle admits now: the one answer the run control and the dispatcher read.

    Each ``*_refusal`` is the sentence that verb is refused with, empty where it is admitted."""

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
        pausing = info.action == "pause"
        declined = run.pause_refusal if pausing else run.start_refusal
        if info.action is None:
            offers, refusal = None, info.no_action
        elif run.inner:
            offers, refusal = None, declined
        elif declined:
            # The phase names a verb its producer does not admit yet; a pause says its own refusal.
            offers, refusal = None, declined if pausing else f"{info.parked.capitalize()}."
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


#: What a pause waits on, every state worded; a served `optimizer_step` names its step in place.
DASHBOARD_STATE_PAUSE_WORDS: Worded[DashboardState, str] = Worded(
    {
        DashboardState.INIT: "starting up",
        DashboardState.ORIGIN: "scoring origin",
        DashboardState.PROPOSING: "generating candidates",
        DashboardState.SCORING: "scoring samples",
        DashboardState.BETWEEN_SAMPLES: "scoring samples",
        DashboardState.BETWEEN_CANDIDATES: "scoring samples",
        DashboardState.OPTIMIZER_STEP: "the optimizer's step",
        DashboardState.BENCH: "grading on the bench set",
        DashboardState.STOPPED: "the current round",
    }
)
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


class NextAct(NamedTuple):
    """A remedy past a stop: a *kind* a door presses and the one payload field it fills (dotted
    into a model); with no kind, a declared *field* the operator sets; or a file it *reads*.
    *value* describes what goes in the field. Each door words its own spelling from it, the
    terminal its verb and flag (`views/render/phase.py`). A kind is checked against the kind
    table at its import (`command_kinds.py`)."""

    kind: Literal["", "start-run", "set-limits", "verify-candidate", "mint-campaign", "fork-cycle"]
    field: str = ""
    value: str = ""
    reads: str = ""


class StopReasonInfo(NamedTuple):
    """``halts_mid_round``: a round this reason CUTS is left on disk PARTIAL. No defaults, on purpose."""

    label: str
    outcome: StopOutcome
    category: StopCategory
    halts_mid_round: bool
    has_traceback: bool
    next_step: str
    grades_selection: bool
    next_acts: tuple[NextAct, ...]


# `halts_mid_round` follows WHERE a stop is raised, never severity. `next_step` states the fact past
# the stop in every surface's words, naming no verb or file; `next_acts` are the acts it names.
STOP_REASON_INFO: Worded[StopReason, StopReasonInfo] = Worded(
    {
        StopReason.ROUNDS_BUDGET: StopReasonInfo(
            "Rounds budget reached",
            StopOutcome.SUCCESS,
            StopCategory.LIMIT,
            False,
            False,
            "Every allowed round ran. If the curve was still moving, raise the rounds cap and resume; "
            "else read the review.",
            True,
            (
                NextAct("set-limits", "budget.rounds", "more"),
                NextAct("start-run"),
                NextAct("", reads="review.md"),
            ),
        ),
        StopReason.TARGET_HIT: StopReasonInfo(
            "Target reached", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True, ()
        ),
        StopReason.LIVES_EXHAUSTED: StopReasonInfo(
            "Out of lives", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", True, ()
        ),
        StopReason.HARD_CAP: StopReasonInfo(
            "Arm cap", StopOutcome.SUCCESS, StopCategory.LIMIT, False, False, "", True, ()
        ),
        StopReason.OPTIMIZER_EXHAUSTED: StopReasonInfo(
            "Optimizer out of moves",
            StopOutcome.SUCCESS,
            StopCategory.SEARCH,
            False,
            False,
            "",
            True,
            (),
        ),
        StopReason.REBASED: StopReasonInfo(
            "Rebased to fork", StopOutcome.SUCCESS, StopCategory.SEARCH, False, False, "", False, ()
        ),
        StopReason.PAUSED: StopReasonInfo(
            "Paused",
            StopOutcome.PAUSED,
            StopCategory.OPERATOR,
            True,
            False,
            "Resuming picks it up at the next checkpoint.",
            False,
            (NextAct("start-run"),),
        ),
        # Unlike PAUSED the round is discarded, not persisted partial: `resume` alone re-buys the cut.
        StopReason.PANEL_CUT: StopReasonInfo(
            "Panel cut by a declared bound",
            StopOutcome.PAUSED,
            StopCategory.LIMIT,
            False,
            False,
            "Cells were cut at their time bound. Give them room (the connector's cell envelope, or "
            "the backend deadline their rows name) before resuming, or turn the panel gate off to "
            "elect on the holed panel.",
            False,
            (
                NextAct("", "Connector.cell_envelope_s"),
                NextAct("", "optimization.panel_gate", "off"),
                NextAct("start-run"),
            ),
        ),
        StopReason.ADAPTER_OWED: StopReasonInfo(
            "Adapter output owed",
            StopOutcome.PAUSED,
            StopCategory.FAILURE,
            True,
            False,
            "The round before's adapter landed nothing in its re-runs; resuming re-runs it before "
            "the next walk.",
            False,
            (NextAct("start-run"),),
        ),
        StopReason.OPTIMIZER_ABORT: StopReasonInfo(
            "Optimizer abort", StopOutcome.HALTED, StopCategory.SEARCH, False, False, "", True, ()
        ),
        # The counter is CUMULATIVE across resume: a new ceiling must clear what it already counted.
        StopReason.SPEND_BUDGET: StopReasonInfo(
            "Spend budget reached",
            StopOutcome.HALTED,
            StopCategory.BUDGET,
            True,
            False,
            "The spend cap counts every launch so far: raise it above what it already counts, then "
            "resume.",
            True,
            (
                NextAct("set-limits", "budget.usd", "above what the cap already counts"),
                NextAct("start-run"),
            ),
        ),
        StopReason.TOKEN_BUDGET: StopReasonInfo(
            "Token budget reached",
            StopOutcome.HALTED,
            StopCategory.BUDGET,
            True,
            False,
            "Raise the token cap above what is already spent, then resume.",
            True,
            (
                NextAct("set-limits", "budget.tokens", "above what is already spent"),
                NextAct("start-run"),
            ),
        ),
        # Not SPEND_BUDGET: `set-limits` moves the campaign's ceiling, never the host's coupon.
        StopReason.HOST_ALLOWANCE: StopReasonInfo(
            "Host allowance used up",
            StopOutcome.HALTED,
            StopCategory.BUDGET,
            True,
            False,
            "The box's allowance for this account is spent: a resume sends on the account's own key, "
            "and a backend's sends still run on the box's key.",
            True,
            (NextAct("start-run"),),
        ),
        # A cell still open at the edge was cut; each launch's clock starts afresh.
        StopReason.WALL_CLOCK: StopReasonInfo(
            "Wall clock reached",
            StopOutcome.HALTED,
            StopCategory.BUDGET,
            True,
            False,
            "Resuming runs another launch under the same clock; the run limits move it.",
            True,
            (NextAct("start-run"), NextAct("set-limits", "budget.wall_clock_s", "")),
        ),
        StopReason.ORIGIN_GATE: StopReasonInfo(
            "Origin gate (unhealthy origin)",
            StopOutcome.HALTED,
            StopCategory.OPERATOR,
            False,
            False,
            "Fix the backend, then resume: resuming re-measures the cells the origin lost, and the "
            "gate grades it again; an origin still unhealthy when resumed passes on that resume. "
            "An edited origin is a new campaign or an operator-steered fork at round 0: resuming "
            "refuses it.",
            True,
            (NextAct("start-run"), NextAct("mint-campaign"), NextAct("fork-cycle")),
        ),
        StopReason.BACKEND_UNREACHABLE: StopReasonInfo(
            "Backend unreachable",
            StopOutcome.HALTED,
            StopCategory.EXTERNAL,
            True,
            False,
            "The unreached cell is a hole, not a score: restore the backend or the network it "
            "needs, then resuming re-measures it.",
            True,
            (NextAct("start-run"),),
        ),
        # Not SPEND_BUDGET: that ceiling is ours and `set-limits` moves it. This one is the provider's.
        StopReason.PROVIDER_CREDIT: StopReasonInfo(
            "Provider out of credit",
            StopOutcome.HALTED,
            StopCategory.EXTERNAL,
            True,
            False,
            "Raise the provider key's limit or top up its credit, then resume; a refused cell is a "
            "hole it re-measures.",
            True,
            (NextAct("start-run"),),
        ),
        # Not BACKEND_UNREACHABLE: the provider answered, with a refusal the run already waited out.
        StopReason.PROVIDER_THROTTLED: StopReasonInfo(
            "Provider rate-limited",
            StopOutcome.HALTED,
            StopCategory.EXTERNAL,
            True,
            False,
            "Use your own key for that provider (OpenRouter BYOK), route to another host, or wait "
            "out its quota, then resume; the refused cell is a hole it re-measures.",
            True,
            (NextAct("start-run"),),
        ),
        # Not BACKEND_UNREACHABLE: the send was refused before it left, for want of a price.
        StopReason.PRICE_LIST_UNREACHABLE: StopReasonInfo(
            "Price list unreachable",
            StopOutcome.HALTED,
            StopCategory.EXTERNAL,
            True,
            False,
            "Nothing was sent and the backend is untouched: the gateway's host price list did not "
            "answer, so no send under the USD ceiling could be priced. Resume once it answers; a "
            "refused cell is a hole it re-measures.",
            True,
            (NextAct("start-run"),),
        ),
        # Not SPEND_BUDGET: no ceiling was reached; the send has no price to hold.
        StopReason.NO_RATE: StopReasonInfo(
            "No rate under the spend cap",
            StopOutcome.HALTED,
            StopCategory.BUDGET,
            True,
            False,
            "Nothing was sent: no rate bounds the call, so the dollar cap cannot be enforced for it. "
            "Run it on a provider and model the rate table prices, or drop the dollar cap and bound "
            "the run in tokens; then resume.",
            True,
            (NextAct("start-run"),),
        ),
        StopReason.CRASHED: StopReasonInfo(
            "Crashed",
            StopOutcome.FAILED,
            StopCategory.FAILURE,
            True,
            True,
            "The run raised an error it did not expect, and its traceback is kept; resuming re-runs "
            "from the last closed round.",
            False,
            (NextAct("start-run"),),
        ),
        # Not CRASHED: the run declined the operator's own input (`PayloadInvalidError`).
        StopReason.INPUT_REFUSED: StopReasonInfo(
            "Input refused",
            StopOutcome.FAILED,
            StopCategory.FAILURE,
            True,
            False,
            "The run declined its input at run init, so nothing was searched. A spend cap under one "
            "round is raised in the run limits or met by narrowing the round's nodes; then resume.",
            False,
            (NextAct("set-limits", "budget.usd", ""), NextAct("start-run")),
        ),
        # A JOB's ending, never a cycle's: the launch left before it held a run.
        StopReason.NOT_ADMITTED: StopReasonInfo(
            "Not admitted", StopOutcome.HALTED, StopCategory.LIMIT, False, False, "", False, ()
        ),
        # Declared by the REAPER; mid-round because a vanished process died at an arbitrary point.
        StopReason.PRODUCER_VANISHED: StopReasonInfo(
            "Producer vanished",
            StopOutcome.FAILED,
            StopCategory.FAILURE,
            True,
            False,
            "",
            False,
            (),
        ),
        # Like OPTIMIZER_TIMEOUT, caught by TYPE at the round loop: the closed rounds' selection stands.
        StopReason.RENDER_ERROR: StopReasonInfo(
            "Render error", StopOutcome.FAILED, StopCategory.FAILURE, True, True, "", True, ()
        ),
        StopReason.DIVERGED: StopReasonInfo(
            "Diverged",
            StopOutcome.FAILED,
            StopCategory.FAILURE,
            False,
            False,
            "A recorded decision no longer replays under the edited config: fork here to branch "
            "under it, or revert the edit to continue.",
            False,
            (NextAct("start-run", "mode.fork_on_divergence"),),
        ),
        StopReason.OPTIMIZER_TIMEOUT: StopReasonInfo(
            "Optimizer timeout",
            StopOutcome.FAILED,
            StopCategory.FAILURE,
            True,
            False,
            "",
            True,
            (),
        ),
    }
)

_missing_stop_info = set(StopReason) - set(STOP_REASON_INFO)
if _missing_stop_info:
    raise RuntimeError(
        f"STOP_REASON_INFO is missing rows for {sorted(r.value for r in _missing_stop_info)} — "
        "every StopReason needs a label + outcome (domain/phases.py)."
    )
# A next step every surface words names its acts, and no act stands without its words.
_unspelled = sorted(
    r.value for r, info in STOP_REASON_INFO.items() if bool(info.next_step) != bool(info.next_acts)
)
if _unspelled:
    raise RuntimeError(f"STOP_REASON_INFO rows {_unspelled} word a next step one surface lacks")


# A pause the run raises on itself names its own cause, whatever else reached the process.
SELF_PAUSE_CAUSE: dict[StopReason, PauseCause] = {
    StopReason.PANEL_CUT: PauseCause.BOUND,
    StopReason.ADAPTER_OWED: PauseCause.OWED,
}
assert all(STOP_REASON_INFO[r].outcome is StopOutcome.PAUSED for r in SELF_PAUSE_CAUSE)


def stop_reason_outcome(reason: StopReason) -> StopOutcome:
    return STOP_REASON_INFO[reason].outcome


class ErrorCategoryInfo(NamedTuple):
    # The configuration under test answers for it, so a cell of it scores, never a hole.
    charged: bool
    # The stop a refused send (`SendRefusedError`) or a banked hole of it ends the run on.
    stops: StopReason | None


ERROR_CATEGORY_INFO: Annotated[dict[ErrorCategory, ErrorCategoryInfo], shapes_optimizer_prompt] = {
    ErrorCategory.CLIENT: ErrorCategoryInfo(True, None),
    ErrorCategory.PARAMETER_REFUSED: ErrorCategoryInfo(True, None),
    ErrorCategory.SERVER: ErrorCategoryInfo(False, None),
    ErrorCategory.CONNECTION: ErrorCategoryInfo(False, StopReason.BACKEND_UNREACHABLE),
    ErrorCategory.PROVIDER_CREDIT: ErrorCategoryInfo(False, StopReason.PROVIDER_CREDIT),
    ErrorCategory.PROVIDER_THROTTLED: ErrorCategoryInfo(False, StopReason.PROVIDER_THROTTLED),
    ErrorCategory.SPEND_CEILING: ErrorCategoryInfo(False, StopReason.SPEND_BUDGET),
    ErrorCategory.TOKEN_CEILING: ErrorCategoryInfo(False, StopReason.TOKEN_BUDGET),
    ErrorCategory.HOST_ALLOWANCE: ErrorCategoryInfo(False, StopReason.HOST_ALLOWANCE),
    ErrorCategory.WALL_CLOCK: ErrorCategoryInfo(False, StopReason.WALL_CLOCK),
    ErrorCategory.PRICE_LIST_UNREACHABLE: ErrorCategoryInfo(
        False, StopReason.PRICE_LIST_UNREACHABLE
    ),
    ErrorCategory.NO_RATE: ErrorCategoryInfo(False, StopReason.NO_RATE),
    ErrorCategory.PIPELINE: ErrorCategoryInfo(True, None),
    ErrorCategory.HALTED: ErrorCategoryInfo(True, None),
    ErrorCategory.UNSCOREABLE: ErrorCategoryInfo(False, None),
    ErrorCategory.UNKNOWN: ErrorCategoryInfo(True, None),
}
assert set(ERROR_CATEGORY_INFO) == set(ErrorCategory), "every ErrorCategory must take a side"


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


class ErrorRecord(FrozenModel):
    """An arm of ``CycleRecord`` declared here, since ``CycleResult.error`` sits below that union."""

    record_type: Literal["error"] = "error"
    # Exception class name — operator-facing diagnostic, never load-bearing for routing.
    kind: str
    # The exception's first non-blank line, else its class name; the lines after it are `detail`.
    message: str
    detail: str | None
    traceback: str | None = None
    stop_reason: StopReason
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def raised(
        cls,
        exc: BaseException,
        *,
        kind: str,
        stop_reason: StopReason,
        traceback: str | None = None,
        round: int | None = None,
    ) -> ErrorRecord:
        message, _, detail = (str(exc).strip() or type(exc).__name__).partition("\n")
        return cls(
            kind=kind,
            message=message,
            detail=detail or None,
            traceback=traceback,
            stop_reason=stop_reason,
            round=round,
        )
