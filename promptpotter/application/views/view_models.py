"""Frozen view-model dataclasses — one type set across render targets. Pure data, no I/O. ``RoundCompleteView`` is the
one event that lands on disk; the live-only events have no disk counterpart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.results import HardSampleOrder, OverlapReading
from promptpotter.domain.ruler import AbilityReading

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
    """Mutable per-cycle scratch threaded through ``from_phase_event``, serialized to the wire so ledger subscribers re-sync
    their own copy. Distinct from the frozen ``*View`` payloads: this carries running state the builders mutate."""

    max_rounds: int = 0
    patience: int = 0
    round_num: int = 0
    l1_stall_count: int = 0
    # Banked lives ("hearts") entering the current round; ``None`` when lives mode is off.
    hearts: int | None = None
    # The bank's ceiling — the denominator every ♥ readout renders against. A bare count
    # is scaleless, and in lives mode there is no ``max_rounds`` to fall back on.
    hearts_cap: int | None = None
    parent_accuracy: float = 0.0
    parent_composite_fitness: float | None = None
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None

    def ledger_anchors(self) -> dict[str, Any]:
        """The four scalars a ledger subscriber re-syncs from (``LiveDisplay._phase_ctx``). Not
        ``asdict``: that re-emitted both whole ``*_sp_flat`` prompts per candidate and per round."""
        return {
            "parent_accuracy": self.parent_accuracy,
            "parent_composite_fitness": self.parent_composite_fitness,
            "composite_fitness_formula": self.composite_fitness_formula,
            "composite_fitness_formula_short": self.composite_fitness_formula_short,
        }


@dataclass(frozen=True)
class WarningEntry:
    title: str
    detail: str


@dataclass(frozen=True)
class InitEnterView:
    """Pre-origin init banner. ``composite_fitness_formula`` rides here so the dashboard stamps it BEFORE origin scoring;
    otherwise the mask editor has no formula reference during the origin."""

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
    """Post-origin init exit. ``resumed_from_round`` is the NEXT L1 round; ``cached_rounds_count`` is a literal count of
    round artifacts, kept independent so "Resumed from N (M cached)" can be truthful."""

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
    hearts: int | None = None
    hearts_cap: int | None = None


@dataclass(frozen=True)
class SpDiffView:
    columns: tuple[tuple[str, dict[str, str]], ...]
    node_param_keys: dict[str, list[str]] | None
    round_num: int | None
    clone_labels: tuple[str, ...]
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    l1_n_repeat: int


@dataclass(frozen=True)
class CandidatesGeneratedView:
    """L1 generate exit — N candidates ready, sp_diff table follows."""

    n_candidates: int
    source: str  # "disk" | "llm"
    n_scoring_samples: int
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    l1_n_repeat: int
    clone_labels: tuple[str, ...]
    sp_diff: SpDiffView


@dataclass(frozen=True)
class ScoreEntry:
    label: str
    accuracy: float
    composite_fitness: float | None
    total: int
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    escalation_aborted: bool = False
    # Why this row scored fewer cells than the panel: "" (whole panel) | "skip" (operator cut the
    # walk short). Carried because the display RANKS on it (`domain/rendering.py::display_rank_key`)
    # and a view cannot demote what it was never told.
    partial_reason: str = ""
    # First-validation-failure reason for synthetic-zeroed variants (e.g. ``no_op_variant``);
    # scoreboard suppresses these rows so ranking reflects mutated candidates only.
    invalid_reason: str | None = None
    # The origin as this row's comparison floor. ``None`` unless the row covered the origin's
    # whole panel — a prefix rate is decided by where PoBB stopped the candidate, not by its
    # answers (`scoring/metrics.py::matched_parent_stats`) — which is NOT the same as 0.0.
    matched_parent_accuracy: float | None = None
    matched_parent_composite: float | None = None
    # What this row was RANKED on: ``None`` outside the election fit, and for every row while the
    # ruler is cold. A table printing accuracy alone can seat a winner it has no column able to
    # explain. The blocked LIFT and its interval are deliberately not here — the terminal's Δ
    # column is accuracy-space and a second, fitness-space margin beside it would read as the
    # same number twice; the interval's surfaces are ``ScoreboardRow`` and ``DashboardCandidate``.
    theta: float | None = None
    theta_se: float | None = None


@dataclass(frozen=True)
class RoundCompleteView:
    """L1 score exit — round summary. Round-trip invariant target."""

    round: int
    parent_acc: float
    scores: tuple[ScoreEntry, ...]
    winner_label: str
    winner_accuracy: float
    winner_composite_fitness: float | None
    winner_evaluators: dict[str, float]
    winner_total: int
    improved: bool
    # ``None`` alongside ``matched_parent_accuracy`` — there is no Δ without a floor.
    delta: float | None
    p_value: float | None
    # The round's outcome in the numbers that decided it — see ``RoundResult.verdict_reason``.
    # Present on a won round as well as a held one, which is what lets the terminal print a
    # verdict either way instead of falling silent exactly when nothing was resolved.
    verdict_reason: str | None
    next_action: str
    l1_critique_text: str
    composite_fitness_formula: str | None
    composite_fitness_formula_short: str | None
    # The parent restricted to the winner's measured samples; verdict line + Δ read these so
    # operator-facing "Δ vs parent" matches the ``l1_score`` gate. ``None`` when the winner
    # did not cover the parent's panel — the verdict then states the winner's own rate and
    # drops the "(was …)" clause rather than quoting the full-set parent, which is a
    # different sample basis and would read as lift the winner never earned.
    # No default: the one builder resolves it, and a ``0.0`` sitting here would render
    # "was 0.0%" on any round whose payload lacked the key.
    matched_parent_accuracy: float | None
    matched_parent_composite: float | None = None


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
    # The two surfaces L2 still writes (`escalation/firing.py::_l2_exit`), plus its prose.
    # Three fields sat here that `_l2_exit` had stopped emitting, so each rendered its
    # default forever: `task_context_changed` (the framing is frozen — L2 has no
    # task_context field), `action` (probe rounds are not wired — no `action` on
    # `L2ContextOutput`), and `warned_samples` (the warned-query inventory was deleted).
    # Meanwhile `l1_layout_changed` and `axis_targeted` WERE emitted and shown nowhere —
    # so the operator's L2 line reported a param count and stayed silent about the
    # attention edit, which is the move L2 is for.
    param_changes_count: int
    l1_layout_changed: bool
    axis_targeted: str
    changes_description: str
    # Post-fire L2 counters — the four scalars ``EscalationFSM.fold`` rebuilds resume state
    # from. They ride the VIEW because the view is the persisted half of the record: they used
    # to travel in ``PhaseEvent.data``, which is in-memory-only, so every resume silently
    # rebuilt L2 as never-fired. A resume-critical fact is a declared field, not a loose key.
    l2_round: int
    l2_stall_count: int
    l2_best_composite_fitness_at_entry: float
    l2_best_theta_at_entry: float | None
    # `l2_prompt` / `l2_response_json` are NOT here. The rendered call is already on this
    # ledger as the `l2_context` LLMCallRecord and assembled human-readably in the audit twin
    # (`.runtime/cache/rounds/round_NNNN.json::nodes.l2_context`, uncapped) — one prompt, three
    # copies, two of them in this file. The terminal readout addresses the twin.


@dataclass(frozen=True)
class PlanEnterView:
    l3_round: Any
    l2_stall_count: Any
    current_plan_preview: str


@dataclass(frozen=True)
class PlanExitView:
    new_plan_preview: str
    changes_description: str
    # Post-fire L3 counters — same contract as ``L2RefineExitView``'s four above.
    l3_round: int
    l3_stall_count: int
    l3_best_composite_fitness_at_entry: float
    l3_best_theta_at_entry: float | None


# --- Aggregate views for log.md (post-hoc, disk-derived only) -------------


@dataclass(frozen=True)
class DigestStatusView:
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
    round: int
    label: str
    accuracy: float
    improved: bool
    total: int
    composite_fitness: float
    changes_description: str
    l1_critique_text: str
    l1_yield: float
    l1_n_no_op: int
    l1_n_duplicate: int
    l1_n_repeat: int
    candidates_scored: int
    evaluators: dict[str, float]
    # THIS round's own comparison floor — the parent re-scored on the samples this round drew.
    # `log.md` compared against the whole-cycle origin composite instead, so under
    # `per_round_resubset` it read draw difficulty as candidate lift and printed a different Δ
    # from the terminal for the same round. ``None`` where the round matched nothing, and there
    # is no fallback to the cycle origin: that is a different sample basis, not a default.
    matched_parent_composite: float | None = None
    # The subset-invariant series and the scale it was read on, so a reader can see a round
    # scored mostly off that scale. Mirrors ``RoundResult``.
    ability: AbilityReading | None = None
    # The round's outcome in the numbers that decided it — see ``RoundResult.verdict_reason``.
    verdict_reason: str | None = None
    # The adopted line read on ONE shared set of cells — the only row in this view two rounds can
    # be differenced on, since `accuracy` above is read on whatever subset the round bought.
    overlap: OverlapReading | None = None
    # Per-candidate P(best) trajectory from ``.runtime/streams/round_NNNN_p_best.jsonl``;
    # empty for resumed / pre-PoBB rounds.
    p_best_trajectory: dict[str, list[float]] = field(default_factory=dict)


@dataclass(frozen=True)
class HardSamplesView:
    """Hard-sample-sorter heatmap artifact (passed through verbatim)."""

    artifact: dict[str, Any]
    sample_query_lookup: dict[int, str] = field(default_factory=dict)
    order: HardSampleOrder = "info_gain"
    """`CampaignConfig.hard_sample_order` — ranks the leaderboard block, not the matrix."""


@dataclass(frozen=True)
class FinalWinnerView:
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
    status: DigestStatusView
    rounds: tuple[RoundDigestView, ...]
    formula: str | None
    hard_samples: HardSamplesView | None
    final: FinalWinnerView | None
    forks: tuple[ForkSummaryView, ...] = ()
    family_best: tuple[float, str] | None = None


@dataclass(frozen=True)
class SweepPayloadRow:
    source_file: str
    status: str
    cycle_id: str


@dataclass(frozen=True)
class SweepSummaryView:
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
    | PlanEnterView
    | PlanExitView
    | LogMdView
    | FinalWinnerView
    | ForkSummaryView
    | SweepSummaryView
)
