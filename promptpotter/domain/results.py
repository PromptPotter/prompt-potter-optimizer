"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, computed_field

from promptpotter.domain.escalation_signals import (
    EscalationSignal,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import EvidenceGrounding, OptSearchPoint
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.run_records import DecisionRecord

__all__ = [
    "CandidateProposal",
    "CritiqueReadout",
    "CycleResult",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "EliminationContext",
    "PayloadOutcome",
    "RoundOrigin",
    "RoundResult",
    "RoundSummary",
    "RoundSummaryCandidate",
    "SampleOrderStep",
    "ScoredCandidate",
    "SweepBatchResult",
    "WarningDict",
    "candidate_label",
    "is_round_winner",
]


class EliminationContext(TypedDict, total=False):
    """PoBB exit context on a ``ScoredCandidate`` — written by ``decode_signal_effect``
    when the elimination check fires. Empty ``{}`` when the candidate wasn't cut.
    ``leader_label`` is decorated post-construction (needs prior-rank lookup), so the
    whole shape is ``total=False``. Disjoint from :class:`DegradationContext`."""

    p_best: float
    epsilon: float
    leader_id: str
    queries_scored: int
    total_queries: int
    n_priors: int
    leader_locked: bool
    leader_label: str


class DegradationContext(TypedDict, total=False):
    """DegradationCheck / scoring-error-abort exit context on a ``ScoredCandidate``.
    Empty ``{}`` when the candidate wasn't degradation-cut. Disjoint from
    :class:`EliminationContext` — the renderer branches on which is non-empty."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


class CritiqueReadout(TypedDict, total=False):
    """Serialized ``L1CritiqueOutput`` as it rides ``RoundResult.critique`` and the
    dispatch ``RoundDigest``. Domain-local mirror of the three load-bearing fields so
    a round file round-trips without the optimization layer's schema in scope; the
    optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate label — `C0` for origin, `C{N}.{idx+1}` otherwise. Sole writer."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def is_round_winner(changes_description: str | None, winner_label: str) -> bool:
    """The round winner is the candidate whose diff description equals the round's
    elected label. Sole definition of the rule — the round-file scoreboard
    (`_build_scoreboard`) and the dashboard round summary (`build_round_summary`)
    both call this so the operator-visible winner flag can't diverge between the
    two surfaces."""
    return bool(changes_description) and changes_description == winner_label


class ScoredCandidate(BaseModel):
    """One candidate's L1 score report — the single shape for round-file scores.

    ``model_dump()`` *is* the wire format; ``model_validate()`` reads it back.
    ``ci_lo``/``ci_hi`` are computed from ``hits``/``total`` (sole Wilson site),
    so they round-trip through serialization without being stored or recomputed
    by readers.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candidate_id: str
    label: str
    changes_description: str = ""
    accuracy: float
    composite_fitness: float
    hits: int
    total: int
    evaluators: dict[str, float] = Field(default_factory=dict)
    pipeline_params_override: dict[str, Any] | None = None
    # The fully-resolved per-node config this candidate's searchpoint executes —
    # origin floor ⊕ this candidate's delta, config-only (model/provider/
    # reasoning_effort/temperature + ``steps``; the prompt rides ``prompt_fields``).
    # Served so the OBSERVE view reads the effective config verbatim and never
    # re-merges client-side. Distinct from the sparse ``pipeline_params_override``
    # above (the fork transport) — two data classes, not a stitch.
    resolved_pipeline_params: dict[str, Any] | None = None
    # The candidate's evolved prompt (``OptSearchPoint.prompt_field_dict()`` shape).
    # Paired with ``pipeline_params_override`` this is the full searchpoint an
    # operator selects to seed an operator-steered fork (read side, Decision F).
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    # Why a partial subset was scored (``scored_samples < expected_samples``):
    # "" (full / not partial) | "pobb" (automatic elimination) | "skip" (operator
    # early-abort — marks the cycle ``human_intervened``). Distinct from the
    # ``elimination_stopped``/``escalation_aborted`` outcome booleans.
    partial_reason: str = ""
    invalid: bool = False
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    elimination_context: EliminationContext = Field(default_factory=EliminationContext)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # Origin restricted to this candidate's measured samples — apples-to-apples
    # when PoBB locks mid-budget; equals full-set origin when fully scored.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Difficulty-adjusted Rasch ability (+ Laplace SE) on the round's joint-fit scale — the
    # subset-invariant metric the winner election ranks by (`elect_round_winner`), stamped from
    # that single fit. Distinct from subset-relative `accuracy`: it discounts for *which* samples
    # this candidate saw, so it explains a lower-accuracy winner. `None` for candidates outside
    # the election fit (eliminated / under the coverage floor).
    theta: float | None = None
    theta_se: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ci_lo(self) -> float:
        from promptpotter.shared.statistics import wilson_ci

        return wilson_ci(self.hits, self.total)[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ci_hi(self) -> float:
        from promptpotter.shared.statistics import wilson_ci

        return wilson_ci(self.hits, self.total)[1]


class CandidateProposal(BaseModel):
    """LLM-proposed candidate — OSP + nested pipeline-params override, persisted across generate→score for resume replay."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="Mirrors osp.lineage.evidence_grounding — duplicated so audit / display sites can read it before OSP construction.",
    )


class RoundOrigin(BaseModel):
    """The round's challenger anchor. On probe rounds, scalars reflect the probe subset, not the full set."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite_fitness: float
    osp: OptSearchPoint
    # Per-sample ``QueryMeasurement`` rows plus open-ended stale-data protocol
    # markers (``retry_of_degraded`` etc.) — kept ``dict`` so the markers survive
    # serialization (a closed model would strip them); readers cast at hot sites.
    results: list[dict[str, Any]] = Field(default_factory=list)
    label: str
    evaluators: dict[str, float] = Field(default_factory=dict)


class SampleOrderStep(BaseModel):
    """One measurement step in a round's adaptive sample-selection timeline.

    Frozen snapshot of the adaptive queue mechanism's state the moment it picked
    the ``step``-th sample: ``computed`` are the samples already measured (in
    measurement order), ``planned`` the picker's intended order for the remaining
    samples under the posterior at that step, ``current_sample_id`` the one about
    to be measured (``planned[0]``, or ``None`` at the terminal step). The
    ``planned`` tail re-ranks every step, so each row is the *frozen* plan as it
    was then — the trajectory hover reads one row per step.
    """

    model_config = ConfigDict(frozen=True)

    step: int
    current_sample_id: int | None = None
    computed: list[int] = Field(default_factory=list)
    planned: list[int] = Field(default_factory=list)


class RoundResult(BaseModel):
    """Per-round outcome — the flat wire shape persisted to `index.json`.

    Two field groups: checkpoint-critical scalars (the resume/diff surface) and the
    raw payload (per-candidate detail for replay, audit, full rendering). They were
    once two base classes; flattened here because nothing instantiated either half
    on its own. `diagnostics`/`critique`/`health` are computed post-scoring; read by
    dispatch's `diagnostics`/`critique` signals and rendered by every health surface.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # --- checkpoint-critical scalars (no raw payloads) ---
    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    hits: int
    total: int
    improved: bool
    # One-sided two-proportion p-value vs origin; drives the IMPROVED gate. None ⇒ no test ran.
    p_value: float | None = None
    # When `improved=False` despite Δ>0: which of {delta_threshold, significance, min_samples} blocked promotion.
    improved_reason: str | None = None
    degraded_samples: int = 0
    # Fatal-warning samples discarded from hits/total/accuracy on the winner's run.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Origin restricted to the winner's measured samples — apples-to-apples when PoBB locks
    # at q8/20 while origin has 20. Drives `improved`, p_value, verdict Δ.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Per-sample best-so-far accuracy across rounds; dashboard renders "current best" without walking priors.
    cumulative_accuracy: float = 0.0
    # --- raw payload ---
    prompt_fields: dict[str, Any]
    # The elected winner's `memory.task_context` (TaskDecomposition.to_dict()), so
    # `Cycle.absorb_round` adopts an L1 winner's task_context delta onto the cycle's
    # working OSP. It rides its OWN field, not `prompt_fields`, precisely so it does
    # NOT enter `render()`/the identity gate/the content hash — those stay symmetric.
    # None when the winner carried no task-framing (default TaskDecomposition).
    winner_task_context: dict[str, str] | None = None
    pipeline_params: dict[str, Any] | None = None
    # Prior round's accuracy (or campaign origin for round 0).
    origin_accuracy: float = 0.0
    # Per-sample rows — ``QueryMeasurement`` + stale-data markers (see ``RoundOrigin.results``).
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results — lets resume rescore under a changed scorer + replay decisions.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    # ResumeCheckpoint records consumed by the divergence-replay walker.
    decisions: list[DecisionRecord] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 yield + failure counts; defaults assume "all valid" for replay paths bypassing the detector.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0
    # Adaptive-queue-mechanism sample-selection timeline for the round's
    # representative (longest-surviving) candidate — one row per measurement
    # step, each a frozen (computed, planned) split. Mirrors ``selection``'s
    # longest-candidate rule (``round_summary._measurement_order``) so the
    # executed prefix lines up. Empty on replay / cache paths that bypass the
    # online picker.
    sample_order_timeline: list[SampleOrderStep] = Field(default_factory=list)
    # --- computed post-scoring ---
    diagnostics: RoundDiagnostics | None = None
    critique: CritiqueReadout | None = None
    # Context-aware degradation verdict, stamped at round close (sole compute
    # site); every surface (dashboard summary, CLI, round file) renders this.
    health: DegradationHealth | None = None


class CycleError(BaseModel):
    """Structured error carried on :class:`CycleResult` when the runner exited
    on ``CRASHED`` / ``RENDER_ERROR`` / ``DIVERGED``.

    Mirrors the trailing ``ErrorRecord`` on the canonical ledger; carried as a
    typed field so :class:`~promptpotter.application.jobs.registry.JobRegistry`
    and the post-run teardown read crash detail from one place without
    coupling to projection state. The runner's ``except`` sites build this
    instance and call ``emit_error_record`` from the same kwargs.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    message: str
    traceback: str | None = None


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    origin_accuracy: float
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 1
    # Set when ``stop_reason`` ∈ ``{CRASHED, RENDER_ERROR, DIVERGED}``;
    # ``None`` on clean completions. The runner's ``except`` sites populate
    # this in lockstep with the ``emit_error_record`` ledger append.
    error: CycleError | None = None


class DiagnosticRunRecord(BaseModel):
    """One on-demand candidate verification → webapp Verify tab.

    Per-sample data lands in `measurements/`; this record carries the
    workspace-scope verdict (did the source-campaign composite hold).
    """

    model_config = ConfigDict(frozen=True)

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_label: str
    source_candidate_id: str
    config_hash: str
    samples_requested: int
    samples_added: int
    workspace_n: int
    workspace_accuracy: float
    workspace_composite: float
    source_campaign_accuracy: float
    source_campaign_composite: float
    source_campaign_n: int


class RoundSummaryCandidate(BaseModel):
    """Display-summary row for `dashboard.json::rounds[].candidates` — chart/lineage/sparkline subset of `ScoredCandidate`."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    accuracy: float
    composite_fitness: float
    scored_samples: int
    expected_samples: int
    is_winner: bool
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""
    partial_reason: str = ""  # "" | "pobb" | "skip" — see ScoredCandidate.partial_reason
    # Difficulty-adjusted Rasch ability + SE (`ScoredCandidate.theta`) — the metric the winner
    # was elected on, so the chart can explain a lower-accuracy winner. `None` outside the fit.
    theta: float | None = None
    theta_se: float | None = None


class WarningDict(TypedDict):
    """One backend diagnostics warning as it rides ``round_file::results[].pipeline_data.diagnostics.warnings``.

    ``kind`` is **source-stamped by the backend** (TermNorm's ``WarningKind`` enum —
    ``structural`` = config/schema fault the operator must fix, ``transient`` =
    recoverable noise). PromptPotter reads it directly and keeps NO shadow code→kind
    taxonomy: an absent or unrecognized ``kind`` is SKIPPED, never guessed. That
    inverts the old failure mode safely — a backend site that forgets to stamp
    under-counts (and surfaces in the live smoke), instead of silently grading a
    transient blip as an abort-worthy ``structural`` break."""

    step: str
    code: str
    message: str
    kind: str
    details: NotRequired[list[Any]]
    stats: NotRequired[dict[str, Any]]


HealthGrade = Literal["healthy", "degraded", "critical"]


class DegradationHealth(BaseModel):
    """Context-aware degradation verdict for a round (origin included), computed
    PP-side at round close (``domain/results_health.py``) from the backend's
    per-sample warning stamps — the single graded signal every surface renders (R-36).

    ``grade`` distinguishes a structurally-broken pipeline (``critical``,
    abort-worthy) from transient backend noise (``degraded``, keep going) from a
    sound round (``healthy``). The SAME degradation grades differently by track
    record: structural failures at an untested config (``prior_clean_rounds == 0``)
    are abort-suspect, while the same isolated failure deep in a proven campaign is
    noise. The verdict NEVER stops the run — ``suggested_action`` (set only at
    ``critical``) is an operator-facing recommendation, surfaced read-only."""

    model_config = ConfigDict(frozen=True)

    grade: HealthGrade
    reasons: list[str] = Field(default_factory=list)
    samples: int
    structural_count: int
    transient_count: int
    # Samples whose pipeline SUCCEEDED but emitted no extractable prediction
    # (empty terminal ranking → ``NO_RESULT``). PP-owned — the backend stamps no
    # warning, since from its side generation succeeded. A high share is a
    # structurally-unscoreable floor (answer-format / extraction mismatch),
    # distinct from a wrong-but-extractable miss. Drives the ``unscoreable`` grade.
    no_result_count: int = 0
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    node_failure_rates: dict[str, float] = Field(default_factory=dict)
    # Verbatim upstream reasons per node ("[code] message"), harvested from the
    # connector's StepWarnings — the evidence behind the verdict, connector-agnostic.
    node_warnings: dict[str, list[str]] = Field(default_factory=dict)
    ci_lo: float
    ci_hi: float
    suggested_action: str | None = None


class RoundSummary(BaseModel):
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.

    Top-level `accuracy`/`composite_fitness` mirror the winner so trend/sparkline
    don't scan `candidates`. In-flight round rides `current_round`.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Per-round selection from the adaptive queue mechanism — sample ids
    # in measurement order (longest candidate sequence carries the full
    # series since PoBB truncates losers, not the queue mechanism itself).
    selection: list[int] = Field(default_factory=list)
    # PP-computed (round-close) degradation verdict for this round (origin included).
    # ``None`` only when the round measured zero samples. Webapp/CLI render it;
    # never recompute (R-36).
    health: DegradationHealth | None = None


class PayloadOutcome(BaseModel):
    """Per-payload row inside a ``SweepBatchResult``."""

    source_file: str
    status: str  # completed | interrupted | skipped | skipped_already_forked
    cycle_id: str


class SweepBatchResult(BaseModel):
    """Final outcome of a sweep batch — all forks attempted, persistence finalized."""

    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    fork_cycle_ids: list[str]
    payload_outcomes: list[PayloadOutcome]
    interrupted: bool
