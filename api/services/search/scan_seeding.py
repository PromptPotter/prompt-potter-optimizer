"""Deterministic scan context preparation for scan-aware feedback cycle.

Formats sensitivity scan results (leaderboard, axis profiles, query difficulty)
into structured text that the LLM candidate generator can reason about.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def prepare_scan_context(
    scan_df,
    axis_profiles: list[dict],
    scan_variants: dict[str, list],
    baseline_accuracy: float,
    *,
    difficulty_summary: dict | None = None,
) -> dict:
    """Build structured scan context for the LLM meta-prompt.

    All formatting is deterministic — no LLM calls. Returns a dict with
    pre-formatted text sections plus structured data for downstream use.

    Args:
        scan_df: DataFrame from ``sensitivity_scan()`` with columns
            axis, axis_type, value_idx, value_preview, hits, total,
            accuracy, delta, composite (optional), errors.
        axis_profiles: Axis profile dicts sorted by sensitivity_range desc.
        scan_variants: Flat dict mapping axis names to value lists.
        baseline_accuracy: Baseline accuracy from the scan.
        difficulty_summary: Optional query difficulty counts
            (keys: easy, hard, discriminating, error, total).

    Returns:
        Dict with keys: leaderboard_text, sensitivity_text, difficulty_text,
        improving_axes, tested_values, baseline_accuracy.
    """
    # --- Leaderboard text ---
    sort_col = "composite" if "composite" in scan_df.columns else "accuracy"
    ranked = scan_df.sort_values(sort_col, ascending=False)

    lines = []
    for _, r in ranked.iterrows():
        label = r["value_preview"]
        bl_tag = " (baseline)" if r["delta"] == 0.0 else ""
        lines.append(
            f"  {r['axis']:<24s} {label:<40s} "
            f"acc={r['accuracy']:.1%}  delta={r['delta']:+.1%}"
            f"  hits={r['hits']}/{r['total']}{bl_tag}"
        )
    leaderboard_text = "\n".join(lines)

    # --- Sensitivity text ---
    sens_lines = []
    for i, p in enumerate(axis_profiles, 1):
        room = ""
        if p["best_delta"] > 0:
            room = f"  [room: best_delta={p['best_delta']:+.1%}]"
        sens_lines.append(
            f"  {i}. {p['axis']:<24s} type={p['axis_type']:<14s} "
            f"sensitivity={p['sensitivity_range']:.3f}  "
            f"card={p['cardinality']}  budget={p['exploration_budget']}"
            f"{room}"
        )
    sensitivity_text = "\n".join(sens_lines)

    # --- Difficulty text ---
    if difficulty_summary:
        total = difficulty_summary.get("total", 0)
        parts = []
        for cat in ("easy", "discriminating", "hard", "error"):
            c = difficulty_summary.get(cat, 0)
            pct = f" ({c / total:.0%})" if total else ""
            parts.append(f"{cat}: {c}{pct}")
        difficulty_text = f"  Query classes: {' | '.join(parts)}"
    else:
        difficulty_text = "  (not available)"

    # --- Improving axes ---
    _improving = [
        p for p in axis_profiles
        if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]
    improving_axes = [p["axis"] for p in _improving]
    has_prompt_axes = any(p["axis_type"] == "prompt_field" for p in _improving)

    # --- Tested values per axis ---
    tested_lines = []
    for axis_name in improving_axes:
        axis_rows = scan_df[scan_df["axis"] == axis_name].sort_values(
            "accuracy", ascending=False,
        )
        vals = []
        for _, r in axis_rows.iterrows():
            bl = " *baseline*" if r["delta"] == 0.0 else ""
            vals.append(f"    [{r['value_idx']}] {r['value_preview'][:50]}"
                        f"  acc={r['accuracy']:.1%}{bl}")
        tested_lines.append(f"  {axis_name} ({len(axis_rows)} values tested):")
        tested_lines.extend(vals)
    tested_values = "\n".join(tested_lines)

    return {
        "leaderboard_text": leaderboard_text,
        "sensitivity_text": sensitivity_text,
        "difficulty_text": difficulty_text,
        "improving_axes": improving_axes,
        "has_prompt_axes": has_prompt_axes,
        "tested_values": tested_values,
        "baseline_accuracy": baseline_accuracy,
    }
