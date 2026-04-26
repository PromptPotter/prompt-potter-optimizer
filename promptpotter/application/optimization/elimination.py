"""Candidate elimination — four mid-evaluation checks (first-to-fire wins).

Each check exposes ``.name`` and ``.evaluate(results, candidate_idx, n_total_candidates)
-> EscalationSignal | None``. Ordering and rationale: see
``docs/methods/candidate-elimination.md``.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.utils import extract_warning_types
from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.shared.statistics import should_stop_early

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig


# Deterministic — one occurrence ends the candidate. Grow this set when a new
# warning proves equally conclusive; do not expose as a tunable.
FATAL_WARNINGS: frozenset[str] = frozenset({"llm_only:empty_content_reasoning_fallback"})


def _eliminate(
    name: str, check_result: dict, candidate_idx: int, n_total_candidates: int
) -> EscalationSignal:
    return EscalationSignal(
        check_name=name,
        target=EscalationTarget.ELIMINATE_CANDIDATE,
        check_result=check_result,
        candidate_idx=candidate_idx,
        candidates_scored=candidate_idx + 1,
        candidates_skipped=n_total_candidates - candidate_idx - 1,
    )


class DegradationCheck:
    """Fatal-warning fast-path + rate-based degradation."""

    name = "degradation"

    def __init__(self, threshold: float = 0.4, min_queries: int = 3) -> None:
        self.threshold = threshold
        self.min_queries = min_queries

    def evaluate(
        self, results: list[dict], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
        if results:
            latest = extract_warning_types(results[-1])
            fatal = next((w for w in latest if w in FATAL_WARNINGS), None)
            if fatal is not None:
                n = len(results)
                return _eliminate(
                    self.name,
                    {
                        "degraded_rate": 1.0,
                        "degraded_count": n,
                        "total_evaluated": n,
                        "warning_types": {fatal: 1},
                        "dominant_warning": fatal,
                        "fatal": True,
                    },
                    candidate_idx,
                    n_total_candidates,
                )

        n = len(results)
        if n < self.min_queries:
            return None
        degraded = count_degraded_queries(results)
        rate = degraded / n
        if rate < self.threshold:
            return None

        wtypes: Counter[str] = Counter()
        for r in results:
            wtypes.update(extract_warning_types(r))
        dominant = max(wtypes, key=wtypes.get) if wtypes else "unknown"  # type: ignore[arg-type]
        return _eliminate(
            self.name,
            {
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_evaluated": n,
                "warning_types": dict(wtypes),
                "dominant_warning": dominant,
            },
            candidate_idx,
            n_total_candidates,
        )


class EliminationCheck:
    """Paired one-sided Wilcoxon signed-rank vs completed priors (Holm-Bonferroni)."""

    name = "elimination"

    def __init__(self, *, n_min: int = 4, alpha: float = 0.2, n_queries: int) -> None:
        self.n_min = n_min
        self.alpha = alpha
        self.n_queries = n_queries
        self.priors: list[list[float]] = []
        self.prior_ids: list[str] = []

    def register_completed(self, scores: list[float], candidate_id: str = "") -> None:
        self.priors.append(scores)
        self.prior_ids.append(candidate_id)

    def evaluate(
        self, results: list[dict], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
        if not self.priors:
            return None
        n = len(results)
        if n < self.n_min:
            return None
        scores = [r.get("score", 0.0) for r in results]
        stop, ctx = should_stop_early(scores, self.priors, self.alpha)
        if not stop:
            return None
        return _eliminate(
            self.name,
            {
                "queries_evaluated": n,
                "total_queries": self.n_queries,
                "n_priors": len(self.priors),
                **ctx,
            },
            candidate_idx,
            n_total_candidates,
        )


def build_degradation_checks(config: CampaignConfig) -> list[Any]:
    """Per-query checks (degradation). EliminationCheck is built by the runner."""
    opt = config.optimization
    checks: list[Any] = []
    if opt.degradation_threshold > 0:
        checks.append(DegradationCheck(threshold=opt.degradation_threshold))
    return checks
