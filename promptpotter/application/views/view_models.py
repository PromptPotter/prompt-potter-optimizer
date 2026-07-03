"""Frozen view-model dataclasses — unified type set across render targets.

Producers: ``application.views.ingress`` (live PhaseEvents) + ``application.output.writers``
(post-hoc disk round_data). Consumers: ``render`` (``to_text``/``to_markdown``).
Pure data — no I/O, no methods that emit text.

Round-trip invariant (no standing test — a broken round-trip surfaces as a
wrong/empty dashboard; see ``tests/CLAUDE.md``):
``from_phase_event(e) == from_disk_round(write_then_load(e))`` on
``RoundCompleteView`` — the one event that lands on disk. Live-only events
(refine/probe/plan/escalation) have no disk counterpart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AnyView",
    "CandidatesGeneratedView",
    "DigestStatusView",
    "EscalationEnterView",
    "EscalationExitView",
    "FinalWinnerView",
    "ForkSummaryView",
    "HardSamplesView",
    "InitEnterView",
    "InitExitView",
    "L2RefineEnterView",
    "L2RefineExitView",
    "LogMdView",
    "PlanEnterView",
    "PlanExitView",
    "ProbeEnterView",
    "ProbeExitView",
    "RoundCompleteView",
    "RoundDigestView",
    "RoundStartView",
    "ScoreEntry",
    "SpDiffView",
    "SweepPayloadRow",
    "SweepSummaryView",
    "ViewContext",
    "WarningEntry",
]


@dataclass
class ViewContext:
    """Mutable per-cycle scratch state threaded through ``from_phase_event``.

    Owned by ``RunCallbacks._phase_ctx`` and serialized to the wire via
    ``asdict`` on round-complete + candidate-scored emits so ledger subscribers
    can re-sync their own dict-shaped copy. Distinct from the frozen ``*View``
    dataclasses: those are immutable per-event payloads; this carries running
    state the builders mutate as phase events flow in.
    """

    max_rounds: int = 0
    patience: int = 0
    round_num: int = 0
    l1_stall_count: int = 0
    origin_accuracy: float = 0.0
    origin_composite_fitness: float | None = None
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class WarningEntry:
    title: str
    detail: str


@dataclass(frozen=True)
class InitEnterView:
    """Pre-origin init banner — only warnings render to text.

    ``composite_fitness_formula`` propagated here so ``LiveDashboardView``
    stamps it on ``dashboard.json`` before origin scoring; otherwise the
    What-If panel has no formula reference during origin.
    """

    warnings: tuple[WarningEntry, ...] = ()
    max_rounds: int = 0
    patience: int = 0
    n_variants: int = 0
    sp_budget_ttest: int = 0
    dataset_size: int = 0
    model: str = ""
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


@dataclass(frozen=True)
class InitExitView:
    """Post-origin init exit — origin accuracy + cycle identity.

    ``resumed_from_round`` = next L1 round (1 fresh, N+1 after replaying N).
    ``cached_rounds_count`` = literal count of round artifacts on disk;
    kept independent so the renderer can show "Resumed from N (M cached)" truthfully.
    """

    origin_acc: float
    cycle_id_short: str
    samples: int
    obs_on: bool
    # Count of origin per-sample measurements — the live dashboard's
    # ``origin.samples`` field. Carried on the view so the dashboard projection
    # reads it here, not off the live ``Cycle`` (a runtime-only object stripped
    # before the record is persisted/streamed).
    origin_samples: int = 0
    resumed_from_round: int = 1
    cached_rounds_count: int = 0
    task_context_keys: int = 0
    l2_round: int = 0
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    origin_composite_fitness: float = 0.0


@dataclass(frozen=True)
class RoundStartView:
    """L1 generate enter — round banner + generate config block."""

    round: int
    max_rounds: int
    l1_stall_count: int
    patience: int
    current_acc: float
    prompt_preview: str
    n_variants: int
    model: str
    has_l1_critique: bool


@dataclass(frozen=True)
class SpDiffView:
    columns: tuple[tuple[str, dict[str, str]], ...]
    node_param_keys: dict[str, list[str]] | None
    round_num: int | None
    clone_labels: tuple[str, ...]
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int


@dataclass(frozen=True)
class CandidatesGeneratedView:
    """L1 generate exit — N candidates ready, sp_diff table follows."""

    n_candidates: int
    source: str  # "disk" | "llm"
    n_scoring_samples: int
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    clone_labels: tuple[str, ...]
    sp_diff: SpDiffView


@dataclass(frozen=True)
class ScoreEntry:
    """One row in the round-end scoreboard."""

    label: str
    accuracy: float
    composite_fitness: float | None
    hits: int
    total: int
    ci_lo: float
    ci_hi: float
    escalation_aborted: bool = False
    # First-validation-failure reason for synthetic-zeroed variants (e.g. ``no_op_variant``);
    # scoreboard suppresses these rows so ranking reflects mutated candidates only.
    invalid_reason: str | None = None
    # Origin restricted to this row's measured samples — apples-to-apples Δ
    # when PoBB leader-locked early; equals full-set origin when fully scored.
    matched_origin_accuracy: float = 0.0
    matched_origin_composite: float | None = None


@dataclass(frozen=True)
class RoundCompleteView:
    """L1 score exit — round summary. Round-trip invariant target."""

    round: int
    origin_acc: float
    scores: tuple[ScoreEntry, ...]
    winner_label: str
    winner_accuracy: float
    winner_composite_fitness: float | None
    winner_evaluators: dict[str, float]
    winner_hits: int
    winner_total: int
    improved: bool
    delta: float
    p_value: float | None
    improved_reason: str | None
    next_action: str
    l1_critique_text: str
    composite_fitness_formula: str | None
    composite_fitness_formula_short: str | None
    origin_composite_fitness: float | None
    # Origin restricted to the winner's measured samples; verdict line + Δ
    # read these so operator-facing "Δ vs origin" matches the ``l1_score`` gate.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float | None = None


@dataclass(frozen=True)
class EscalationEnterView:
    check_name: str
    target: str
    degraded_rate: float
    warning_types: dict[str, int]


@dataclass(frozen=True)
class EscalationExitView:
    classifications: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class L2RefineEnterView:
    l2_round: Any
    l1_stall_count: Any
    current_acc: float
    best_acc: float
    l1_overrides: dict[str, str]


@dataclass(frozen=True)
class L2RefineExitView:
    param_changes_count: int
    task_context_changed: bool
    action: str
    changes_description: str
    warned_samples: int
    l2_prompt: str
    l2_response_json: Any | None


@dataclass(frozen=True)
class ProbeEnterView:
    n_probe_samples: int
    probe_queries: tuple[str, ...]


@dataclass(frozen=True)
class ProbeExitView:
    n_probed: int
    probe_hits: int


@dataclass(frozen=True)
class PlanEnterView:
    l3_round: Any
    l2_stall_count: Any
    current_plan_preview: str


@dataclass(frozen=True)
class PlanExitView:
    new_plan_preview: str
    changes_description: str


# --- Aggregate views for log.md (post-hoc, disk-derived only) -------------


@dataclass(frozen=True)
class DigestStatusView:
    """Top-of-log.md status block."""

    campaign_id: str
    parent_session_id: str | None
    status: str
    stop_reason: str
    origin_accuracy: float
    best_accuracy: float
    best_round: int | None
    rounds_completed: int
    started_at: str | None
    finished_at: str | None
    # ``generation_only`` rounds (diag preview / sweep no-score follow-up) —
    # counted into ``rounds_completed`` but rendered separately.
    gen_only_rounds: int = 0


@dataclass(frozen=True)
class RoundDigestView:
    """Per-round entry in log.md (derived from round_data JSON)."""

    round: int
    label: str
    accuracy: float
    improved: bool
    hits: int
    total: int
    composite_fitness: float
    changes_description: str
    l1_critique_text: str
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    candidates_scored: int
    evaluators: dict[str, float]
    # Per-candidate P(best) trajectory from ``.runtime/streams/round_NNNN_p_best.jsonl``;
    # empty for resumed / pre-PoBB rounds.
    p_best_trajectory: dict[str, list[float]] = field(default_factory=dict)


@dataclass(frozen=True)
class HardSamplesView:
    """Hard-sample-sorter heatmap artifact (passed through verbatim)."""

    artifact: dict[str, Any]
    sample_query_lookup: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalWinnerView:
    """Final winner block at the end of log.md."""

    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any]


@dataclass(frozen=True)
class ForkSummaryView:
    """One row of the family-root log.md ``## Forks`` section; forks themselves render an empty tuple."""

    cycle_id: str
    mode: str
    status: str
    best_accuracy: float
    origin_accuracy: float
    n_rounds: int
    stop_reason: str
    finished_at: str | None


@dataclass(frozen=True)
class LogMdView:
    """Composite — the full log.md document is one of these."""

    status: DigestStatusView
    rounds: tuple[RoundDigestView, ...]
    formula: str | None
    origin_composite_fitness: float | None
    hard_samples: HardSamplesView | None
    final: FinalWinnerView | None
    forks: tuple[ForkSummaryView, ...] = ()
    family_best: tuple[float, str] | None = None


@dataclass(frozen=True)
class SweepPayloadRow:
    """One row in a sweep batch's ``summary.md`` payload table."""

    source_file: str
    status: str
    cycle_id: str


@dataclass(frozen=True)
class SweepSummaryView:
    """``summary.md`` for a sweep batch dir — header + one row per payload."""

    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    n_minted: int
    n_payloads: int
    payloads: tuple[SweepPayloadRow, ...]


AnyView = (
    InitEnterView
    | InitExitView
    | RoundStartView
    | CandidatesGeneratedView
    | RoundCompleteView
    | EscalationEnterView
    | EscalationExitView
    | L2RefineEnterView
    | L2RefineExitView
    | ProbeEnterView
    | ProbeExitView
    | PlanEnterView
    | PlanExitView
    | LogMdView
    | FinalWinnerView
    | ForkSummaryView
    | SweepSummaryView
)
