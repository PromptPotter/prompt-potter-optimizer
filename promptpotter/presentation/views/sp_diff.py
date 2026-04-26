"""SearchPoint diff renderer — pure function over the flattened columns dict.

Consumes the ``sp_diff`` block produced by
``application.campaign.phase_views`` (which calls
``application.optimization.sp_diff_view`` to flatten/build/group). Emits the
N-column diff table with a ``[a]..[z]`` legend for long values.
"""

from __future__ import annotations

from promptpotter.application.optimization.sp_diff_view import group_diff_keys
from promptpotter.presentation.views.display_primitives import (
    CYAN,
    DIM,
    RESET,
    _node_line,
)

__all__ = ["render_sp_diff"]


_ABSENT = "-"
_UNCHANGED = "·"  # middle dot
_VAL_INLINE_MAX = 12


def render_sp_diff(view: dict) -> str:
    """Render N-column diff table from a ``sp_diff`` view-dict.

    ``view`` shape: ``{"columns": [{"label": str, "flat": dict[str,str]}], ...
    "node_param_keys": dict | None, "round_num": int | None}``
    """
    columns_in = view.get("columns") or []
    if len(columns_in) < 2:
        return ""

    columns: list[tuple[str, dict[str, str]]] = [(c["label"], c["flat"]) for c in columns_in]
    node_param_keys = view.get("node_param_keys")
    round_num = view.get("round_num")

    all_keys: set[str] = set()
    for _, d in columns:
        all_keys.update(d.keys())
    diff_keys: list[str] = []
    for k in sorted(all_keys):
        vals = [d.get(k) for _, d in columns]
        if len(set(vals)) > 1:
            diff_keys.append(k)
    if not diff_keys:
        return ""

    lookup: dict[str, str] = {}
    legend: list[tuple[str, str]] = []
    code_idx = 0

    def _get_code(val: str) -> str:
        nonlocal code_idx
        if val in lookup:
            return lookup[val]
        code = chr(ord("a") + code_idx)
        code_idx += 1
        lookup[val] = f"[{code}]"
        legend.append((f"[{code}]", val))
        return f"[{code}]"

    def _cell(val: str | None, prior: str | None) -> str:
        if val is None:
            return _ABSENT
        if val == prior:
            return _UNCHANGED
        if len(val) <= _VAL_INLINE_MAX:
            return val
        return _get_code(val)

    col_w = max(8, max(len(label) for label, _ in columns) + 2)
    widest_inline = max(
        (
            len(d[k])
            for k in diff_keys
            for _, d in columns
            if k in d and len(d[k]) <= _VAL_INLINE_MAX
        ),
        default=0,
    )
    col_w = max(col_w, widest_inline + 2)
    max_key = max(len(k) for k in diff_keys)

    out: list[str] = []
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    out.append(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    out.append(_node_line(hdr))

    groups = group_diff_keys(diff_keys, node_param_keys)
    for node_name, group_keys in groups:
        if node_name and len(groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k in group_keys:
            cells: list[str] = []
            prev_val: str | None = None
            for _, d in columns:
                v = d.get(k)
                cells.append(_cell(v, prev_val))
                prev_val = v
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w}}" for c in cells)
            out.append(_node_line(row))

    if legend:
        out.append(_node_line(""))
        out.append(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            out.append(_node_line(f"  {code} {full}"))

    return "\n".join(out)
