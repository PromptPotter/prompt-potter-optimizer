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

from promptpotter.domain.results import OriginSummary, RoundSummary

__all__ = [
    "BackendWarning",
    "BackfillLogEntry",
    "DashboardError",
    "InFlightCall",
    "LiveDashboardState",
    "LoopWarning",
    "SpendBucket",
    "SpendRollup",
]


class BackfillLogEntry(BaseModel):
    """One paired-PoBB backfill event appended by ``candidate_block.append_backfill``.

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


class SpendRollup(BaseModel):
    """``state.spend`` — two-bucket spend rollup + total."""

    model_config = ConfigDict(extra="forbid")

    backend: SpendBucket = Field(default_factory=SpendBucket)
    loop: SpendBucket = Field(default_factory=SpendBucket)
    total_used_usd: float = 0.0
    budget_usd: float | None = None


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
    normal stops (interrupted / completed)."""

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

    # Operator-visible state name + transition timestamp.
    state: str = "init"
    state_since: str

    stop_reason: str | None = None

    # Current round / candidate / sample-progress markers.
    round: int = 0
    candidate: str = ""
    query: str = ""
    patience: str = ""

    origin: OriginSummary = Field(default_factory=lambda: OriginSummary(accuracy=0.0, samples=0))
    rounds: list[RoundSummary] = Field(default_factory=list)

    best: float = 0.0
    current_acc: float = 0.0
    composite_fitness_formula: str | None = None

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

    # Adaptive queue mechanism's expected next-sample order at candidate-start.
    hard_sample_order: list[int] | None = None

    last_query_elapsed_s: float = 0.0
    wallclock_serialized_at: str | None = None

    n_variants: int
    sp_budget_ttest: int

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
