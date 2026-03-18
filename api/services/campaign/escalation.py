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
        return {
            "check_name": self.check_name,
            "target": self.target,
            "context": self.context,
            "candidate_idx": self.candidate_idx,
            "candidates_evaluated": self.candidates_evaluated,
            "candidates_skipped": self.candidates_skipped,
        }


@dataclass
class EscalationStrategy:
    """Configurable response to a specific warning type.

    Extensible: forks add their own entries to ``DEFAULT_STRATEGIES``.
    """

    target: str = "l2"
    max_retries: int = 1
    apply_critique_suggestion: bool = False


DEFAULT_STRATEGIES: dict[str, EscalationStrategy] = {
    "web_search:partial_scrape": EscalationStrategy(target="l2"),
}


@dataclass
class WarningClassification:
    """Per-warning-type investigation status."""

    warning_type: str
    status: str  # "under_investigation" | "config_sensitivity" | "connector_code"
    evidence: list[str] = field(default_factory=list)
    rounds_observed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_type": self.warning_type,
            "status": self.status,
            "evidence": self.evidence,
            "rounds_observed": self.rounds_observed,
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


def classify_warnings(
    warning_types: dict[str, int],
    round_history: list[dict],
) -> list[WarningClassification]:
    """Classify each warning type based on evidence from round history.

    Default: ``under_investigation``. Promoted to ``config_sensitivity``
    only when the warning rate correlates with a pipeline_param change
    across rounds.  ``connector_code`` is never auto-assigned (human decision).
    """
    classifications: list[WarningClassification] = []

    for wtype, count in warning_types.items():
        rounds_seen = _count_rounds_with_warning_type(wtype, round_history)
        status = "under_investigation"
        evidence = [f"Observed in {rounds_seen} round(s), {count} queries this round"]

        if rounds_seen >= 2:
            correlation = _check_param_correlation(wtype, round_history)
            if correlation:
                status = "config_sensitivity"
                evidence.append(f"Correlated with param change: {correlation}")

        classifications.append(WarningClassification(
            warning_type=wtype,
            status=status,
            evidence=evidence,
            rounds_observed=rounds_seen,
        ))

    return classifications


def _count_rounds_with_warning_type(wtype: str, round_history: list[dict]) -> int:
    """Count how many rounds had this specific warning type."""
    count = 0
    for rh in round_history:
        wt = rh.get("warning_types") or {}
        if wtype in wt:
            count += 1
    return count


def _check_param_correlation(wtype: str, round_history: list[dict]) -> str | None:
    """Check if warning rate changed when pipeline_params changed.

    Returns a description of the correlated param, or None.
    """
    if len(round_history) < 2:
        return None

    for i in range(1, len(round_history)):
        prev = round_history[i - 1]
        curr = round_history[i]

        prev_pp = prev.get("pipeline_params") or {}
        curr_pp = curr.get("pipeline_params") or {}

        prev_wt = prev.get("warning_types") or {}
        curr_wt = curr.get("warning_types") or {}

        prev_count = prev_wt.get(wtype, 0)
        curr_count = curr_wt.get(wtype, 0)

        if prev_count == curr_count:
            continue

        changed_params = {
            k for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed_params:
            direction = "decreased" if curr_count < prev_count else "increased"
            return (
                f"{', '.join(sorted(changed_params))} "
                f"(warning {direction}: {prev_count}→{curr_count})"
            )

    return None


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
