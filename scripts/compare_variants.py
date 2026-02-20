#!/usr/bin/env python3
"""
Compare Variant A (no LLM2) vs Variant B (full pipeline) results.

Loads both fixtures and computes ML metrics with statistical significance
tests (McNemar's, Wilcoxon). Outputs structured JSON and/or a text report.
Works entirely offline — no API calls needed.

Usage:
    python scripts/compare_variants.py
    python scripts/compare_variants.py --format json
    python scripts/compare_variants.py --format markdown
    python scripts/compare_variants.py --export-csv comparison.csv
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
VARIANT_A_PATH = FIXTURE_DIR / "experiment_1_variant_a_replay.json"
VARIANT_B_PATH = FIXTURE_DIR / "experiment_1_mappings_response.json"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_variant_a(path: Optional[Path] = None) -> List[Dict]:
    """Load replay results (Variant A)."""
    path = path or VARIANT_A_PATH
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [r for r in data["results"] if r["status"] == "success"]


def load_variant_a_metadata(path: Optional[Path] = None) -> Dict:
    """Load Variant A metadata (not per-result)."""
    path = path or VARIANT_A_PATH
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        k: v for k, v in data.items() if k != "results"
    }


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def hit_at_k(ranked_candidates: List[Dict], ground_truth: str, k: int) -> bool:
    """Check if ground_truth appears in top-k candidates."""
    for candidate in ranked_candidates[:k]:
        if candidate.get("candidate") == ground_truth:
            return True
    return False


def reciprocal_rank(ranked_candidates: List[Dict], ground_truth: str) -> float:
    """1 / (rank of ground_truth) or 0 if not found."""
    for i, candidate in enumerate(ranked_candidates):
        if candidate.get("candidate") == ground_truth:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------

def mcnemar_test(a_correct: List[bool], b_correct: List[bool]) -> Dict[str, Any]:
    """McNemar's test for paired binary classification outcomes.

    Tests whether A and B disagree significantly on correctness.
    Uses scipy if available, otherwise falls back to manual calculation.
    """
    # Build 2x2 contingency table
    # n00: both wrong, n01: A wrong B right, n10: A right B wrong, n11: both right
    n00 = n01 = n10 = n11 = 0
    for a, b in zip(a_correct, b_correct):
        if a and b:
            n11 += 1
        elif a and not b:
            n10 += 1
        elif not a and b:
            n01 += 1
        else:
            n00 += 1

    contingency = {"both_correct": n11, "a_only": n10, "b_only": n01, "both_wrong": n00}

    # Discordant pairs
    discordant = n01 + n10
    if discordant == 0:
        return {
            "statistic": 0.0,
            "p_value": 1.0,
            "contingency": contingency,
            "note": "No discordant pairs — variants agree on all queries",
        }

    try:
        from scipy.stats import chi2

        # McNemar's chi-squared (with continuity correction)
        stat = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
        p_value = 1 - chi2.cdf(stat, df=1)
        return {"statistic": round(stat, 4), "p_value": round(p_value, 4), "contingency": contingency}
    except ImportError:
        # Manual approximation without scipy
        stat = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
        return {
            "statistic": round(stat, 4),
            "p_value": None,
            "contingency": contingency,
            "note": "scipy not installed — p-value not computed (install scipy for full analysis)",
        }


def wilcoxon_test(a_latencies: List[float], b_latencies: List[float]) -> Dict[str, Any]:
    """Wilcoxon signed-rank test for paired latency comparison."""
    try:
        from scipy.stats import wilcoxon

        stat, p_value = wilcoxon(a_latencies, b_latencies)
        return {"statistic": round(float(stat), 4), "p_value": round(float(p_value), 4)}
    except ImportError:
        return {
            "statistic": None,
            "p_value": None,
            "note": "scipy not installed — install scipy for statistical tests",
        }
    except ValueError as e:
        return {"statistic": None, "p_value": None, "note": str(e)}


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compute_comparison(results: List[Dict]) -> Dict[str, Any]:
    """Compute full comparison between Variant A and B.

    Each result dict must contain both variant's data (embedded by replay script).
    """
    n = len(results)

    # Per-query metrics
    a_correct_list = []
    b_correct_list = []
    a_latencies = []
    b_latencies = []
    a_hit3 = 0
    a_hit5 = 0
    a_rr_sum = 0.0

    classification = {
        "both_correct": [],
        "a_only_correct": [],
        "b_only_correct": [],
        "both_wrong": [],
    }

    for r in results:
        gt = r["ground_truth"]
        a_pred = r.get("predicted", "")
        b_pred = r.get("variant_b_predicted", "")
        ranked = r.get("ranked_candidates", [])

        a_hit1 = a_pred == gt
        b_hit1 = b_pred == gt
        a_correct_list.append(a_hit1)
        b_correct_list.append(b_hit1)

        a_latencies.append(r.get("latency_ms", 0))
        b_latencies.append(r.get("variant_b_latency_ms", 0))

        # Variant A has full ranked list
        if hit_at_k(ranked, gt, 3):
            a_hit3 += 1
        if hit_at_k(ranked, gt, 5):
            a_hit5 += 1
        a_rr_sum += reciprocal_rank(ranked, gt)

        # Classify
        query_summary = {
            "query": r["query"],
            "bom_material": r.get("bom_material", ""),
            "ground_truth": gt,
            "a_predicted": a_pred,
            "b_predicted": b_pred,
        }
        if a_hit1 and b_hit1:
            classification["both_correct"].append(query_summary)
        elif a_hit1 and not b_hit1:
            classification["a_only_correct"].append(query_summary)
        elif not a_hit1 and b_hit1:
            classification["b_only_correct"].append(query_summary)
        else:
            classification["both_wrong"].append(query_summary)

    a_hit1_count = sum(a_correct_list)
    b_hit1_count = sum(b_correct_list)

    # Statistical tests
    mcnemar = mcnemar_test(a_correct_list, b_correct_list)
    wilcoxon = wilcoxon_test(a_latencies, b_latencies)

    # Latency stats
    def latency_stats(lats: List[float]) -> Dict:
        if not lats:
            return {"mean": 0, "median": 0, "p95": 0}
        s = sorted(lats)
        p95_idx = int(len(s) * 0.95)
        return {
            "mean": round(statistics.mean(s), 1),
            "median": round(statistics.median(s), 1),
            "p95": round(s[min(p95_idx, len(s) - 1)], 1),
        }

    return {
        "pipeline": {
            "variant_a": {
                "notation": "LLM1-TokenMatching",
                "description": "No LLM2 reranking",
            },
            "variant_b": {
                "notation": "LLM1-TokenMatching-LLM2",
                "description": "Full pipeline with LLM2 semantic reranking",
            },
        },
        "dataset": {"query_count": n},
        "metrics": {
            "hit_at_1": {
                "a": round(a_hit1_count / n, 4) if n else 0,
                "a_count": a_hit1_count,
                "b": round(b_hit1_count / n, 4) if n else 0,
                "b_count": b_hit1_count,
                "mcnemar": mcnemar,
            },
            "hit_at_3": {"a": round(a_hit3 / n, 4) if n else 0, "a_count": a_hit3, "b": None},
            "hit_at_5": {"a": round(a_hit5 / n, 4) if n else 0, "a_count": a_hit5, "b": None},
            "mrr": {"a": round(a_rr_sum / n, 4) if n else 0, "b": None},
            "latency_ms": {
                "a": latency_stats(a_latencies),
                "b": latency_stats(b_latencies),
                "wilcoxon": wilcoxon,
            },
            "confidence": {
                "a_mean": round(statistics.mean(r.get("confidence", 0) for r in results), 4) if results else 0,
                "b_mean": round(
                    statistics.mean(r.get("variant_b_confidence", 0) for r in results), 4
                )
                if results
                else 0,
            },
        },
        "classification": {
            "both_correct": len(classification["both_correct"]),
            "a_only_correct": len(classification["a_only_correct"]),
            "b_only_correct": len(classification["b_only_correct"]),
            "both_wrong": len(classification["both_wrong"]),
            "details": classification,
        },
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(comp: Dict, meta: Dict) -> str:
    """Format comparison as a text report."""
    m = comp["metrics"]
    c = comp["classification"]
    n = comp["dataset"]["query_count"]
    h1 = m["hit_at_1"]
    lat = m["latency_ms"]

    lines = []
    lines.append("=" * 70)
    lines.append("  TermNorm Pipeline Comparison: Variant A vs Variant B")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  Variant A: {comp['pipeline']['variant_a']['notation']}"
                 f"  ({comp['pipeline']['variant_a']['description']})")
    lines.append(f"  Variant B: {comp['pipeline']['variant_b']['notation']}"
                 f"  ({comp['pipeline']['variant_b']['description']})")
    lines.append(f"  Queries evaluated: {n}")
    lines.append(f"  Session terms: {meta.get('session_terms_count', '?')}")
    lines.append("")

    # Accuracy
    lines.append("  ACCURACY")
    lines.append("  " + "-" * 66)
    lines.append(f"  {'Metric':<22} {'Variant A':<16} {'Variant B':<16} {'p-value':<10}")
    lines.append("  " + "-" * 66)

    a_pct = f"{h1['a_count']}/{n} ({h1['a'] * 100:.1f}%)"
    b_pct = f"{h1['b_count']}/{n} ({h1['b'] * 100:.1f}%)"
    lines.append(f"  {'hit@1':<22} {a_pct:<16} {b_pct:<16}")

    mc = h1["mcnemar"]
    p_str = f"p={mc['p_value']:.4f}" if mc.get("p_value") is not None else mc.get("note", "n/a")
    lines.append(f"  {'McNemar test':<22} {'':16} {'':16} {p_str}")

    h3 = m["hit_at_3"]
    h5 = m["hit_at_5"]
    mrr = m["mrr"]
    lines.append(f"  {'hit@3 (A only)':<22} {h3['a_count']}/{n} ({h3['a'] * 100:.1f}%){'':5} {'n/a':<16}")
    lines.append(f"  {'hit@5 (A only)':<22} {h5['a_count']}/{n} ({h5['a'] * 100:.1f}%){'':5} {'n/a':<16}")
    lines.append(f"  {'MRR (A only)':<22} {mrr['a']:.4f}{'':11} {'n/a':<16}")
    lines.append("")

    # Latency
    lines.append("  LATENCY")
    lines.append("  " + "-" * 66)
    lines.append(f"  {'Metric':<22} {'Variant A':<16} {'Variant B':<16} {'p-value':<10}")
    lines.append("  " + "-" * 66)
    lines.append(f"  {'Avg latency':<22} {lat['a']['mean']:.0f}ms{'':<10} {lat['b']['mean']:.0f}ms")
    lines.append(f"  {'Median latency':<22} {lat['a']['median']:.0f}ms{'':<9} {lat['b']['median']:.0f}ms")
    lines.append(f"  {'P95 latency':<22} {lat['a']['p95']:.0f}ms{'':<10} {lat['b']['p95']:.0f}ms")

    w = lat["wilcoxon"]
    w_str = f"p={w['p_value']:.4f}" if w.get("p_value") is not None else w.get("note", "n/a")
    lines.append(f"  {'Wilcoxon test':<22} {'':16} {'':16} {w_str}")
    lines.append("")

    # Classification
    lines.append("  PER-QUERY CLASSIFICATION")
    lines.append("  " + "-" * 66)
    lines.append(f"  Both correct:       {c['both_correct']:3d}    LLM2 made no difference")
    lines.append(f"  A-only correct:     {c['a_only_correct']:3d}    LLM2 HURT these queries")
    lines.append(f"  B-only correct:     {c['b_only_correct']:3d}    LLM2 HELPED these queries")
    lines.append(f"  Both wrong:         {c['both_wrong']:3d}    Neither pipeline got it")
    lines.append("")

    # Details: where LLM2 helped
    details = c["details"]
    if details["b_only_correct"]:
        lines.append("  QUERIES WHERE LLM2 HELPED (B correct, A wrong):")
        for q in details["b_only_correct"]:
            lines.append(f"    - {q['query'][:60]}")
            lines.append(f"      GT: {q['ground_truth'][:55]}")
            lines.append(f"      A:  {q['a_predicted'][:55]}")
            lines.append(f"      B:  {q['b_predicted'][:55]}")
        lines.append("")

    # Details: where LLM2 hurt
    if details["a_only_correct"]:
        lines.append("  QUERIES WHERE LLM2 HURT (A correct, B wrong):")
        for q in details["a_only_correct"]:
            lines.append(f"    - {q['query'][:60]}")
            lines.append(f"      GT: {q['ground_truth'][:55]}")
            lines.append(f"      A:  {q['a_predicted'][:55]}")
            lines.append(f"      B:  {q['b_predicted'][:55]}")
        lines.append("")

    # Limitations
    lines.append("  LIMITATIONS:")
    for lim in meta.get("limitations", []):
        lines.append(f"    - {lim}")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def format_markdown(comp: Dict, meta: Dict) -> str:
    """Format comparison as markdown."""
    m = comp["metrics"]
    c = comp["classification"]
    n = comp["dataset"]["query_count"]
    h1 = m["hit_at_1"]
    lat = m["latency_ms"]
    mc = h1["mcnemar"]
    w = lat["wilcoxon"]

    lines = []
    lines.append("# TermNorm Pipeline Comparison: Variant A vs Variant B\n")
    lines.append(f"- **Variant A:** {comp['pipeline']['variant_a']['notation']}"
                 f" ({comp['pipeline']['variant_a']['description']})")
    lines.append(f"- **Variant B:** {comp['pipeline']['variant_b']['notation']}"
                 f" ({comp['pipeline']['variant_b']['description']})")
    lines.append(f"- **Queries:** {n}")
    lines.append(f"- **Session terms:** {meta.get('session_terms_count', '?')}\n")

    lines.append("## Accuracy\n")
    lines.append("| Metric | Variant A | Variant B | p-value |")
    lines.append("|--------|-----------|-----------|---------|")

    a_pct = f"{h1['a_count']}/{n} ({h1['a'] * 100:.1f}%)"
    b_pct = f"{h1['b_count']}/{n} ({h1['b'] * 100:.1f}%)"
    p_str = f"{mc['p_value']:.4f}" if mc.get("p_value") is not None else "n/a"
    lines.append(f"| hit@1 | {a_pct} | {b_pct} | McNemar p={p_str} |")
    lines.append(f"| hit@3 | {m['hit_at_3']['a_count']}/{n} ({m['hit_at_3']['a'] * 100:.1f}%) | n/a | |")
    lines.append(f"| hit@5 | {m['hit_at_5']['a_count']}/{n} ({m['hit_at_5']['a'] * 100:.1f}%) | n/a | |")
    lines.append(f"| MRR | {m['mrr']['a']:.4f} | n/a | |\n")

    lines.append("## Latency\n")
    lines.append("| Metric | Variant A | Variant B | p-value |")
    lines.append("|--------|-----------|-----------|---------|")
    w_str = f"{w['p_value']:.4f}" if w.get("p_value") is not None else "n/a"
    lines.append(f"| Mean | {lat['a']['mean']:.0f}ms | {lat['b']['mean']:.0f}ms"
                 f" | Wilcoxon p={w_str} |")
    lines.append(f"| Median | {lat['a']['median']:.0f}ms | {lat['b']['median']:.0f}ms | |")
    lines.append(f"| P95 | {lat['a']['p95']:.0f}ms | {lat['b']['p95']:.0f}ms | |\n")

    lines.append("## Per-Query Classification\n")
    lines.append(f"- **Both correct:** {c['both_correct']}")
    lines.append(f"- **A-only correct (LLM2 hurt):** {c['a_only_correct']}")
    lines.append(f"- **B-only correct (LLM2 helped):** {c['b_only_correct']}")
    lines.append(f"- **Both wrong:** {c['both_wrong']}\n")

    details = c["details"]
    if details["b_only_correct"]:
        lines.append("### Where LLM2 Helped\n")
        for q in details["b_only_correct"]:
            lines.append(f"- **{q['query']}**")
            lines.append(f"  - GT: `{q['ground_truth']}`")
            lines.append(f"  - A: `{q['a_predicted']}`")
            lines.append(f"  - B: `{q['b_predicted']}`")

    if details["a_only_correct"]:
        lines.append("\n### Where LLM2 Hurt\n")
        for q in details["a_only_correct"]:
            lines.append(f"- **{q['query']}**")
            lines.append(f"  - GT: `{q['ground_truth']}`")
            lines.append(f"  - A: `{q['a_predicted']}`")
            lines.append(f"  - B: `{q['b_predicted']}`")

    return "\n".join(lines)


def export_csv(comp: Dict, results: List[Dict], path: Path) -> None:
    """Export per-query comparison to CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "query", "bom_material", "ground_truth",
            "a_predicted", "a_correct", "a_latency_ms",
            "b_predicted", "b_correct", "b_latency_ms",
            "classification",
        ])
        for r in results:
            gt = r["ground_truth"]
            a_pred = r.get("predicted", "")
            b_pred = r.get("variant_b_predicted", "")
            a_ok = a_pred == gt
            b_ok = b_pred == gt
            if a_ok and b_ok:
                cls = "both_correct"
            elif a_ok:
                cls = "a_only_correct"
            elif b_ok:
                cls = "b_only_correct"
            else:
                cls = "both_wrong"
            writer.writerow([
                r["query"], r.get("bom_material", ""), gt,
                a_pred, a_ok, r.get("latency_ms", ""),
                b_pred, b_ok, r.get("variant_b_latency_ms", ""),
                cls,
            ])
    print(f"  CSV exported to {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Compare Variant A vs B")
    parser.add_argument(
        "--variant-a",
        type=Path,
        default=None,
        help=f"Path to Variant A replay JSON (default: {VARIANT_A_PATH})",
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        help="Export per-query comparison to CSV",
    )
    args = parser.parse_args()

    # Load data
    a_path = args.variant_a or VARIANT_A_PATH
    if not a_path.exists():
        print(f"Error: Variant A results not found at {a_path}")
        print("Run replay_without_llm2.py first to generate them.")
        sys.exit(1)

    results = load_variant_a(a_path)
    meta = load_variant_a_metadata(a_path)

    if not results:
        print("Error: No successful results in Variant A data.")
        sys.exit(1)

    # Compute comparison
    comp = compute_comparison(results)

    # Output
    if args.format == "json":
        print(json.dumps(comp, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(format_markdown(comp, meta))
    else:
        print(format_text(comp, meta))

    # CSV export
    if args.export_csv:
        export_csv(comp, results, args.export_csv)


if __name__ == "__main__":
    main()
