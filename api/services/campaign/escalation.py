"""EscalationCheck framework for mid-evaluation pipeline health checks.

Runs per-query during ``evaluate_prompt_batch()``.  When a check fires,
evaluation is aborted and the signal bubbles up through the feedback cycle
to trigger L2/L3 investigation or retry.

Extensible per warning type via ``EscalationStrategy``.  Forks can add
new ``EscalationCheck`` subclasses and strategy entries without changing
the framework.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.services.campaign.models import CycleConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass
class EscalationSignal:
    """Signal emitted when an EscalationCheck triggers mid-evaluation."""

    check_name: str
    target: str  # "retry" | "l2" | "l3" | "abort"
    context: dict[str, Any]
    candidate_idx: int
    candidates_evaluated: int
    candidates_skipped: int

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class EscalationStrategy:
    """Configurable response to a specific warning type."""

    target: str = "l2"


DEFAULT_STRATEGIES: dict[str, EscalationStrategy] = {
    "web_search:partial_scrape": EscalationStrategy(target="l2"),
}



# ---------------------------------------------------------------------------
# Exception for mid-eval abort
# ---------------------------------------------------------------------------


class EscalationError(Exception):
    """Raised inside evaluate_prompt_batch when a check fires."""

    def __init__(self, signal: EscalationSignal, partial_results: list[dict]):
        self.signal = signal
        self.partial_results = partial_results
        super().__init__(f"EscalationCheck '{signal.check_name}' triggered")


# ---------------------------------------------------------------------------
# EscalationCheck ABC + DegradationCheck
# ---------------------------------------------------------------------------


class EscalationCheck(ABC):
    """Base class for mid-evaluation escalation checks."""

    name: str = ""
    enabled: bool = True
    strategies: dict[str, EscalationStrategy] = field(default_factory=dict)

    @abstractmethod
    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        """Check results accumulated so far. Return signal to abort, or None."""
        ...


class DegradationCheck(EscalationCheck):
    """Triggers when degraded query fraction exceeds threshold."""

    def __init__(
        self,
        threshold: float = 0.4,
        strategies: dict[str, EscalationStrategy] | None = None,
    ):
        self.name = "degradation"
        self.enabled = True
        self.threshold = threshold
        self.strategies = strategies or dict(DEFAULT_STRATEGIES)

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        if not results_so_far:
            return None

        degraded = _count_degraded(results_so_far)
        rate = degraded / len(results_so_far)

        if rate < self.threshold:
            return None

        warning_types = collect_warning_types(results_so_far)
        dominant = max(warning_types, key=warning_types.get) if warning_types else "unknown"
        strategy = self.strategies.get(dominant, EscalationStrategy())

        return EscalationSignal(
            check_name=self.name,
            target=strategy.target,
            context={
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_evaluated": len(results_so_far),
                "warning_types": warning_types,
                "dominant_warning": dominant,
            },
            candidate_idx=candidate_idx,
            candidates_evaluated=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_degraded(results: list[dict]) -> int:
    return sum(
        1 for r in results
        if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
    )


def collect_warning_types(results: list[dict]) -> dict[str, int]:
    """Count occurrences of each warning type across results."""
    counts: Counter[str] = Counter()
    for r in results:
        for w in (
            (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings") or []
        ):
            if isinstance(w, dict):
                wtype = f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}"
            else:
                wtype = "unknown"
            counts[wtype] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_escalation_checks(config: "CycleConfig") -> list[EscalationCheck]:
    """Build enabled escalation checks from CycleConfig."""
    checks: list[EscalationCheck] = []
    threshold = getattr(config, "degradation_threshold", 0.0)
    if threshold > 0:
        checks.append(DegradationCheck(threshold=threshold))
    return checks
