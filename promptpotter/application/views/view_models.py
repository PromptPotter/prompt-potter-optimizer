from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from promptpotter.domain.cells import HardSamples
from promptpotter.domain.phase_views import ViewAnchors
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import (
    DisplayMetric,
    HardSampleOrder,
    OptimizerFact,
    OverlapReading,
    RunStanding,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.spend import SpendRollup

__all__ = [
    "DigestStatusView",
    "FinalWinnerView",
    "ForkSummaryView",
    "HardSamplesView",
    "LogMdView",
    "RoundDigestView",
    "ViewContext",
]


@dataclass
class ViewContext:
    max_rounds: int = 0
    patience: int | None = None
    # Its cap is every ♥ readout's denominator: a run banking stalls may have no ``max_rounds``.
    run_standing: RunStanding | None = None
    composite_fitness_formula: str | None = None
    composite_fitness_formula_short: str | None = None
    display_metric: DisplayMetric = "accuracy"
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None

    def anchors(self) -> ViewAnchors:
        """Not the whole context: both ``*_sp_flat`` prompts would ride every candidate and round."""
        return ViewAnchors(
            composite_fitness_formula=self.composite_fitness_formula,
            composite_fitness_formula_short=self.composite_fitness_formula_short,
            display_metric=self.display_metric,
        )


@dataclass(frozen=True)
class DigestStatusView:
    campaign_id: str
    optimizer: str | None
    stop_reason: StopReason | None
    standing: RunStanding | None
    rounds_completed: int
    started_at: str | None
    finished_at: str | None
    # Counted into ``rounds_completed`` but rendered separately.
    gen_only_rounds: int = 0


@dataclass(frozen=True)
class RoundDigestView:
    round: int
    label: str
    accuracy: float | None
    improved: bool
    total: int
    composite_fitness: float | None
    changes_description: str
    facts: tuple[OptimizerFact, ...]
    evaluators: dict[str, float]
    overlap: OverlapReading
    # ``None`` where the round matched nothing: the cycle origin is a different basis, not a default.
    composite_floor: float | None = None
    ability: AbilityReading | None = None
    verdict_reason: str | None = None
    # Empty for resumed rounds.
    p_best_trajectory: dict[str, list[float]] = field(default_factory=dict)
    candidate_labels: dict[str, str] = field(default_factory=dict)
    # The trajectory is a STOPPING posterior: its argmax is regularly not the elected arm.
    winner_id: str = ""
    # Served per round by the projection, never re-folded here; ``None`` where nothing billed.
    spend: SpendRollup | None = None


@dataclass(frozen=True)
class HardSamplesView:
    artifact: HardSamples
    sample_query_lookup: dict[int, str]
    # It ranks the leaderboard block, not the matrix.
    order: HardSampleOrder
    ranked: tuple[int, ...]


@dataclass(frozen=True)
class FinalWinnerView:
    result_prompt_fields: dict[str, Any]
    result_pipeline_params: dict[str, Any]


@dataclass(frozen=True)
class ForkSummaryView:
    cycle_id: str
    # What the id's own separator says the cycle is (``layout.py::sibling_kind``).
    kind: Literal["root", "fork", "diag"]
    standing: RunStanding | None
    n_rounds: int
    stop_reason: StopReason | None


@dataclass(frozen=True)
class LogMdView:
    status: DigestStatusView
    rounds: tuple[RoundDigestView, ...]
    formula: str | None
    hard_samples: HardSamplesView | None
    final: FinalWinnerView | None
    # Best first, so the head is the family's best wherever it beats this cycle's own.
    forks: tuple[ForkSummaryView, ...] = ()
