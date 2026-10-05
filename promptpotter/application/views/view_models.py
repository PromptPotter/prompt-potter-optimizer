"""Frozen view-model dataclasses — one type set across render targets. Pure data, no I/O. ``RoundCompleteView`` is the
one event that lands on disk; the live-only events have no disk counterpart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.dashboard_rows import RunStanding
from promptpotter.domain.results import (
    ArmOutcome,
    DisplayMetric,
    HardSampleOrder,
    OptimizerFact,
    OverlapReading,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.spend import SpendRollup

__all__ = [
    "AnyView",
    "BenchGradedView",
    "BenchScoredView",
    "CandidatesGeneratedView",
    "DigestStatusView",
    "FinalWinnerView",
    "ForkSummaryView",
    "HardSamplesView",
    "InitEnterView",
    "InitExitView",
    "LogMdView",
    "MeasureEnterView",
    "OptimizerStepEnterView",
    "OptimizerStepExitView",
    "RoundCompleteView",
    "RoundDigestView",
    "RoundStartView",
    "ScoreEntry",
    "SpDiffView",
    "ViewContext",
    "WarningEntry",
]


@dataclass
class ViewContext:
    """Mutable per-cycle scratch threaded through ``from_phase_event``, serialized to the wire so ledger subscribers re-sync
    their own copy. Distinct from the frozen ``*View`` payloads: this carries running state the builders mutate."""

    max_rounds: int = 0
    patience: int | None = None
    round_num: int = 0
    # The optimizer's standing entering the current round; its cap is the denominator every ♥
    # readout renders against, since a run banking stalls may have no ``max_rounds``.
    run_standing: RunStanding | None = None
    parent_accuracy: float = 0.0
    parent_composite_fitness: float | None = None
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    display_metric: DisplayMetric = "accuracy"
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None

    def ledger_anchors(self) -> dict[str, Any]:
        """The five scalars a ledger subscriber re-syncs from (``ReadoutProjection._phase_ctx``). Not
        ``asdict``: that re-emitted both whole ``*_sp_flat`` prompts per candidate and per round."""
        return {
            "parent_accuracy": self.parent_accuracy,
            "parent_composite_fitness": self.parent_composite_fitness,
            "composite_fitness_formula": self.composite_fitness_formula,
            "composite_fitness_formula_short": self.composite_fitness_formula_short,
            "display_metric": self.display_metric,
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
    # The optimizer's own (`OptimizerPacing.patience`); ``None`` where it keeps none.
    patience: int | None = None
    sp_budget_round: int = 0
    dataset_size: int = 0
    model: str = ""
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


@dataclass(frozen=True)
class InitExitView:
    """Post-origin init exit. ``resumed_from_round`` is the NEXT L1 round; ``cached_rounds_count`` is a literal count of
    round artifacts, kept independent so "Resumed from N (M cached)" can be truthful."""

    # ``None`` where the origin graded no cell on accuracy — the same absence as
    # ``RoundCompleteView.winner_accuracy`` below, reached first by an L4 outer cycle, whose
    # measurand is ``mean_round_delta``. The builder reads it off a ``cycle`` typed ``Any``, so a
    # declaration narrower than the ``RoundResult`` feeding it is invisible to the checker and
    # surfaces as a formatted ``None``.
    origin_acc: float | None
    cycle_id_short: str
    samples: int
    bench_samples: int
    obs_on: bool
    # Count of origin per-sample measurements — the live dashboard's
    # ``origin.samples`` field. Carried on the view so the dashboard projection
    # reads it here, not off the live ``Cycle`` (a runtime-only object stripped
    # before the record is persisted/streamed).
    origin_samples: int = 0
    resumed_from_round: int = 1
    cached_rounds_count: int = 0
    task_context_keys: int = 0
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None


@dataclass(frozen=True)
class RoundStartView:
    """``propose:enter`` — the round banner and the proposing block, whichever optimizer proposes;
    ``standing`` and ``note`` are the optimizer's own words (``RoundOpening``)."""

    node: str
    round: int
    max_rounds: int
    standing: str
    current_acc: float
    prompt_preview: str
    arms: int | None
    note: str
    model: str
    run_standing: RunStanding | None = None


@dataclass(frozen=True)
class SpDiffView:
    columns: tuple[tuple[str, dict[str, str]], ...]
    node_param_keys: dict[str, list[str]] | None
    round_num: int | None
    clone_labels: tuple[str, ...]
    # Proposals each ``INVARIANT_REASONS`` member collapsed, and who proposed them.
    collapses: dict[str, int]
    proposer: str


@dataclass(frozen=True)
class CandidatesGeneratedView:
    """``propose:exit`` — N candidates ready, sp_diff table follows."""

    n_candidates: int
    source: str  # "disk" | "llm"
    n_scoring_samples: int
    clone_labels: tuple[str, ...]
    sp_diff: SpDiffView


@dataclass(frozen=True)
class MeasureEnterView:
    node: str
    n_candidates: int
    n_samples: int


@dataclass(frozen=True)
class BenchScoredView:
    # `BenchScore.model_dump(mode="json")` — the dashboard folds `bench_score` from it.
    bench: dict[str, Any]


@dataclass(frozen=True)
class BenchGradedView:
    # One pass's `BenchReading.model_dump(mode="json")`, or ``None`` with `missing` saying why —
    # the dashboard folds it onto the round it names.
    reading: dict[str, Any] | None
    missing: str | None


@dataclass(frozen=True)
class ScoreEntry:
    label: str
    accuracy: float | None
    composite_fitness: float | None
    total: int
    # The row's BASIS: cells this arm answered of the cells the round asked of it. An arm stopped
    # early reports a rate over a prefix, and a board without this prints it beside full-panel
    # rates as if the two were one measurement.
    scored: int
    expected: int
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    # Carried because the display RANKS on it (`domain/results.py::scoreboard_rank_key`) and a
    # view cannot demote what it was never told.
    outcome: ArmOutcome
    # First-validation-failure reason for synthetic-zeroed variants (e.g. ``no_op_variant``);
    # scoreboard suppresses these rows so ranking reflects mutated candidates only.
    invalid_reason: str | None = None
    # The origin as this row's comparison floor. ``None`` unless the row covered the origin's
    # whole panel — a prefix rate is decided by where PoBB stopped the candidate, not by its
    # answers (`scoring/metrics.py::matched_parent_stats`) — which is NOT the same as 0.0.
    reference_accuracy: float | None = None
    reference_composite: float | None = None
    # What this row was RANKED on: ``None`` outside the election fit, and for every row while the
    # ruler is cold. A table printing accuracy alone can seat a winner it has no column able to
    # explain. The blocked LIFT and its interval are deliberately not here — the terminal's Δ
    # column is accuracy-space and a second, fitness-space margin beside it would read as the
    # same number twice; the interval's surfaces are ``ScoreboardRow`` and ``DashboardCandidate``.
    theta: float | None = None
    theta_se: float | None = None


@dataclass(frozen=True)
class RoundCompleteView:
    """``select:exit`` — the round's verdict. The readout HOLDS it until the round closes: the
    panel gate can still unwind the round, and the overlap line it prints beside is not measured
    yet. Round-trip invariant target."""

    round: int
    parent_acc: float
    scores: tuple[ScoreEntry, ...]
    # The selected arm's candidate label; ``""`` on a round that held its best-so-far.
    winner_label: str
    # Mirrors `RoundResult.stamps_theta`: the scoreboard's ability column.
    stamps_theta: bool
    # ``None`` where the round graded no cell on accuracy — every arm errored, or the backend is
    # verifier-graded — as ``RoundResult.accuracy`` is.
    winner_accuracy: float | None
    winner_composite_fitness: float | None
    winner_evaluators: dict[str, float]
    winner_total: int
    improved: bool
    # ``None`` alongside ``reference_accuracy`` — there is no Δ without a floor.
    delta: float | None
    p_value: float | None
    # The round's outcome in the numbers that decided it — see ``RoundResult.verdict_reason``.
    # Present on a won round as well as a held one, which is what lets the terminal print a
    # verdict either way instead of falling silent exactly when nothing was resolved.
    verdict_reason: str | None
    composite_fitness_formula: str | None
    composite_fitness_formula_short: str | None
    # The selected arm's reference restricted to its measured samples; the verdict line + Δ read
    # these. ``None`` when the arm did not cover its reference's panel — the verdict then drops
    # the reference rate rather than quoting a full-set one, a different sample basis that would
    # read as lift the arm never earned. No default: a ``0.0`` here would render as a real rate.
    reference_accuracy: float | None
    reference_composite: float | None = None
    # WHICH number leads the verdict line. Carried rather than read from config at render
    # time: a knob resolved in the renderer is one the disk round-trip cannot reproduce.
    display_metric: DisplayMetric = "accuracy"
    # ``RoundResult.ability``'s θ. ``None`` while the ruler is cold, where the headline falls back
    # to accuracy — a cold θ is logit-accuracy on the arm's own subset, so headlining it dresses a
    # subset-relative number as the difficulty-adjusted one.
    ability_theta: float | None = None


@dataclass(frozen=True)
class OptimizerStepEnterView:
    """An optimizer's own phase opening (``OptimizerRuntime.phases``), in its own words."""

    node: str
    activity: str
    title: str
    tag: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class OptimizerStepExitView:
    """An optimizer's own phase closing. ``headline`` is empty where the step adopted nothing."""

    headline: str
    details: tuple[str, ...]
    # The pointer's label and the node whose call the audit twin holds — addressed, never reprinted.
    audit: tuple[str, str] | None
    # What the optimizer's own resume fold rebuilds from, the view being the record's persisted
    # half; ``None`` where the step adopted nothing and so advanced no state.
    state: dict[str, Any] | None


# --- Aggregate views for log.md (post-hoc, disk-derived only) -------------


@dataclass(frozen=True)
class DigestStatusView:
    campaign_id: str
    parent_session_id: str | None
    # The manifest the rounds were run under, off their own documents; ``None`` before round 0.
    optimizer: str | None
    status: str
    stop_reason: str
    # ``None`` where the cycle banked no round 0 — `origin_accuracy_of` reads it off the round
    # documents and there is no stored copy, so absent means never scored, not scored zero.
    origin_accuracy: float | None
    best_accuracy: float
    best_round: int | None
    rounds_completed: int
    started_at: str | None
    finished_at: str | None
    # ``generation_only`` rounds (the diag preview) —
    # counted into ``rounds_completed`` but rendered separately.
    gen_only_rounds: int = 0


@dataclass(frozen=True)
class RoundDigestView:
    round: int
    label: str
    accuracy: float | None
    improved: bool
    total: int
    composite_fitness: float
    changes_description: str
    facts: tuple[OptimizerFact, ...]
    # Mirrors `RoundResult.stamps_theta`.
    stamps_theta: bool
    evaluators: dict[str, float]
    # THIS round's own comparison floor — the parent re-scored on the samples this round drew, as
    # the terminal's Δ reads it. ``None`` where the round matched nothing, and there is no
    # fallback to the cycle origin: that is a different sample basis, not a default.
    reference_composite: float | None = None
    # The subset-invariant series and the scale it was read on, so a reader can see a round
    # scored mostly off that scale. Mirrors ``RoundResult``.
    ability: AbilityReading | None = None
    # The round's outcome in the numbers that decided it — see ``RoundResult.verdict_reason``.
    verdict_reason: str | None = None
    # The best-so-far line read on ONE shared set of cells — the only row two rounds can
    # be differenced on, since `accuracy` above is read on whatever subset the round bought.
    overlap: OverlapReading | None = None
    # Per-candidate P(best) trajectory from the round's racing stream
    # (``.runtime/streams/round_NNNN_{member}.jsonl``); empty for resumed rounds.
    p_best_trajectory: dict[str, list[float]] = field(default_factory=dict)
    # ``{candidate_id: label}`` for the trajectory's keys, so its rows carry the name every other
    # surface prints an arm under.
    candidate_labels: dict[str, str] = field(default_factory=dict)
    # Who the round ELECTED. The trajectory above is a STOPPING posterior and cannot answer it —
    # its argmax is regularly not the elected arm, and can name two of them or none.
    winner_id: str = ""
    # What THIS round cost, and how much of its input providers served off their own prefix cache.
    # Served per round by the projection (`dashboard.json::spend_by_round`) and read here, never
    # re-folded: the browser's cost strip and this line are the same number or one of them is
    # wrong. ``None`` for a cycle with no dashboard on disk (a foreign fork sibling) or a round
    # that billed nothing.
    spend: SpendRollup | None = None


@dataclass(frozen=True)
class HardSamplesView:
    """Hard-sample-sorter heatmap artifact (passed through verbatim)."""

    artifact: dict[str, Any]
    sample_query_lookup: dict[int, str] = field(default_factory=dict)
    order: HardSampleOrder = "info_gain"
    """`CampaignConfig.hard_sample_order` — ranks the leaderboard block, not the matrix."""


@dataclass(frozen=True)
class FinalWinnerView:
    result_prompt_fields: dict[str, Any]
    result_pipeline_params: dict[str, Any]


@dataclass(frozen=True)
class ForkSummaryView:
    """One row of the family-root log.md ``## Forks`` section; forks themselves render an empty tuple."""

    cycle_id: str
    mode: str
    status: str
    best_accuracy: float
    origin_accuracy: float | None
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


AnyView = (
    InitEnterView
    | InitExitView
    | RoundStartView
    | CandidatesGeneratedView
    | MeasureEnterView
    | BenchScoredView
    | BenchGradedView
    | RoundCompleteView
    | OptimizerStepEnterView
    | OptimizerStepExitView
    | LogMdView
    | FinalWinnerView
    | ForkSummaryView
)
