from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.application.views.view_models import HardSamplesView

# Graded, not binary: on a graded scorer every cell sits inside (0,1) and a split shades them alike.
_SHADES = ("░", "▒", "▓", "█")
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _shade(fitness: float) -> str:
    idx = min(int(fitness * len(_SHADES)), len(_SHADES) - 1)
    return _SHADES[idx]


def render_hard_sample_heatmap(shown: HardSamplesView) -> str:
    """``shown.ranked`` orders the LEADERBOARD only; the GRID stays δ-sorted whatever it says."""
    view = shown.artifact
    if not view.cells:
        return ""

    candidate_order, sample_order = view.candidate_order, view.sample_order
    cells = {(c.candidate, c.sample_id): c.fitness for c in view.cells}
    fitted = view.ruler.state == "fitted"

    label_w = max(_HEATMAP_LABEL_W, min(24, max(len(c) for c in candidate_order)))
    cell_w = _HEATMAP_CELL_W

    lines = [
        f"  individuals : {len(candidate_order)}",
        f"  samples     : {len(sample_order)}",
        f"  observed cells : {len(view.cells)}",
        f"  legend : fitness {_SHADES[0]} low → {_SHADES[-1]} high   {_UNMEASURED} not measured",
    ]

    header_pad = " " * (label_w + 3)
    axis = "hardest ──── sample_id ────→ easiest" if fitted else f"sample_id ({view.ruler.label})"
    lines.append("")
    lines.append(header_pad + axis)
    lines.append(header_pad + "".join(f"{(sid // 10) % 10:>{cell_w}d}" for sid in sample_order))
    lines.append(header_pad + "".join(f"{sid % 10:>{cell_w}d}" for sid in sample_order))

    for cid in candidate_order:
        row_cells = []
        for sid in sample_order:
            fitness = cells.get((cid, sid))
            row_cells.append(_UNMEASURED * cell_w if fitness is None else _shade(fitness) * cell_w)
        theta_str = f"{view.theta[cid]:>+5.2f}" if cid in view.theta else " " * 5
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {theta_str}  {''.join(row_cells)}")

    lines.append("")
    if not fitted:
        lines.append(f"  {view.ruler.label}")
        return "\n".join(lines)

    by_gain = shown.order == "info_gain"
    title, key_label = (
        ("Info-gain leaderboard", "info gain") if by_gain else ("Hardness leaderboard", "delta")
    )
    keyed = (
        (sid, on.pick_score if by_gain else on.delta, on.delta_se)
        for sid in shown.ranked
        for on in (view.samples[sid],)
    )
    top = [
        (sid, key, delta_se)
        for sid, key, delta_se in keyed
        if key is not None and delta_se is not None
    ][:10]
    if top:
        lines.append(f"  {title} (top {len(top)})")
        lines.append(f"    {'sample_id':>10s} {key_label:>10s} {'delta_se':>10s}  query")
        lines.append(f"    {'-' * 10} {'-' * 10} {'-' * 10}  {'-' * 40}")
        for sid, key, delta_se in top:
            query = shown.sample_query_lookup.get(sid, "")[:40]
            lines.append(f"    {sid:>10d} {key:>+10.3f} {delta_se:>10.3f}  {query}")

    off = [sid for sid in sample_order if view.samples[sid].delta is None]
    if off:
        lines.append(f"  {view.samples[off[0]].label}: {', '.join(str(sid) for sid in off)}")

    return "\n".join(lines)


__all__ = ["render_hard_sample_heatmap"]
