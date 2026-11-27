from __future__ import annotations

from collections.abc import Mapping, Sequence

from promptpotter.domain.cells import HardSampleOrder, HardSamples

# Graded, not binary: on a graded scorer every cell sits inside (0,1) and a split shades them alike.
_SHADES = ("░", "▒", "▓", "█")
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _shade(fitness: float) -> str:
    idx = min(int(fitness * len(_SHADES)), len(_SHADES) - 1)
    return _SHADES[idx]


def render_hard_sample_heatmap(
    view: HardSamples, queries: Mapping[str, str], order: HardSampleOrder, ranked: Sequence[str]
) -> list[str]:
    """*ranked* orders the LEADERBOARD only; the GRID stays δ-sorted whatever it says. *queries*
    is by ``Sample.key``, as the artifact holds its samples."""
    if not view.cells:
        return []

    candidate_order, sample_order = view.candidate_order, view.ranked_keys
    row = view.positions
    cells = {(c.candidate, c.sample_key): c.fitness for c in view.cells}
    fitted = view.ruler.state == "fitted"

    label_w = max(_HEATMAP_LABEL_W, min(24, max(len(c) for c in candidate_order)))
    cell_w = _HEATMAP_CELL_W

    lines = [
        "  HARD SAMPLES",
        f"  individuals : {len(candidate_order)}",
        f"  samples     : {len(sample_order)}",
        f"  observed cells : {len(view.cells)}",
        f"  legend : fitness {_SHADES[0]} low → {_SHADES[-1]} high   {_UNMEASURED} not measured",
    ]

    header_pad = " " * (label_w + 3)
    axis = "hardest ──── position ────→ easiest" if fitted else f"position ({view.ruler.label})"
    lines.append("")
    lines.append(header_pad + axis)
    lines.append(header_pad + "".join(f"{(row[k] // 10) % 10:>{cell_w}d}" for k in sample_order))
    lines.append(header_pad + "".join(f"{row[k] % 10:>{cell_w}d}" for k in sample_order))

    for cid in candidate_order:
        row_cells = []
        for key in sample_order:
            fitness = cells.get((cid, key))
            row_cells.append(_UNMEASURED * cell_w if fitness is None else _shade(fitness) * cell_w)
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {''.join(row_cells)}")

    lines.append("")
    if not fitted:
        lines.append(f"  {view.ruler.label}")
        return lines

    by_gain = order == "info_gain"
    title, key_label = (
        ("Info-gain leaderboard", "info gain") if by_gain else ("Hardness leaderboard", "delta")
    )
    keyed = (
        (key, on.pick_score if by_gain else on.delta, on.delta_se)
        for key in ranked
        for on in (view.samples[key],)
    )
    top = [
        (key, value, delta_se)
        for key, value, delta_se in keyed
        if value is not None and delta_se is not None
    ][:10]
    if top:
        lines.append(f"  {title} (top {len(top)})")
        lines.append(f"    {'position':>10s} {key_label:>10s} {'delta_se':>10s}  query")
        lines.append(f"    {'-' * 10} {'-' * 10} {'-' * 10}  {'-' * 40}")
        for key, value, delta_se in top:
            query = queries.get(key, "")[:40]
            lines.append(f"    {row[key]:>10d} {value:>+10.3f} {delta_se:>10.3f}  {query}")

    off = [key for key in sample_order if view.samples[key].delta is None]
    if off:
        lines.append(f"  {view.samples[off[0]].label}: {', '.join(str(row[k]) for k in off)}")

    return lines
