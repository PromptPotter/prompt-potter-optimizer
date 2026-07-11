"""Pydantic schema for ``dashboard.json`` — the live operator dashboard.

This file is the single source of truth for the dashboard payload shape.
``LiveDashboardView`` accumulates state in a plain ``dict[str, Any]`` for
in-memory mutation efficiency, but at every ``_persist()`` boundary the
dict is validated through this model — drift between the writer and the
schema raises at write time, not silently in production.

The model is also the input to ``scripts/build_ts_types.py``, which emits
``webapp/lib/api/types.generated.ts`` so the webapp's TypeScript consumers
see the same shape the writer ships. Adding or renaming a field here
without regenerating the TS types fails the CI guard.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.phases import RunPhase
from promptpotter.domain.results import HeadlineMetric, RoundSummary
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


class BackfillLogEntry(BaseModel):
    """One paired-PoBB backfill event appended by ``LiveDashboardView._append_backfill``.

    Names the round/candidate the backfill fired during, the sample the
    priors were caught up on, and which priors gained a measurement. The
    list is capped at 256 entries in the writer.
    """

    model_config = ConfigDict(extra="forbid")

    round: int
    candidate_idx: int
    candidate_total: int
    sample_id: int
    prior_ids: list[str]


class SpendBucket(BaseModel):
    """One spend sub-bucket (backend or optimizer-loop). Mutated only by
    :meth:`LiveDashboardView._handle_token_usage` — the sole writer for
    ``dashboard.json::spend`` after the canonical-ledger collapse.
    """

    model_config = ConfigDict(extra="forbid")

    used_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    rate_known: bool = False
    model: str | None = None
    # Tokens billed under this bucket whose USD cost couldn't be resolved (no wire
    # cost AND no rate on file). >0 means the USD cap is blind to real spend here —
    # the dashboard surfaces a "USD cap inactive" warning; the token cap backstops.
    unpriced_tokens: int = 0


class SpendRollup(BaseModel):
    """``state.spend`` — two-bucket spend rollup + total.

    Carries spend only; the armed USD ceiling lives in ``run_limits.spend_budget_usd``
    (the single authoritative budget source every surface reads). There is no
    ``budget_usd`` here — it was a always-null duplicate that let the RemoteBar and
    the chat job-bar disagree."""

    model_config = ConfigDict(extra="forbid")

    backend: SpendBucket = Field(default_factory=SpendBucket)
    loop: SpendBucket = Field(default_factory=SpendBucket)
    total_used_usd: float = 0.0

    @property
    def total_tokens_used(self) -> int:
        """Cumulative tokens across both buckets (input + output) — the token
        halt probe's source, mirroring ``total_used_usd`` for the USD gate."""
        return (
            self.backend.input_tokens
            + self.backend.output_tokens
            + self.loop.input_tokens
            + self.loop.output_tokens
        )


class BackendWarning(BaseModel):
    """One entry in ``recent_backend_warnings`` — backend transport retry / 429 / 5xx surface."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    kind: str
    attempt: int | None = None
    max_attempts: int | None = None
    wait_s: float | None = None
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str | None = None


class LoopWarning(BaseModel):
    """One entry in ``recent_loop_warnings`` — an optimizer-loop degradation the
    self-healing rails recovered from (zero-candidate round, L2 framing
    soft-reject, injection budget truncation). Projected from the canonical
    :class:`~promptpotter.domain.run_records.RoundWarningRecord`. Previously
    log-only; now visible on the dashboard / file-tree alongside
    ``recent_backend_warnings``."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    kind: str
    severity: str
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DashboardError(BaseModel):
    """``dashboard.json::error`` — structured crash summary written by
    :meth:`LiveDashboardView._handle_error` from the canonical ``ErrorRecord``
    when the runner exits via ``CRASHED`` / ``RENDER_ERROR`` / ``DIVERGED``.
    ``message`` is the operator-actionable text; ``kind`` is the exception
    class name; ``stop_reason`` echoes the ledger ``StopReason``. Absent on
    normal stops (paused / completed)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    message: str
    stop_reason: str


class InFlightCall(BaseModel):
    """``state.in_flight`` — the optimizer LLM call currently in progress.

    Set on :class:`LLMCallStartRecord`; cleared on the paired
    :class:`LLMCallRecord` by ``call_id``. ``None`` between calls — the
    explicit None lets the webapp distinguish "no call" from "stale slot".
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    node: str
    model: str | None = None
    round: int | None = None
    candidate_idx: int | None = None
    started_at_ms: int


class RunLimits(BaseModel):
    """``state.run_limits`` — the cycle's declared run-limit ceilings, written
    once at ``INIT:exit`` from the ``OptimizationConfig``. Static (unlike the
    live ``patience`` "N/max" string): the operator-facing source the fork
    reconcile dialog defaults against ("3 of 6 rounds left"). A steered fork
    re-emits its own reconciled limits here at its INIT.
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int | None = None
    l1_patience: int
    l2_patience: int | None = None
    l3_patience: int | None = None
    pobb_epsilon: float
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    # The ♥ bank's declared ceilings, the DENOMINATOR for the live ``hearts`` count.
    # Without them ``hearts: 3`` is scaleless — the operator cannot tell healthy-of-four
    # from nearly-dead-of-seven, and in lives mode ``max_rounds`` is null, so the round
    # counter it replaces carried the only scale there was. ``None`` when lives is off.
    lives_start: int | None = None
    lives_cap: int | None = None


class LiveDashboardState(BaseModel):
    """``dashboard.json`` — operator-facing snapshot, polled by the webapp.

    See :class:`promptpotter.infrastructure.projections.live_dashboard.LiveDashboardView`
    for the writer. The two surfaces inside this file:

    * ``rounds[]`` — completed-round summaries (one entry per closed
      round). Sole source for the FitnessChart, TrendChart, TopStrip
      sparkline, LineageTree.
    * ``current_round`` — the in-flight round's deep node block (L1
      generate / critique / score in progress, plus PoBB telemetry).
      Wiped at ``L1_GENERATE:enter`` so a fresh round starts with an
      empty in-flight buffer. Past rounds' deep audit lives in
      ``round_NNNN.json``, lazy-fetched by the webapp.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

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

    # Operator-visible state name + transition timestamp.
    state: str = "init"
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

    # In-flight sample markers (cleared on ``sample_scored``).
    current_query_payload: str | None = None
    current_sample_id: int | None = None

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
        campaign_id: str,
        cycle_id: str,
        session_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        langfuse_trace_url: str | None,
    ) -> LiveDashboardState:
        """The state a starting run writes — ``prior`` (a resume/fork seed) carried
        forward wholesale, with only this process's own facts stamped over it.

        The cycle's ACCUMULATED trajectory all carries: rounds, best, current_acc,
        spend, the four counters, both warning lists, run_limits, headline_metric,
        hearts, the backfill log. *All* of it, because this model IS the on-disk shape,
        so a hand-picked subset silently resets whatever it forgets to name — nine
        fields did exactly that, and the constructor's first flush persisted the zeros.

        What does not carry is everything describing the process that just ended: this
        run's identity + trace, and the liveness markers that are lies the moment the
        previous writer died. ``run_phase`` and ``stop_reason`` reset together — a
        resumed run is RUNNING, and a carried stop reason would contradict the phase
        printed beside it.
        """
        mine: dict[str, Any] = {
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "session_id": session_id,
            "langfuse_trace_url": langfuse_trace_url,
            "state_since": utcnow_iso(),
            "n_variants": n_variants,
            "sp_budget_ttest": sp_budget_ttest,
            "patience": f"0/{l1_patience}",
            "run_phase": RunPhase.RUNNING,
            "stop_reason": None,
            "error": None,
            "in_flight": None,
            "current_round": {},
            "current_query_payload": None,
            "current_sample_id": None,
        }
        return prior.model_copy(update=mine) if prior is not None else cls(**mine)
