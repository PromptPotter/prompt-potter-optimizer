"""Pydantic schema for ``dashboard.json``. The writer mutates a plain dict for speed and validates
through this model at every ``_persist()``, so writer/schema drift raises at write time."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import ConfigDict, Field

from promptpotter.connectors.protocol import ConcurrencyArming, MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import DashboardCandidate, RoundSummary
from promptpotter.domain.phases import DashboardState, RunPhase
from promptpotter.domain.results import HeadlineMetric, OverlapReading
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "BackendWarning",
    "BackfillLogEntry",
    "CurrentRound",
    "DashboardError",
    "InFlightCall",
    "LiveDashboardState",
    "LoopWarning",
    "PobbBlock",
    "RunLimits",
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


class BackfillLogEntry(StrictModel):
    """One paired-PoBB backfill event appended by ``LiveDashboardView._append_backfill``.
    The writer caps the list at 256 entries."""

    round: int
    candidate_idx: int
    candidate_total: int
    sample_id: int
    prior_ids: list[str]


class BackendWarning(StrictModel):
    """One entry in ``recent_backend_warnings`` — backend transport retry / 429 / 5xx surface."""

    ts: str
    kind: str
    attempt: int | None = None
    max_attempts: int | None = None
    wait_s: float | None = None
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str | None = None


class LoopWarning(StrictModel):
    """One entry in ``recent_loop_warnings`` — an optimizer-loop degradation the
    self-healing rails recovered from: zero-candidate round, L2 framing soft-reject, truncation."""

    ts: str
    kind: str
    severity: str
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DashboardError(StrictModel):
    """``dashboard.json::error`` — structured crash summary written by
    ``_handle_error`` on a CRASHED / RENDER_ERROR / DIVERGED exit; absent on a normal stop."""

    kind: str
    message: str
    stop_reason: str


class InFlightCall(StrictModel):
    """``state.in_flight`` — the optimizer LLM call currently in progress.
    Set on ``LLMCallStartRecord``, cleared on the paired ``LLMCallRecord``; ``None`` between calls."""

    call_id: str
    node: str
    model: str | None = None
    round: int | None = None
    candidate_idx: int | None = None
    started_at_ms: int


class RunLimits(StrictModel):
    """``state.run_limits`` — the cycle's run-limit ceilings, stamped at WIRING off the effective
    ``campaign_config``, so a fork's reconcile dialog can default against them. It rode
    ``INIT:enter`` and that record lands after the whole origin has scored, so the operator watched
    the longest phase of the run with no ceiling on screen at all.

    **The two spend arms are the ARMED ceilings, not the declared ones**, re-read from
    ``spend_cap.json`` at every persist (``view.py::_persist``). They were static, and that is
    precisely what made every surface reading them — the control's own prefill, the run strip —
    report a number ``BudgetGate`` had stopped using the moment ``change-spend-budget`` landed."""

    max_rounds: int | None = None
    l1_patience: int
    l2_patience: int | None = None
    l3_patience: int | None = None
    pobb_epsilon: float
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    # DENOMINATOR for the live ``hearts`` count — without it ``hearts: 3`` is scaleless, and
    # in lives mode ``max_rounds`` is null. ``None`` when lives is off.
    lives_cap: int | None = None


class PobbBlock(StrictModel):
    """``current_round.pobb`` — round-wide elimination telemetry, rebuilt every persist."""

    current_id: str = ""
    n_samples: int = 0
    leader_prob: float = 0.0
    posterior_width: float = 1.0
    top: list[dict[str, Any]] = Field(default_factory=list)


class CurrentRound(StrictModel):
    """``dashboard.json::current_round`` — the round in flight, rebuilt whole on every persist.
    The four rules it serves under (no ``live`` flag, ``round`` is ``state.round``, this-round-only
    ``nodes``, one candidate shape) are ``infrastructure/CLAUDE.md`` § LiveDashboardView RESOLVES."""

    round: int = 0
    active_node: str | None = None
    candidates: list[DashboardCandidate] = Field(default_factory=list)
    # Free-form per-node LLM I/O (``build_node_block``), mirroring ``round_NNNN.json::nodes``.
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pobb: PobbBlock = Field(default_factory=PobbBlock)
    # The parent line on its shared cells, stamped at the ELECTION and null before it. Null here
    # is "not measured yet", never "withheld". ONLY this one of the round's readings: the others
    # (`verdict_reason`, `electable_count`, `separable`, `ability`, `health`) reach no live
    # surface, and a served field nothing renders is a note nobody reads.
    overlap: OverlapReading | None = None


class LiveDashboardState(StrictModel):
    """``dashboard.json`` — operator-facing snapshot, polled by the webapp.
    ``current_round`` wipes at ``L1_GENERATE:enter``; past deep audit lives in ``round_NNNN.json``."""

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
    state_since: str

    # The runner's DECLARATION of the coarse lifecycle+control axis, made via control
    # PhaseRecords — so a paused run stays readable as paused once this file goes stale.
    # ``state`` above stays the fine-grained activity. It is an INPUT to
    # ``derive_run_phase``, never the answer: its only writer is the runner's own process,
    # so it cannot report "detached" (a dead producer can't write) and it went on saying
    # "running" forever after a kill. It is NAMED for what it is, so that nobody reading
    # this file in an editor — the folder-UI contract's equal consumer — mistakes it for
    # the answer.
    declared_phase: RunPhase = RunPhase.RUNNING

    # The answer, and WIRE-ONLY: ``exclude=True`` keeps it out of every ``model_dump``, so
    # it never reaches disk, while ``model_fields`` still carries it to the TS generator —
    # which is what lets the browser's type name the field the browser actually reads. The
    # route and the SSE snapshot set it from ``derive_run_phase`` on the way out; nothing
    # in Python reads or writes it, and the writer must never start.
    run_phase: RunPhase = Field(default=RunPhase.RUNNING, exclude=True)

    stop_reason: str | None = None

    round: int = 0
    candidate: str = ""
    query: str = ""
    patience: str = ""
    # Banked lives in improvement-banked-budget mode; ``None`` when lives is off (the UI
    # then shows the round counter). A per-round marker, not a ceiling — hence not in
    # ``run_limits``.
    hearts: int | None = None

    rounds: list[RoundSummary] = Field(default_factory=list)

    # ``None`` until round 0 settles — a `0.0` default reports "Best 0%" for the whole origin
    # pass, which is a measurement nothing has taken (`_update_current_acc` refuses it mid-round).
    best: float | None = None
    current_acc: float | None = None
    # Served headline lift, in LOGITS on the cycle's fixed δ ruler: the parent's ``ability``
    # minus the origin's, ``None`` unless the two share a ruler. The ONE derivation — the chip
    # and the L4 inner progress line read it, neither recomputes. ``None`` until round 0 has
    # settled with an ability. Accuracy cannot answer this: under ``per_round_resubset`` each
    # round draws a fresh subset, so a max over rounds selects the luckiest draw.
    ability_delta: float | None = None
    composite_fitness_formula: str | None = None
    # The same formula as ``{evaluator: coefficient}``, where it IS a weighted sum — what the mask
    # editor's per-evaluator weights seed from. ``None`` says the formula cannot carry them and the
    # control disables rather than guessing, which is the whole point of serving it: a browser
    # parsing coefficients out of the string substitutes a default for whatever its regex missed.
    composite_fitness_weights: dict[str, float] | None = None
    # DISPLAY config — the gate is always θ; this seeds the webapp's client-overridable
    # headline toggle. Stamped at construction (``for_run``), so a fork carries its own.
    headline_metric: HeadlineMetric = "accuracy"

    degraded_count: int = 0
    error_count: int = 0

    backend_retry_count: int = 0
    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)
    recent_loop_warnings: list[LoopWarning] = Field(default_factory=list)

    total_queries_scored: int = 0
    total_backend_calls: int = 0

    # The OLDEST open sample, derived from the open set rather than assigned per event, since
    # look-ahead leaves more than one open (``view.py::_refresh_open_sample_markers``).
    current_query_payload: str | None = None
    current_sample_id: int | None = None
    # EVERY sample in flight, oldest first — the membership test `current_sample_id` cannot
    # answer. That one is the walk's CURSOR and is right to name a single position; asking it
    # "is this row running?" lit one row of N under look-ahead, silently, since the arming is
    # exactly when the operator is watching. Two questions, so two fields.
    open_sample_ids: list[int] = Field(default_factory=list)
    # The order the running candidate DECLARED it would walk. Served as well as streamed, because
    # the SSE event fires once per candidate and a reader that joins after it has no forward view
    # at all. Named `declared_` because the heatmap's `sample_order` is absolute difficulty and
    # this one is relevance — and it is a PLAN: PoBB can stop a candidate before the tail is
    # reached, so no reader may word it as "will".
    declared_sample_order: list[int] = Field(default_factory=list)

    # What the LOOP held in flight, never what the operator asked for (that is
    # `.runtime/sample_lookahead.json`); the two differ for up to one sample.
    sample_lookahead: int = 1
    # Samples launched then discarded unabsorbed — the arming's whole running cost, cumulative.
    sample_lookahead_discards: int = 0
    # The REQUEST — what the operator armed and the walk has not spent, read off
    # `.runtime/sample_lookahead.json` exactly as the armed spend cap is. `sample_lookahead` above
    # is what the loop HELD; between a press and the next launch boundary the two disagree.
    sample_lookahead_armed: int = 1
    # The connector's own declarations, stamped at INIT:exit. SERVED rather than inferred: the
    # browser's only available guess — "is this self-optimization?" — is not the question. `1`
    # says the control does not apply, and went unserved before, so the button took dead presses.
    max_cells_in_flight: int = 1
    concurrency_arming: ConcurrencyArming = "round"
    # The backend's own noun for a measured row, so the browser never picks one off a local flag.
    measured_unit: MeasuredUnit = "sample"

    # ``None`` where the last row recorded no time at all — a cached replay's 0.0 is a real
    # reading and must stay separable from it (``domain/scoring.py::recorded_elapsed_s``).
    last_query_elapsed_s: float | None = None
    wallclock_serialized_at: str | None = None

    n_variants: int
    sp_budget_ttest: int

    # None until INIT:exit.
    run_limits: RunLimits | None = None

    spend: SpendRollup = Field(default_factory=SpendRollup)

    in_flight: InFlightCall | None = None

    backfill_log: list[BackfillLogEntry] = Field(default_factory=list)

    current_round: CurrentRound = Field(default_factory=CurrentRound)

    # Sole writer ``LiveDashboardView._handle_error``; absent on normal stops.
    error: DashboardError | None = None

    # Stamped at run start and riding no ledger record, so a fold off disk cannot answer for them
    # and the serving route stamps the head file's over the replay. Everything NOT in here moves
    # over a run and is folded.
    WIRING_FIELDS: ClassVar[tuple[str, ...]] = (
        "session_id",
        "n_variants",
        "sp_budget_ttest",
        "headline_metric",
        "langfuse_trace_url",
        "max_cells_in_flight",
        "concurrency_arming",
        "measured_unit",
        "run_limits",
    )

    @classmethod
    def for_run(
        cls,
        prior: LiveDashboardState | None,
        *,
        hop: CycleHop,
        session_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        langfuse_trace_url: str | None,
        headline_metric: HeadlineMetric,
    ) -> LiveDashboardState:
        """The state a starting run writes — ``prior`` carried forward WHOLESALE, this process's own facts
        stamped over it. This model IS the on-disk shape, so a hand-picked subset resets what it omits."""
        mine: dict[str, Any] = {
            "campaign_id": hop.campaign_id,
            "cycle_id": hop.cycle_id,
            "session_id": session_id,
            "langfuse_trace_url": langfuse_trace_url,
            "state_since": utcnow_iso(),
            "n_variants": n_variants,
            "sp_budget_ttest": sp_budget_ttest,
            "patience": f"0/{l1_patience}",
            # Not carried from `prior` and not deferred to INIT:exit — round 0 runs before any
            # INIT event reaches the ledger, so waiting mis-headlines the whole origin pass.
            "headline_metric": headline_metric,
            "declared_phase": RunPhase.RUNNING,
            "stop_reason": None,
            "error": None,
            "in_flight": None,
            "current_round": CurrentRound(),
            "current_query_payload": None,
            "current_sample_id": None,
            "open_sample_ids": [],
            "declared_sample_order": [],
        }
        return prior.model_copy(update=mine) if prior is not None else cls(**mine)
