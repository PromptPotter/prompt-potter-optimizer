"""Sequential candidate elimination for the optimization loop.

``EliminationCheck`` implements the ``DegradationCheck`` protocol consumed
by ``optimization/nodes/score.py::_score_candidates``. It compares the
in-progress candidate's per-query scores against fully-evaluated priors
via the Wilcoxon signed-rank test (with Holm-Bonferroni correction) and
emits an ``EscalationSignal`` to abort early when the candidate is
statistically inferior.
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

    def __init__(self, *, n_min: int = 4, alpha: float = 0.2, n_queries: int) -> None:
        self.n_min = n_min
        self.alpha = alpha
        self.n_queries = n_queries
        self.enabled = True
        self._prior_scores: list[list[float]] = []
        self._prior_ids: list[str] = []

    def register_completed(self, scores: list[float], candidate_id: str = "") -> None:
        """Register a fully-evaluated candidate's per-query scores as a prior.

        ``candidate_id`` is recorded alongside so elimination-cut decision
        records can point to the exact priors that triggered the test.
        """
        self._prior_scores.append(scores)
        self._prior_ids.append(candidate_id)

    def prior_ids_snapshot(self) -> list[str]:
        """Return the list of prior candidate ids, in registration order."""
        return list(self._prior_ids)

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

        current_scores = [r.get("score", 0.0) for r in results_so_far]
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
