"""SearchPoint-diff table renderer — internal helper for ``candidates_generated``.

Two-column-minimum diff over per-candidate flat param dicts: collapse
unchanged values to ``·``, inline short values, code long values into a
legend. Used only by :func:`render_candidates_generated`.
"""

from __future__ import annotations

from promptpotter.domain.opt_search_point import group_diff_keys
from promptpotter.presentation.views.display import CYAN, DIM, RESET, YELLOW, _node_line
from promptpotter.presentation.views.view_models import SpDiffView

_SP_DIFF_ABSENT = "-"
_SP_DIFF_UNCHANGED = "·"
_SP_DIFF_VAL_INLINE_MAX = 12


def render_sp_diff(view: SpDiffView) -> str:
    columns_in = list(view.columns)
    if len(columns_in) < 2:
        return ""

    clone_labels = set(view.clone_labels)
    columns: list[tuple[str, dict[str, str]]] = [
        (
            f"{label}[clone]" if label in clone_labels else label,
            flat,
        )
        for label, flat in columns_in
    ]
    node_param_keys = view.node_param_keys
    round_num = view.round_num
    n_no_op = view.l1_n_no_op
    n_duplicate = view.l1_n_duplicate
    l1_yield = view.l1_yield

    warning_lines: list[str] = []
    if n_no_op or n_duplicate:
        n_total = sum(1 for label, _ in columns_in if label.startswith("C"))
        n_valid = max(0, n_total - n_no_op - n_duplicate)
        bits: list[str] = []
        if n_no_op:
            bits.append(f"{n_no_op} no-op")
        if n_duplicate:
            bits.append(f"{n_duplicate} duplicate")
        bits_text = " / ".join(bits)
        cl_text = f" ({', '.join(sorted(clone_labels))})" if clone_labels else ""
        warning_lines.append(
            _node_line(
                f"{YELLOW}⚠ L1 produced {bits_text} variant(s){cl_text} — "
                f"synthetic-zeroed (no API cost). yield={l1_yield:.0%} "
                f"({n_valid}/{n_total} valid).{RESET}"
            )
        )

    all_keys = {k for _, d in columns for k in d}
    diff_keys = sorted(k for k in all_keys if len({d.get(k) for _, d in columns}) > 1)
    if not diff_keys:
        return "\n".join(warning_lines) if warning_lines else ""

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
            return _SP_DIFF_ABSENT
        if val == prior:
            return _SP_DIFF_UNCHANGED
        if len(val) <= _SP_DIFF_VAL_INLINE_MAX:
            return val
        return _get_code(val)

    max_key = max(len(k) for k in diff_keys)

    groups = group_diff_keys(diff_keys, node_param_keys)
    rendered_groups: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for node_name, group_keys in groups:
        rows: list[tuple[str, list[str]]] = []
        for k in group_keys:
            cells: list[str] = []
            start_val = columns[0][1].get(k) if columns else None
            parent_val = columns[1][1].get(k) if len(columns) > 1 else None
            for ci, (_, d) in enumerate(columns):
                v = d.get(k)
                if ci == 0:
                    prior: str | None = None
                elif ci == 1:
                    prior = start_val
                else:
                    prior = parent_val
                cells.append(_cell(v, prior))
            rows.append((k, cells))
        rendered_groups.append((node_name, rows))

    n_cols = len(columns)
    col_w: list[int] = []
    for ci in range(n_cols):
        label_w = len(columns[ci][0])
        cell_w = max(
            (len(cells[ci]) for _, rows in rendered_groups for _, cells in rows),
            default=0,
        )
        col_w.append(max(label_w, cell_w) + 2)

    out: list[str] = list(warning_lines)
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    out.append(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(
        f"{label:<{col_w[ci]}}" for ci, (label, _) in enumerate(columns)
    )
    out.append(_node_line(hdr))

    for node_name, rows in rendered_groups:
        if node_name and len(rendered_groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k, cells in rows:
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w[ci]}}" for ci, c in enumerate(cells))
            out.append(_node_line(row))

    if legend:
        out.append(_node_line(""))
        out.append(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            out.append(_node_line(f"  {code} {full}"))

    return "\n".join(out)


__all__ = ["render_sp_diff"]
