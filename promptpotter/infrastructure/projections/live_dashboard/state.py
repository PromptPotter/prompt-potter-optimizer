"""Pydantic schema for ``dashboard.json``. The writer mutates a plain dict for speed and validates
through this model at every ``_persist()``, so writer/schema drift raises at write time."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, ValidationError, computed_field

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import BenchScore, BenchSubject, bench_missing_reason
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import (
    LiveCandidate,
    OptimizerLimit,
    RoundSummary,
    RunStanding,
)
from promptpotter.domain.phases import DashboardState, RunPhase, StopReason
from promptpotter.domain.results import DisplayMetric, OverlapReading, VerifyStrategy
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import verify_stale_after
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_verify
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "BackendWarning",
    "BenchPassProgress",
    "CatchUpLogEntry",
    "CurrentRound",
    "DashboardError",
    "LiveDashboardState",
    "LoopWarning",
    "RacingBlock",
    "RunLimits",
    "VerifyPassProgress",
    "overlay_criterion_dials",
    "overlay_spend_metered",
    "overlay_verify",
    "warming_payload",
]


def warming_payload(hop: CycleHop, *, run_phase: str) -> dict[str, Any]:
    """The canonical "this cycle has no ``dashboard.json`` yet" body, served at 200 rather than 404 so
    the webapp renders "initialising" instead of appearing offline. It lives beside the model whose
    absence it stands in for, because BOTH wire surfaces serve it — the dashboard route and the SSE
    snapshot — and they had drifted into two hand-written shapes carrying different keys.

    ``run_phase`` is required, not defaulted: a body with no phase is exactly what let the browser
    invent one (a warming cycle read as "stopped", and the run-control button offered Start)."""
    return {
        "warming_up": True,
        "campaign_id": hop.campaign_id,
        "cycle_id": hop.cycle_id,
        "phase_hint": "origin",
        "run_phase": run_phase,
    }


def overlay_spend_metered(body: dict[str, Any], meter: CeilingMeter) -> None:
    """A ``spend`` block this build cannot parse serves none, rather than failing the poll."""
    try:
        spend = SpendRollup.model_validate(body["spend"])
        by_round = {k: SpendRollup.model_validate(v) for k, v in body["spend_by_round"].items()}
    except ValidationError:
        return
    body["spend_metered"] = MeteredSpend.of(spend, meter).model_dump()
    body["spend_metered_by_round"] = {
        k: MeteredSpend.of(r, meter).model_dump() for k, r in by_round.items()
    }


def overlay_criterion_dials(body: dict[str, Any]) -> None:
    """The served formula as its dials and their anchors, where it spells an anchored criterion.
    Derived on the way out: both are pure functions of the string beside them, so a stored copy
    is one more thing a finished cycle's file holds stale."""
    formula = body.get("composite_fitness_formula")
    dials = anchored_criterion_dials(formula) if isinstance(formula, str) else None
    body["composite_fitness_weights"] = (
        None if dials is None else {name: d.weight for name, d in dials.items()}
    )
    body["composite_fitness_anchors"] = (
        None
        if dials is None
        else {name: d.anchor for name, d in dials.items() if d.anchor is not None}
    )


class CatchUpLogEntry(StrictModel):
    """One race catch-up — the priors eliminator ``member`` re-measured on one sample.
    Appended by ``LiveDashboardProjection._append_catch_up``, capped at 256 entries."""

    member: str
    round: int
    candidate_idx: int
    candidate_total: int
    sample_id: int
    prior_ids: list[str]


class BackendWarning(StrictModel):
    """One entry in ``recent_backend_warnings`` — a backend transport or 5xx retry, never a 429.

    That one is the provider's pushback rather than a fault, and is served as ``backpressure``."""

    ts: str
    kind: str
    attempt: int | None = None
    max_attempts: int | None = None
    wait_s: float | None = None
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str | None = None
    # The backend's OWN words about what went wrong. The kind says a cell could not be measured;
    # only this says why — and why is the half that decides whether the operator restarts a daemon,
    # clears a cache or changes nothing. Absent on a wire retry, which has a status code instead.
    detail: str | None = None


class LoopWarning(StrictModel):
    """One entry in ``recent_loop_warnings`` — an optimizer-loop degradation the
    self-healing rails recovered from: a zero-candidate round, a layer's unparseable output, a blank
    terminate, truncation."""

    ts: str
    kind: str
    severity: str
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DashboardError(StrictModel):
    """``dashboard.json::error`` — structured failure summary written by ``_handle_error`` off the
    run's ``ErrorRecord``, on a stop whose ``STOP_REASON_INFO`` row is FAILED; absent otherwise."""

    kind: str
    message: str
    stop_reason: StopReason


class RunLimits(StrictModel):
    """``state.run_limits`` — the cycle's run-limit ceilings, stamped at WIRING off the effective
    ``campaign_config``, so a fork's reconcile dialog can default against them. It rode
    ``INIT:enter`` and that record lands after the whole origin has scored, so the operator watched
    the longest phase of the run with no ceiling on screen at all.

    **The two spend arms and ``max_rounds`` are the ARMED ceilings, not the declared ones**,
    re-read from ``run_limits.json``, the standing ceiling's polled mirror, at every persist
    (``projection.py::_persist``). Held static, every surface reading them — the control's own
    prefill, the run strip — reports a number the run stops using the moment
    ``change-run-limits`` lands."""

    max_rounds: int | None = None
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    # The optimizer's own run-bounding knobs (`OptimizerPacing.limits`), in its own words.
    optimizer: list[OptimizerLimit] = Field(default_factory=list)


class RacingBlock(StrictModel):
    """``current_round.racing`` — the round's standing in its eliminator ``member``'s race.
    Rebuilt every persist."""

    member: str
    current_id: str
    n_samples: int
    leader_prob: float
    posterior_width: float
    top: list[dict[str, Any]]


class BenchPassProgress(StrictModel):
    """``dashboard.json::bench_pass`` — the held-out pass in flight. Its rows are no round's cells."""

    subject: BenchSubject
    label: str = Field(description="The candidate the pass grades, as its row is labelled.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    round: int = Field(description="The round whose selection is graded; 0 is the origin.")
    rows: int = Field(description="Bench rows the pass sends.")
    scored: int = Field(description="Rows of it scored so far.")
    accuracy: float | None = Field(
        default=None,
        description="The gateway's running accuracy over the rows scored; null before the first.",
    )


class VerifyPassProgress(StrictModel):
    """``verify_pass`` on a served dashboard — one candidate being re-scored on unseen cells."""

    label: str = Field(description="The candidate the pass re-scores, as its row is labelled.")
    round: int = Field(description="That candidate's own round; 0 is the origin.")
    rows: int = Field(description="Unseen search cells the pass sends.")
    strategy: VerifyStrategy


def overlay_verify(body: dict[str, Any], cycle_dir: Path) -> None:
    """Each candidate's last verify reading, and the pass in flight, onto a served
    ``dashboard.json`` body — read off the cycle's LEDGER, where the process that ran the pass
    banked it. That process is the runner only for a saturation check, so the runner's file can
    hold neither for an operator's verify, least of all on a halted cycle. Mutates in place.

    **A REPLAY must not call this**: the readings are the ones standing now."""
    verify = scan_ledger_verify(CycleLayout(cycle_dir).ledger)
    readings = {
        label: reading.model_dump(mode="json") for label, (_, reading) in verify.graded.items()
    }
    for closed in body.get("rounds") or []:
        for row in closed.get("candidates") or []:
            row["verify"] = readings.get(row.get("label"))
    stale_after = verify_stale_after(cycle_dir)
    in_flight = verify.open is not None and stale_after is not None and time.time() < stale_after
    body["verify_pass"] = (
        VerifyPassProgress.model_validate(verify.open).model_dump(mode="json")
        if in_flight
        else None
    )


class CurrentRound(StrictModel):
    """``dashboard.json::current_round`` — the round in flight, rebuilt whole on every persist.
    The four rules it serves under (no ``live`` flag, ``round`` is ``state.round``, this-round-only
    ``nodes``, one candidate shape) are ``infrastructure/CLAUDE.md`` § LiveDashboardProjection RESOLVES."""

    round: int = 0
    active_node: str | None = None
    # The node the ledger named at `measure:enter`, null before one has; `active_node` equals it
    # while it measures.
    measurement_node: str | None = None
    candidates: list[LiveCandidate] = Field(default_factory=list)
    # Free-form per-node optimizer LLM I/O (``build_node_block``), mirroring the audit twin's
    # ``nodes``.
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Null before the round's first standing, and on an optimizer that races nothing.
    racing: RacingBlock | None = None
    # The best-so-far line on its shared cells, stamped at the ELECTION and null before it; null
    # is "not measured yet", never "withheld". ONLY this one of the round's readings: the others
    # (`verdict_reason`, `electable_count`, `separable`, `ability`, `health`) reach no live
    # surface, and a served field nothing renders is a note nobody reads.
    overlap: OverlapReading | None = None


class LiveDashboardState(StrictModel):
    """``dashboard.json`` — operator-facing snapshot, polled by the webapp.
    ``current_round`` wipes when the round number moves; past deep audit lives in ``round_NNNN.json``."""

    model_config = ConfigDict(validate_assignment=False)

    # Set once at construction; the webapp drops any polled payload whose stamp doesn't
    # match the unit it asked for.
    campaign_id: str
    cycle_id: str
    session_id: str

    # WHICH MOMENT this file is of — its ``Cut``. Without it a holder cannot tell a lagging copy
    # from a current one, nor join this file to the event stream except by guessing. ``-1`` until
    # the first record lands.
    at_offset: int = -1

    # Composed at construction because the webapp can't — LANGFUSE_HOST is backend-only.
    # None when Langfuse is disabled.
    langfuse_trace_url: str | None = None

    state: DashboardState = DashboardState.INIT
    # The running optimizer step's own words (`OptimizerPhase.activity`) while `state` is
    # `optimizer_step`; null in every other state.
    optimizer_step: str | None = None
    state_since: str

    # The runner's DECLARATION of the coarse lifecycle+control axis, made via control
    # PhaseRecords — so a paused run stays readable as paused once this file goes stale.
    # ``state`` above stays the fine-grained activity. It is an INPUT to
    # ``derive_run_phase``, never the answer: its only writer is the runner's own process,
    # so it cannot report "detached" (a dead producer can't write) and says "running" forever
    # after a kill. It is NAMED for what it is, so that nobody reading
    # this file in an editor — the folder-UI contract's equal consumer — mistakes it for
    # the answer.
    declared_phase: RunPhase = RunPhase.RUNNING

    # The answer, and WIRE-ONLY: ``exclude=True`` keeps it out of every ``model_dump``, so
    # it never reaches disk, while ``model_fields`` still carries it to the TS generator —
    # which is what lets the browser's type name the field the browser actually reads. The
    # route and the SSE snapshot set it from ``derive_run_phase`` on the way out; nothing
    # in Python reads or writes it, and the writer must never start.
    run_phase: RunPhase = Field(default=RunPhase.RUNNING, exclude=True)

    stop_reason: StopReason | None = None

    round: int = 0
    candidate: str = ""
    # The last closed round's; ``None`` until round 0 closes. A per-round marker, not a
    # ceiling — hence not in ``run_limits``.
    run_standing: RunStanding | None = None

    rounds: list[RoundSummary] = Field(default_factory=list)

    # `best_round_on_shared_cells` over `rounds` — `index.json::best_accuracy`'s number, never a
    # max over each round's own subset. ``None`` until round 0 settles, never a `0.0`.
    best: float | None = None
    current_acc: float | None = None
    # The headline for every optimizer: the selection and the origin graded on the held-out bench
    # set. Null until the bench pass lands; a split holding nothing out says so in `missing_reason`.
    bench_score: BenchScore | None = None
    # The bench pass in flight, null outside one: while it is set, the cells being scored are
    # held-out rows of `subject`'s pass and no round's.
    bench_pass: BenchPassProgress | None = None
    # A verify pass in flight on one of this cycle's candidates, null outside one. Wire-only,
    # set by ``overlay_verify`` beside each candidate row's ``verify`` reading.
    verify_pass: VerifyPassProgress | None = Field(default=None, exclude=True)
    # That lift per dollar the SEARCH incurred (`BenchScore.lift_per_usd`, `evidence`'s rule too),
    # settled in ``compose``: spend moves on every call, and a browser dividing the two divides
    # two polls.
    bench_lift_per_incurred_usd: float | None = None
    composite_fitness_formula: str | None = None
    # The same formula as ``{term: weight}``, where it IS an anchored criterion — what the scoring
    # form's dials seed from. ``None`` says the formula cannot carry them and the form opens on
    # its expression rather than guessing, which is the whole point of serving it: a browser
    # parsing coefficients out of the string substitutes a default for whatever its regex missed.
    # WIRE-ONLY like ``run_phase``, set by ``overlay_criterion_dials``.
    composite_fitness_weights: dict[str, float] | None = Field(default=None, exclude=True)
    # The level each anchored dial in it is read against, by term. Wire-only beside it.
    composite_fitness_anchors: dict[str, float] | None = Field(default=None, exclude=True)
    # DISPLAY config — the selector decides on its own objective; this seeds the webapp's
    # client-overridable metric toggle. Stamped at construction (``for_run``), so a fork carries its own.
    display_metric: DisplayMetric = "accuracy"
    # Mirrors `RoundResult.stamps_theta` — campaign-wide, so the per-arm θ column reads ONE flag
    # rather than a candidate's `None` theta, which a cold ruler leaves `None` too.
    stamps_theta: bool = False

    # Run totals. `degraded_count` is over `total_backend_calls`; a round's own is its
    # document's `degraded_samples`.
    degraded_count: int = 0
    error_count: int = 0

    backend_retry_count: int = 0
    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)
    recent_loop_warnings: list[LoopWarning] = Field(default_factory=list)

    total_queries_scored: int = 0
    total_backend_calls: int = 0

    # The OLDEST open sample, derived from the open set rather than assigned per event, since
    # look-ahead leaves more than one open (``projection.py::_refresh_open_sample_markers``).
    current_query_payload: str | None = None
    current_sample_id: int | None = None
    # EVERY sample in flight, oldest first — the membership test `current_sample_id` cannot
    # answer. That one is the walk's CURSOR and names a single position; asked "is this row
    # running?" it lights one row of N under look-ahead. Two questions, so two fields.
    open_sample_ids: list[int] = Field(default_factory=list)
    # The order the running candidate DECLARED it would walk. Served as well as streamed, because
    # the SSE event fires once per candidate and a reader that joins after it has no forward view
    # at all. Named `declared_` because the heatmap's `sample_order` is absolute difficulty and
    # this one is relevance — and it is a PLAN: an eliminator can stop a candidate before the tail is
    # reached, so no reader may word it as "will".
    declared_sample_order: list[int] = Field(default_factory=list)

    # The depth IN FORCE — how many samples the walk holds in flight, read straight off
    # `.runtime/sample_lookahead.json`. ONE number: a second field for "what the loop last held"
    # is a log with no round-boundary writer, and no surface may reconcile the two.
    sample_lookahead: int = 1
    # Whether that depth outlives its round — the operator's auto-arm, read off the same file.
    sample_lookahead_auto: bool = False
    # Samples launched then discarded unabsorbed — the depth's whole running cost, cumulative.
    sample_lookahead_discards: int = 0
    # The scoring phase's calls in flight; how many its stop rules allow right now; and the most it
    # could ever hold — all counted over every candidate walking and the race catch-ups
    # (`scoring/query_loop.py::FlightGauge`). Between phases `lookahead_most` is the next round's
    # (`arms_per_round` x `sp_budget_round`), so the operator can size a press before it starts —
    # ``None`` there under an optimizer that declares no `arms_per_round`.
    in_flight: int = 0
    lookahead_allowed: int = 0
    lookahead_most: int | None = 0
    # How many MORE cells the spend limits admit, and what one reserves: a reserve a few worst
    # cases wide pins the walk at one call while the depth reads armed. ``None``: no book binds.
    lookahead_affordable: int | None = None
    cell_reserve_usd: float | None = None
    # Whether money, not the armed depth, holds the walk; and the deepest press the next walk can
    # take. WIRE-ONLY like ``run_phase``: both read the ARMED depth, so ``overlay_armed_controls``
    # sets them beside it on the way out.
    lookahead_money_pinned: bool = Field(default=False, exclude=True)
    lookahead_pick_max: int = Field(default=1, exclude=True)
    # The call the round's next decision waits on — calls are taken in walk order, so one slow cell
    # at a candidate's head holds every call behind it — and when it was launched (epoch seconds).
    waiting_on: str | None = None
    waiting_since: float | None = None
    # The model provider holding calls out but unsent (`rate_limit.py::Backpressure`); None while
    # it holds nothing.
    backpressure: BackpressureReading | None = None
    # The connector's own declarations, stamped at INIT:exit. SERVED rather than inferred: the
    # browser's only available guess — "is this self-optimization?" — is not the question. `1`
    # says the control does not apply.
    max_cells_in_flight: int = 1
    # The backend's own noun for a measured row, so the browser never picks one off a local flag.
    measured_unit: MeasuredUnit = "sample"

    # ``None`` where the last row recorded no time at all — a cached replay's 0.0 is a real
    # reading and must stay separable from it (``domain/scoring.py::recorded_elapsed_s``).
    last_query_elapsed_s: float | None = None
    wallclock_serialized_at: str | None = None

    # The most arms one round races (`OptimizerPacing.arms_per_round`); ``None`` where undeclared.
    arms_per_round: int | None
    sp_budget_round: int

    # None until INIT:exit.
    run_limits: RunLimits | None = None

    spend: SpendRollup = Field(default_factory=SpendRollup)
    # WIRE-ONLY like `run_phase`: the dashboard route sets it off `spend` under the campaign's
    # `ceiling_meter`, a manifest fact no ledger record carries, so a replay serves it too.
    spend_metered: MeteredSpend | None = Field(default=None, exclude=True)

    # The SAME fold, keyed by the round each call stamped itself with — so "what did round 3 cost,
    # and how much of its input did providers serve off their own prefix cache" is answerable at
    # all. `spend` above is one running total for the whole cycle, and `rounds[]` carries no cost.
    #
    # PER ROUND, not cumulative: the atom is what a bar needs and what a cumulative series is
    # summed FROM, and the reverse does not hold. `evidence/read.py::_spend_to_round` folds these
    # forward for its own cumulative reading rather than walking the ledger a second time.
    #
    # A call carrying no `round` banks at "0" — it ran before any round closed (init, the origin
    # score), and dropping it would under-report every prefix. Keys are `str` because JSON has no
    # integer keys and a round-trip must not change the shape. NOT clamped by a rewind, unlike
    # `rounds[]`: a re-measured round cost money both times, and the sum over this map is what
    # reconciles against `spend`.
    spend_by_round: dict[str, SpendRollup] = Field(default_factory=dict)
    # WIRE-ONLY, `spend_metered` per round off `spend_by_round`, so a bar is read like the cap.
    spend_metered_by_round: dict[str, MeteredSpend] | None = Field(default=None, exclude=True)

    catch_up_log: list[CatchUpLogEntry] = Field(default_factory=list)

    current_round: CurrentRound = Field(default_factory=CurrentRound)

    # Sole writer ``LiveDashboardProjection._handle_error``; absent on normal stops.
    error: DashboardError | None = None

    # Stamped at run start and riding no ledger record, so a fold off disk cannot answer for them
    # and the serving route stamps the head file's over the replay. Everything NOT in here moves
    # over a run and is folded.
    WIRING_FIELDS: ClassVar[tuple[str, ...]] = (
        "session_id",
        "arms_per_round",
        "sp_budget_round",
        "display_metric",
        "langfuse_trace_url",
        "max_cells_in_flight",
        "measured_unit",
        "run_limits",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bench_missing_reason(self) -> str | None:
        """Why ``bench_score`` is null; null beside one."""
        return None if self.bench_score is not None else bench_missing_reason(self.stop_reason)

    @classmethod
    def for_run(
        cls,
        prior: LiveDashboardState | None,
        *,
        hop: CycleHop,
        session_id: str,
        arms_per_round: int | None,
        sp_budget_round: int,
        langfuse_trace_url: str | None,
        display_metric: DisplayMetric,
    ) -> LiveDashboardState:
        """The state a starting run writes — ``prior`` carried forward WHOLESALE, this process's own facts
        stamped over it. This model IS the on-disk shape, so a hand-picked subset resets what it omits."""
        mine: dict[str, Any] = {
            "campaign_id": hop.campaign_id,
            "cycle_id": hop.cycle_id,
            "session_id": session_id,
            "langfuse_trace_url": langfuse_trace_url,
            "state_since": utcnow_iso(),
            "arms_per_round": arms_per_round,
            "sp_budget_round": sp_budget_round,
            # Not carried from `prior` and not deferred to INIT:exit — round 0 runs before any
            # INIT event reaches the ledger, so waiting mis-headlines the whole origin pass.
            "display_metric": display_metric,
            "declared_phase": RunPhase.RUNNING,
            "stop_reason": None,
            "error": None,
            # A resumed run grades its selection again; the prior pass graded a stale one.
            "bench_score": None,
            "bench_pass": None,
            "current_round": CurrentRound(),
            "current_query_payload": None,
            "current_sample_id": None,
            "open_sample_ids": [],
            "declared_sample_order": [],
        }
        return prior.model_copy(update=mine) if prior is not None else cls(**mine)
