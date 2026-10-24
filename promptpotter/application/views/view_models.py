from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from promptpotter.domain.cells import HardSamples
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phase_views import ViewAnchors
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import (
    DisplayMetric,
    HardSampleOrder,
    OptimizerFact,
    OverlapReading,
    RunStanding,
    ScoreSummary,
)
from promptpotter.domain.ruler import SeriesReading
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import LedgerSpend


@dataclass
class ViewContext:
    patience: int | None = None
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
    # The cycle chain's ledger, folded when the digest renders.
    spent: LedgerSpend
    rounds_completed: int
    started_at: str | None
    finished_at: str | None
    # Counted into ``rounds_completed`` but rendered separately.
    gen_only_rounds: int = 0


@dataclass(frozen=True)
class RoundDigestView:
    round: int
    label: str
    fold: ScoreSummary | None
    improved: bool
    changes_description: str
    facts: tuple[OptimizerFact, ...]
    overlap: OverlapReading
    # ``None`` where the round matched nothing: the cycle origin is a different basis, not a default.
    composite_floor: float | None = None
    level: SeriesReading | None = None
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
    # By ``Sample.key``, as the artifact holds its samples.
    sample_query_lookup: dict[str, str]
    # It ranks the leaderboard block, not the matrix.
    order: HardSampleOrder
    ranked: tuple[str, ...]


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
    # The ended cycle whose package states the winner; ``None`` while it runs.
    exported: CycleHop | None
    # Best first, so the head is the family's best wherever it beats this cycle's own.
    forks: tuple[ForkSummaryView, ...] = ()
