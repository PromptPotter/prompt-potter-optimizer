"""Frozen view-model dataclasses — the unified type set for all render targets.

Two factories build these (``view_factories.py``): ``from_phase_event`` for
live ``PhaseEvent`` payloads and ``from_disk`` for post-hoc trial / index
artifacts. Three render targets consume them (``render_text.py``,
``render_markdown.py``, ``render_html.py``). Views are pure data: no methods
that emit text, no I/O.

The named correctness invariant — exercised in ``tests/test_view_factories``
— is ``from_phase_event(e) == from_disk(write_then_load(e))`` on
``RoundCompleteView``, the one phase event that survives to disk in trial
JSON. Live-only events (refine, probe, plan, escalation) have only the
phase-event factory and no disk counterpart.
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
    "WarningEntry",
]


@dataclass(frozen=True)
class WarningEntry:
    title: str
    detail: str


@dataclass(frozen=True)
class InitEnterView:
    """Pre-baseline init banner — only warnings render to text."""

    warnings: tuple[WarningEntry, ...] = ()
    max_rounds: int = 0
    patience: int = 0
    n_variants: int = 0
    sp_budget_ttest: int = 0
    dataset_size: int = 0
    mde: float = 0.0
    model: str = ""
    l2_enabled: bool = False
    l3_enabled: bool = False


@dataclass(frozen=True)
class InitExitView:
    """Post-baseline init exit — baseline accuracy + cycle identity."""

    baseline_acc: float
    cycle_id_short: str
    samples: int
    obs_on: bool
    bootstrap_critique: str = ""
    resumed_from_round: int = 0
    l1_critique_chars: int = 0
    task_context_keys: int = 0
    l2_round: int = 0
    prompt_field_overlays: dict[str, str] = field(default_factory=dict)
    composite_formula: str | None = None
    composite_formula_short: str | None = None
    baseline_composite: float = 0.0


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
    creativity: float
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
    n_scoring_queries: int
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
    composite: float | None
    hits: int
    total: int
    ci_lo: float
    ci_hi: float
    escalation_aborted: bool = False


@dataclass(frozen=True)
class RoundCompleteView:
    """L1 score exit — full round summary; the ``from_disk`` mirror reads
    the same shape from ``trial_NNNN.json``. The named round-trip invariant
    target."""

    round: int
    baseline_acc: float
    scores: tuple[ScoreEntry, ...]
    winner_label: str
    winner_accuracy: float
    winner_composite: float | None
    winner_evaluators: dict[str, float]
    winner_hits: int
    winner_total: int
    improved: bool
    delta: float
    p_value: float | None
    next_action: str
    l1_critique_text: str
    composite_formula: str | None
    composite_formula_short: str | None
    baseline_composite: float | None


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
    current_params: dict[str, str]
    n_params: int


@dataclass(frozen=True)
class L2RefineExitView:
    param_changes_count: int
    task_context_changed: bool
    action: str
    changes_description: str
    warned_queries: int
    top_warning: str
    l2_prompt: str
    l2_response_json: Any | None


@dataclass(frozen=True)
class ProbeEnterView:
    n_probe_queries: int
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
    baseline_accuracy: float
    best_accuracy: float
    best_round: int | None
    rounds_completed: int
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class RoundDigestView:
    """Per-round entry in log.md (derived from trial JSON)."""

    round: int
    label: str
    accuracy: float
    improved: bool
    hits: int
    total: int
    composite: float
    changes_description: str
    l2_directive: str
    l1_critique_text: str
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    candidates_scored: int
    evaluators: dict[str, float]
    # Optional: per-candidate P(best) trajectory for this round, parsed
    # from ``streams/round_NNNN_p_best.jsonl``. Outer dict is candidate_id;
    # inner list is the sequence of P(best) values across queries. Empty
    # when the stream isn't available (resumed cycles, pre-PoBB rounds).
    p_best_trajectory: dict[str, list[float]] = field(default_factory=dict)
    p_best_stopped: dict[str, int] = field(default_factory=dict)


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
    """One row of the family-root log.md's ``## Forks`` section.

    Built from each fork's ``index.json``; the family root is the only
    cycle that renders these (forks themselves get an empty tuple)."""

    cycle_id: str
    mode: str
    status: str
    best_accuracy: float
    baseline_accuracy: float
    n_rounds: int
    stop_reason: str
    finished_at: str | None


@dataclass(frozen=True)
class LogMdView:
    """Composite — the full log.md document is one of these."""

    status: DigestStatusView
    rounds: tuple[RoundDigestView, ...]
    formula: str | None
    baseline_composite: float | None
    hard_samples: HardSamplesView | None
    final: FinalWinnerView | None
    forks: tuple[ForkSummaryView, ...] = ()
    family_best: tuple[float, str] | None = None


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
)
