"""
Comparison logic for execution results.

Computes statistical metrics (hit@k, MRR, McNemar's, Wilcoxon) for
variant A vs variant B pipeline comparisons.
"""

import logging
import statistics as stats
from typing import Any

logger = logging.getLogger(__name__)

P95_PERCENTILE = 0.95


def hit_at_k(ranked: list[dict], ground_truth: str, k: int) -> bool:
    """Check if ground_truth appears in top-k ranked candidates."""
    return any(c.get("candidate") == ground_truth for c in ranked[:k])


def reciprocal_rank(ranked: list[dict], ground_truth: str) -> float:
    """1 / (rank of ground_truth) or 0.0 if not found."""
    for i, c in enumerate(ranked):
        if c.get("candidate") == ground_truth:
            return 1.0 / (i + 1)
    return 0.0


def latency_stats(lats: list[float]) -> dict[str, float]:
    """Compute mean, median, p95 for a list of latencies.

    p95 is the value at the 95th percentile index (``int(n * 0.95)``),
    clamped to the last element.
    """
    if not lats:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0}
    s = sorted(lats)
    p95_idx = int(len(s) * P95_PERCENTILE)
    return {
        "mean": round(stats.mean(s), 1),
        "median": round(stats.median(s), 1),
        "p95": round(s[min(p95_idx, len(s) - 1)], 1),
    }


def mcnemar_test(
    a_correct: list[bool], b_correct: list[bool],
) -> dict[str, Any]:
    """McNemar's chi-squared test for paired binary outcomes.

    Returns:
        Dict with keys ``statistic``, ``p_value``, and optional ``note``.
        ``statistic`` and ``p_value`` are None when scipy is unavailable.
    """
    n01 = sum(1 for a, b in zip(a_correct, b_correct) if not a and b)
    n10 = sum(1 for a, b in zip(a_correct, b_correct) if a and not b)
    result: dict[str, Any] = {
        "statistic": None,
        "p_value": None,
        "n_a_only": n10,
        "n_b_only": n01,
    }

    if n01 + n10 == 0:
        result["p_value"] = 1.0
        result["note"] = "No discordant pairs"
        return result

    try:
        from scipy.stats import chi2

        stat = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
        result["statistic"] = round(stat, 4)
        result["p_value"] = round(1 - chi2.cdf(stat, df=1), 4)
    except ImportError:
        logger.warning(
            "scipy not installed — McNemar test unavailable. "
            "Install with: pip install scipy"
        )
        result["note"] = "scipy not installed"

    return result


def wilcoxon_test(
    a_latencies: list[float], b_latencies: list[float],
) -> dict[str, Any]:
    """Wilcoxon signed-rank test for paired latency comparison.

    Returns:
        Dict with keys ``statistic``, ``p_value``, and optional ``note``.
        ``statistic`` and ``p_value`` are None when scipy is unavailable
        or the test cannot be computed.
    """
    result: dict[str, Any] = {"statistic": None, "p_value": None}
    try:
        from scipy.stats import wilcoxon

        stat, p = wilcoxon(a_latencies, b_latencies)
        result["statistic"] = round(float(stat), 4)
        result["p_value"] = round(float(p), 4)
    except ImportError:
        logger.warning(
            "scipy not installed — Wilcoxon test unavailable. "
            "Install with: pip install scipy"
        )
        result["note"] = "scipy not installed"
    except ValueError as e:
        result["note"] = str(e)

    return result


def compute_comparison(
    results: list[dict],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute full statistical comparison between variant A and B.

    Each result dict must contain both variant's data (variant_b_* fields).
    ``metadata`` may supply ``pipeline_notation``, ``variant_label``,
    ``session_terms_count``, and ``limitations``.
    """
    metadata = metadata or {}
    successful = [r for r in results if r.get("status") == "success"]
    n = len(successful)
    if n == 0:
        raise ValueError("No successful results to compare")

    a_correct: list[bool] = []
    b_correct: list[bool] = []
    a_latencies: list[float] = []
    b_latencies: list[float] = []
    a_hit3 = a_hit5 = 0
    a_rr_sum = 0.0

    classification: dict[str, list[dict]] = {
        "both_correct": [],
        "a_only_correct": [],
        "b_only_correct": [],
        "both_wrong": [],
    }

    for r in successful:
        gt = r["ground_truth"]
        a_pred = r.get("predicted", "")
        b_pred = r.get("variant_b_predicted", "")
        ranked = r.get("ranked_candidates", [])

        a_ok = a_pred == gt
        b_ok = b_pred == gt
        a_correct.append(a_ok)
        b_correct.append(b_ok)
        a_latencies.append(r.get("latency_ms", 0))
        b_latencies.append(r.get("variant_b_latency_ms", 0))

        if hit_at_k(ranked, gt, 3):
            a_hit3 += 1
        if hit_at_k(ranked, gt, 5):
            a_hit5 += 1
        a_rr_sum += reciprocal_rank(ranked, gt)

        summary = {
            "query": r["query"],
            "ground_truth": gt,
            "a_predicted": a_pred,
            "b_predicted": b_pred,
        }
        if a_ok and b_ok:
            classification["both_correct"].append(summary)
        elif a_ok:
            classification["a_only_correct"].append(summary)
        elif b_ok:
            classification["b_only_correct"].append(summary)
        else:
            classification["both_wrong"].append(summary)

    mcnemar = mcnemar_test(a_correct, b_correct)
    wilcoxon = wilcoxon_test(a_latencies, b_latencies)

    a_hit1 = sum(a_correct)
    b_hit1 = sum(b_correct)

    return {
        "pipeline": {
            "variant_a": {
                "notation": metadata.get("pipeline_notation", ""),
                "label": metadata.get("variant_label", ""),
            },
            "variant_b": {
                "notation": metadata.get(
                    "variant_b_notation", "LLM1-TokenMatching-LLM2",
                ),
                "label": "Full pipeline",
            },
        },
        "dataset": {
            "query_count": n,
            "session_terms": metadata.get("session_terms_count"),
        },
        "metrics": {
            "hit_at_1": {
                "a": round(a_hit1 / n, 4),
                "a_count": a_hit1,
                "b": round(b_hit1 / n, 4),
                "b_count": b_hit1,
                "mcnemar": mcnemar,
            },
            "hit_at_3": {
                "a": round(a_hit3 / n, 4), "a_count": a_hit3, "b": None,
            },
            "hit_at_5": {
                "a": round(a_hit5 / n, 4), "a_count": a_hit5, "b": None,
            },
            "mrr": {"a": round(a_rr_sum / n, 4), "b": None},
            "latency_ms": {
                "a": latency_stats(a_latencies),
                "b": latency_stats(b_latencies),
                "wilcoxon": wilcoxon,
            },
        },
        "classification": {
            "both_correct": len(classification["both_correct"]),
            "a_only_correct": len(classification["a_only_correct"]),
            "b_only_correct": len(classification["b_only_correct"]),
            "both_wrong": len(classification["both_wrong"]),
            "details": classification,
        },
        "limitations": metadata.get("limitations", []),
    }
