"""Hard-sample-sorter heatmap — plain-text grid + hardness leaderboard.
Used by the standalone CLI hard-sample run and ``to_markdown`` when ``LogMdView.hard_samples`` is set."""

from __future__ import annotations

from typing import Any

# Graded shade, densest = best. A binary ▓/▒ split would re-impose the `fitness >= 1.0`
# threshold this projection exists to avoid: on a graded scorer every cell sits strictly
# inside (0,1), so a threshold renders the whole grid identically and the matrix carries
# no information at all.
_SHADES = ("░", "▒", "▓", "█")
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _shade(fitness: float) -> str:
    """Bucket a [0,1] fitness onto ``_SHADES``. 1.0 lands in the top bucket."""
    idx = min(int(fitness * len(_SHADES)), len(_SHADES) - 1)
    return _SHADES[idx]


def _cell_index(artifact: dict[str, Any]) -> dict[tuple[str, int], float]:
    return {(c["c"], int(c["s"])): float(c["f"]) for c in artifact.get("cells", [])}


def render_hard_sample_heatmap(
    artifact: dict[str, Any],
    *,
    sample_query_lookup: dict[int, str] | None = None,
) -> str:
    if not artifact or not artifact.get("n_observations"):
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

    label_w = max(_HEATMAP_LABEL_W, min(24, max(len(c) for c in candidate_order)))
    cell_w = _HEATMAP_CELL_W

    cand_str = f"{n_cand} of {total_cand}" + (
        " (truncated)" if truncated and n_cand < total_cand else ""
    )
    samp_str = f"{n_samp} of {total_samp}" + (
        " (truncated)" if truncated and n_samp < total_samp else ""
    )
    lines = [
        f"  individuals : {cand_str}",
        f"  samples     : {samp_str}",
        f"  observed cells : {artifact['n_observations']}",
        f"  legend : fitness {_SHADES[0]} low → {_SHADES[-1]} high   {_UNMEASURED} not measured",
    ]

    header_pad = " " * (label_w + 3)
    lines.append("")
    lines.append(header_pad + "hardest ──── sample_id ────→ easiest")
    lines.append(header_pad + "".join(f"{(sid // 10) % 10:>{cell_w}d}" for sid in sample_order))
    lines.append(header_pad + "".join(f"{sid % 10:>{cell_w}d}" for sid in sample_order))

    for cid in candidate_order:
        row_cells = []
        for sid in sample_order:
            fitness = cells.get((cid, sid))
            row_cells.append(_UNMEASURED * cell_w if fitness is None else _shade(fitness) * cell_w)
        t = theta.get(cid)
        theta_str = f"{t:>+5.2f}" if isinstance(t, (int, float)) else "  ?  "
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {theta_str}  {''.join(row_cells)}")

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


__all__ = ["render_hard_sample_heatmap"]
