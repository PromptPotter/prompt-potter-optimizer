"""Hard-sample-sorter ASCII heatmap renderer.

Consumes the ``hard_samples.json`` artifact produced by
``application/intelligence/hard_sample_sorter.build_hard_samples_artifact``.
Same data powers the webapp heatmap — axes are precomputed per the spec's
sort contract so this renderer never sorts.

Shape (abridged — full schema in the builder):

    {
        "schema_version": 1,
        "disabled": bool,
        "truncated": bool,
        "total_candidates": int, "total_samples": int,
        "n_candidates": int, "n_samples": int, "n_observations": int,
        "candidate_order": [cid, ...],      # Y-axis, desc θ_c
        "sample_order":    [sid, ...],      # X-axis, desc δ_s (hardest left)
        "rasch": {theta, theta_se, delta, delta_se, ...},
        "cells": [{"c": cid, "s": sid, "hit": bool}, ...],
    }
"""

from __future__ import annotations

__all__ = ["render_hard_sample_heatmap"]

_HIT = "█"
_MISS = "▒"
_UNMEASURED = "·"

_DEFAULT_CANDIDATE_LABEL_WIDTH = 10
_DEFAULT_SAMPLE_ID_CELL_WIDTH = 2


def _cell_index(artifact: dict) -> dict[tuple[str, int], bool]:
    """``{(cid, sid): hit}`` lookup from the flat cells array. O(n) one-time build."""
    return {(c["c"], int(c["s"])): bool(c["hit"]) for c in artifact.get("cells", [])}


def render_hard_sample_heatmap(
    artifact: dict,
    *,
    sample_query_lookup: dict[int, str] | None = None,
) -> str:
    """Pretty-print the hard-sample-sorter heatmap + hardness leaderboard.

    Returns an empty string for disabled or empty-observation artifacts so
    callers can append unconditionally. All truncation decisions live in the
    builder; this renderer just draws whatever axes it was handed.
    """
    if not artifact:
        return ""
    if artifact.get("disabled"):
        return ""
    if not artifact.get("n_observations"):
        return ""

    candidate_order: list[str] = list(artifact.get("candidate_order", []))
    sample_order: list[int] = [int(s) for s in artifact.get("sample_order", [])]
    if not candidate_order or not sample_order:
        return ""

    cells = _cell_index(artifact)
    rasch = artifact.get("rasch") or {}
    theta = rasch.get("theta") or {}
    delta = rasch.get("delta") or {}

    n_cand = artifact.get("n_candidates", len(candidate_order))
    n_samp = artifact.get("n_samples", len(sample_order))
    total_cand = artifact.get("total_candidates", n_cand)
    total_samp = artifact.get("total_samples", n_samp)
    truncated = bool(artifact.get("truncated"))

    label_w = max(_DEFAULT_CANDIDATE_LABEL_WIDTH, min(24, max(len(c) for c in candidate_order)))
    cell_w = _DEFAULT_SAMPLE_ID_CELL_WIDTH

    lines = [
        "\nHARD-SAMPLE HEATMAP",
        "=" * 70,
        f"  candidates : {n_cand} of {total_cand}"
        + (" (truncated)" if truncated and n_cand < total_cand else ""),
        f"  samples    : {n_samp} of {total_samp}"
        + (" (truncated)" if truncated and n_samp < total_samp else ""),
        f"  observed cells : {artifact['n_observations']}",
        f"  legend : {_HIT} hit   {_MISS} miss   {_UNMEASURED} not measured",
    ]

    # Column header: axis direction + abbreviated sample ids (tens digit only
    # on this line — two-char cells keep the grid compact).
    header_pad = " " * (label_w + 3)
    lines.append("")
    lines.append(header_pad + "hardest ──── sample_id ────→ easiest")
    tens_row = header_pad + "".join(f"{(sid // 10) % 10:>{cell_w}d}" for sid in sample_order)
    ones_row = header_pad + "".join(f"{sid % 10:>{cell_w}d}" for sid in sample_order)
    lines.append(tens_row)
    lines.append(ones_row)

    # Grid rows — one per candidate, top-to-bottom by θ_c desc.
    for cid in candidate_order:
        row_cells = []
        for sid in sample_order:
            hit = cells.get((cid, sid))
            if hit is None:
                row_cells.append(_UNMEASURED * cell_w)
            elif hit:
                row_cells.append(_HIT * cell_w)
            else:
                row_cells.append(_MISS * cell_w)
        t = theta.get(cid)
        theta_str = f"{t:>+5.2f}" if isinstance(t, (int, float)) else "  ?  "
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {theta_str}  {''.join(row_cells)}")

    # Hardness leaderboard — top 10 by δ_s desc (already at the head of sample_order).
    top_samples = sample_order[:10]
    if top_samples:
        lookup = sample_query_lookup or {}
        lines.append("")
        lines.append(f"  Hardness leaderboard (top {len(top_samples)})")
        lines.append(f"    {'sample_id':>10s} {'delta':>8s} {'delta_se':>10s}  query")
        lines.append(f"    {'-' * 10} {'-' * 8} {'-' * 10}  {'-' * 40}")
        delta_se = rasch.get("delta_se") or {}
        for sid in top_samples:
            d = float(delta.get(str(sid), 0.0))
            se = float(delta_se.get(str(sid), 0.0))
            query = (lookup.get(sid) or "")[:40]
            lines.append(f"    {sid:>10d} {d:>+8.3f} {se:>10.3f}  {query}")

    return "\n".join(lines)
