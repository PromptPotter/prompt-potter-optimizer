"""Sequential candidate elimination for the optimization loop.

``EliminationCheck`` implements the ``DegradationCheck`` protocol consumed
by ``optimization/nodes/score.py::_score_candidates``. It compares the
in-progress candidate's per-query scores against fully-evaluated priors
via Welch's t-test (with Holm-Bonferroni correction) and emits an
``EscalationSignal`` to abort early when the candidate is statistically
inferior.
"""

from __future__ import annotations

import logging

from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.shared.statistics import should_stop_early

logger = logging.getLogger(__name__)


class EliminationCheck:
    """Stateful check that eliminates inferior candidates mid-evaluation.

    Conforms to the ``DegradationCheck`` protocol (``.enabled``, ``.name``,
    ``.evaluate(results_so_far, candidate_idx, n_total_candidates)``).
    """

    name: str = "elimination"

    def __init__(self, *, n_min: int = 20, alpha: float = 0.05, n_queries: int) -> None:
        self.n_min = n_min
        self.alpha = alpha
        self.n_queries = n_queries
        self.enabled = n_queries >= 2 * n_min
        self._prior_scores: list[list[float]] = []

    def register_completed(self, scores: list[float]) -> None:
        """Register a fully-evaluated candidate's per-query scores as a prior."""
        self._prior_scores.append(scores)

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        """Check whether the current candidate should be eliminated.

        Returns ``None`` to continue evaluation, or an ``EscalationSignal``
        to stop the candidate early.
        """
        if not self._prior_scores:
            return None

        n = len(results_so_far)
        if n < self.n_min:
            return None

        current_scores = [r["score"] for r in results_so_far]
        stop, ctx = should_stop_early(current_scores, self._prior_scores, self.alpha)

        if not stop:
            return None

        logger.info(
            "Elimination: candidate %d stopped at query %d/%d (p=%.4f vs prior %d)",
            candidate_idx,
            n,
            self.n_queries,
            ctx.get("triggered_p", 0.0),
            ctx.get("triggered_by_prior", -1),
        )
        return EscalationSignal(
            check_name=self.name,
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result={
                "queries_evaluated": n,
                "total_queries": self.n_queries,
                "n_priors": len(self._prior_scores),
                **ctx,
            },
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )
