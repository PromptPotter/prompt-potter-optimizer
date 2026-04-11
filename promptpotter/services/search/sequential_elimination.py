"""Sequential candidate elimination via paired Welch's t-test.

Candidates are evaluated query-by-query on a shared dataset.  The first
candidate runs to completion (reference population).  Each subsequent
candidate is tested after *n_min* queries against all prior populations
using a one-sided paired t-test with Holm-Bonferroni correction.

Requires ``scipy`` (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from promptpotter.services.campaign.escalation import EscalationSignal, EscalationTarget

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def _paired_ttest_pvalue(current: list[float], prior: list[float]) -> float:
    """One-sided paired t-test p-value (H_a: prior is better than current).

    Uses ``scipy.stats.ttest_rel`` on the shared prefix, then converts
    the two-sided p-value to one-sided.  Returns 1.0 when variance of
    differences is zero (identical score vectors).
    """
    from scipy.stats import ttest_rel  # type: ignore[import-untyped]

    n = min(len(current), len(prior))
    if n < 2:
        return 1.0

    stat, p_two = ttest_rel(prior[:n], current[:n])

    if math.isnan(p_two):
        return 1.0

    # One-sided: H_a is "prior mean > current mean" → reject when t > 0
    if stat > 0:
        return p_two / 2
    return 1 - p_two / 2


def _holm_bonferroni(p_values: list[float], alpha: float) -> list[bool]:
    """Holm-Bonferroni step-down correction.

    Returns a list of booleans (same order as *p_values*) indicating
    which null hypotheses are rejected at family-wise *alpha*.
    """
    m = len(p_values)
    if m == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    rejected = [False] * m

    for rank, (orig_idx, p) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p < threshold:
            rejected[orig_idx] = True
        else:
            break  # step-down: stop at first non-rejection

    return rejected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_stop_early(
    current_scores: list[float],
    prior_populations: list[list[float]],
    alpha: float = 0.05,
) -> tuple[bool, dict[str, Any]]:
    """Decide whether to eliminate the current candidate.

    Runs a one-sided paired t-test against each prior population on the
    shared query prefix, then applies Holm-Bonferroni correction.

    Returns:
        ``(stop, context)`` where *context* carries p-values and the
        index of the prior that triggered rejection (if any).
    """
    if not prior_populations:
        return False, {}

    p_values = [_paired_ttest_pvalue(current_scores, prior) for prior in prior_populations]
    rejections = _holm_bonferroni(p_values, alpha)

    ctx: dict[str, Any] = {"p_values": p_values, "rejections": rejections}

    if any(rejections):
        trigger_idx = rejections.index(True)
        ctx["triggered_by_prior"] = trigger_idx
        ctx["triggered_p"] = p_values[trigger_idx]
        return True, ctx

    return False, ctx


class EliminationCheck:
    """Stateful check that eliminates inferior candidates mid-evaluation.

    Conforms to the ``DegradationCheck`` protocol (``.enabled``, ``.name``,
    ``.evaluate(results_so_far, candidate_idx, n_total_candidates)``), so
    it plugs directly into ``_run_degradation_checks()`` in
    ``dataset_scoring.py``.
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
            target=EscalationTarget.ABORT,
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
