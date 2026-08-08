"""Pydantic schema for ``dashboard.json``. The writer mutates a plain dict for speed and validates
through this model at every ``_persist()``, so writer/schema drift raises at write time."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import RoundSummary
from promptpotter.domain.phases import DashboardState, RunPhase
from promptpotter.domain.results import HeadlineMetric, SpendBucket, SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "BackendWarning",
    "BackfillLogEntry",
    "DashboardError",
    "InFlightCall",
    "LiveDashboardState",
    "LoopWarning",
    "RunLimits",
    "SpendBucket",
    "SpendRollup",
]


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
    """``state.run_limits`` — the cycle's declared run-limit ceilings, written
    once at ``INIT:exit``. Static, so a fork's reconcile dialog can default against it."""

    max_rounds: int | None = None
    l1_patience: int
    l2_patience: int | None = None
    l3_patience: int | None = None
    pobb_epsilon: float
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    # The ♥ bank's declared ceiling, the DENOMINATOR for the live ``hearts`` count.
    # Without it ``hearts: 3`` is scaleless — the operator cannot tell healthy-of-four
    # from nearly-dead-of-seven, and in lives mode ``max_rounds`` is null, so the round
    # counter it replaces carried the only scale there was. ``None`` when lives is off.
    lives_cap: int | None = None


class LiveDashboardState(StrictModel):
    """``dashboard.json`` — operator-facing snapshot, polled by the webapp.
    ``current_round`` wipes at ``L1_GENERATE:enter``; past deep audit lives in ``round_NNNN.json``."""

    model_config = ConfigDict(validate_assignment=False)

    # Identity stamp — which session-family this dashboard.json describes.
    # Set once at construction; the webapp drops any polled payload whose
    # stamp doesn't match the unit it asked for.
    campaign_id: str
    cycle_id: str
    session_id: str

    # Operator-facing deep link to this cycle's Langfuse trace, composed once
    # (set at construction from the obs bridge's trace id). None when Langfuse
    # is disabled. The webapp can't compose it — LANGFUSE_HOST is backend-only —
    # so it ships pre-built; this is the live-run path to the full nested trace.
    langfuse_trace_url: str | None = None

    # Operator-visible activity name + transition timestamp.
    state: DashboardState = DashboardState.INIT
    state_since: str

    # The single run-state vocabulary (RunPhase). Declared by the runner via
    # control PhaseRecords and projected here, so a paused run reads as
    # "paused" even after dashboard.json goes stale — freshness is no longer
    # load-bearing for control state. ``state`` (above) stays the fine-grained
    # activity (origin / scoring / l1_generate / …); ``run_phase`` is the
    # coarse lifecycle+control axis every surface reads. Never "detached" here
    # (a dead producer can't write) — that value is emitted only by the
    # server-side ``derive_run_phase`` reader.
    run_phase: RunPhase = RunPhase.RUNNING

    stop_reason: str | None = None

    # Current round / candidate / sample-progress markers.
    round: int = 0
    candidate: str = ""
    query: str = ""
    patience: str = ""
    # Banked lives ("hearts") — the high-level ♥ readout when the run is in
    # improvement-banked-budget mode; ``None`` when lives mode is off (the UI then
    # shows the round counter as before). A dynamic per-round marker, not a declared
    # ceiling, so it sits here beside ``round`` rather than in ``run_limits``.
    hearts: int | None = None

    # Origin is round 0 — it lives in ``rounds[]`` as a normal one-candidate
    # RoundSummary, not a separate block. Same shape every L1 round has.
    rounds: list[RoundSummary] = Field(default_factory=list)

    best: float = 0.0
    current_acc: float = 0.0
    # Served headline lift, in LOGITS on the cycle's fixed δ ruler: the incumbent's
    # ``cumulative_theta`` minus the origin's. The ONE derivation of "how far above origin is
    # the incumbent" — the webapp headline chip and the L4 inner progress line read it, neither
    # recomputes it. ``None`` until round 0 has settled with an ability.
    #
    # It was ``best − rounds[0].accuracy``, and claimed "one basis on both sides". That is false
    # under ``per_round_resubset``: each round draws a FRESH subset, so ``best`` is a running max
    # over numbers measured on different sample sets — it selects the luckiest draw, and the
    # origin it subtracts was measured on the full bank. On `justlogic-d234` seed-0 that served
    # **+19%** for a cell whose ability never moved (θ +0.6823 in all four rounds, C0 winning
    # every one, stop `lives_exhausted`). Ability is the only cross-round-comparable series here
    # — the same reason `RoundSummary.cumulative_theta` exists — so the headline reads it.
    # Renamed off `headline_delta` because the units changed; `headline_metric` still picks which
    # fitness number headlines per-candidate TEXT, which is a different question.
    ability_delta: float | None = None
    composite_fitness_formula: str | None = None
    # Campaign default for which fitness number headlines the operator's text
    # surfaces (CampaignConfig.headline_metric). DISPLAY config — the gate is
    # always θ; this only seeds the webapp's client-overridable headline toggle.
    # Stamped at INIT:exit beside run_limits, so a fork carries its own default.
    # The SAME closed set `CampaignConfig.headline_metric` declares — widening it to
    # `str` here only forced the webapp to re-narrow it by hand.
    headline_metric: HeadlineMetric = "accuracy"

    degraded_count: int = 0
    error_count: int = 0

    # Backend retry / warning visibility.
    backend_retry_count: int = 0
    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)
    # Optimizer-loop degradation visibility — sibling to recent_backend_warnings.
    recent_loop_warnings: list[LoopWarning] = Field(default_factory=list)

    total_queries_scored: int = 0
    total_backend_calls: int = 0

    # In-flight sample markers — the OLDEST open sample, derived from the open set rather than
    # assigned per event, since look-ahead leaves more than one open
    # (``view.py::_refresh_open_sample_markers``).
    current_query_payload: str | None = None
    current_sample_id: int | None = None

    # What the LOOP held in flight, never what the operator asked for (that is
    # `.runtime/sample_lookahead.flag`); the two differ for up to one sample.
    sample_lookahead: int = 1
    # Samples launched then discarded unabsorbed — the arming's whole running cost, cumulative.
    sample_lookahead_discards: int = 0

    last_query_elapsed_s: float = 0.0
    wallclock_serialized_at: str | None = None

    n_variants: int
    sp_budget_ttest: int

    # Declared run-limit ceilings (max_rounds / patiences / pobb_epsilon /
    # spend_budget). Written once at INIT:exit; None until then.
    run_limits: RunLimits | None = None

    spend: SpendRollup = Field(default_factory=SpendRollup)

    in_flight: InFlightCall | None = None

    # Paired-PoBB backfill audit — per-sample log capped at 256 entries.
    backfill_log: list[BackfillLogEntry] = Field(default_factory=list)

    # Deep current-round node block (rebuilt every persist).
    current_round: dict[str, Any] = Field(default_factory=dict)

    # Populated only when the runner crashed — operator-facing message +
    # exception class. Absent on normal stops; sole writer is
    # ``LiveDashboardView._handle_error``.
    error: DashboardError | None = None

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
            # Stamped by the STARTING process, not carried from `prior`, and not waiting for
            # INIT:exit — round 0 runs before any INIT event reaches the ledger, so waiting
            # publishes the wrong headline for the whole origin pass.
            "headline_metric": headline_metric,
            "run_phase": RunPhase.RUNNING,
            "stop_reason": None,
            "error": None,
            "in_flight": None,
            "current_round": {},
            "current_query_payload": None,
            "current_sample_id": None,
        }
        return prior.model_copy(update=mine) if prior is not None else cls(**mine)
