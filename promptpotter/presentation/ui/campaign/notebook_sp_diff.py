"""SearchPoint diff state + 3-column flattened diff table for phase-handler display."""

from __future__ import annotations

from dataclasses import dataclass, field

from promptpotter.presentation.views.display_primitives import (
    CYAN,
    DIM,
    RESET,
    _node_line,
    _pp_val,
)

__all__ = [
    "_ABSENT",
    "_UNCHANGED",
    "_VAL_INLINE_MAX",
    "_CycleDisplayState",
    "_build_candidate_flat",
    "_flatten_sp_summary",
    "_group_diff_keys",
    "_print_sp_diff",
]


@dataclass
class _CycleDisplayState:
    """Mutable display state threaded through phase/callback closures (populated from PhaseEvents)."""

    max_rounds: int = 0
    patience: int = 0
    l1_stall_count: int = 0
    round_num: int = 0
    baseline_accuracy: float = 0.0
    baseline_total: int = 0
    candidates_meta: list = field(default_factory=list)
    n_scoring_queries: int = 0
    current_pipeline_params: dict | None = None
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    previous_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None


def _flatten_sp_summary(
    pp: dict | None,
) -> dict[str, str]:
    """Flatten SearchPoint dimensions into dot-notation display dict.

    - Scalar pipeline params: ``key`` → formatted value
    - JSON Schema params (type=object with properties): expand to
      ``key.field_name`` → description string
    - Mutation-tuple lists: expand ``['+', name, ...]`` to ``key.name`` → desc
    """
    flat: dict[str, str] = {}

    for k, v in (pp or {}).items():
        if k == "steps":
            continue
        if isinstance(v, dict) and v.get("type") == "object" and "properties" in v:
            for prop_name, prop_def in v["properties"].items():
                desc = prop_def.get("description", prop_def.get("type", "?"))
                flat[f"{k}.{prop_name}"] = desc
        elif isinstance(v, list) and v and isinstance(v[0], list):
            for mutation in v:
                if not mutation:
                    continue
                op = mutation[0]
                if op == "+" and len(mutation) >= 5:
                    flat[f"{k}.{mutation[1]}"] = mutation[4]
                elif op == "~" and len(mutation) >= 6:
                    flat[f"{k}.{mutation[2]}"] = mutation[5]
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                flat[sub_k] = _pp_val(sub_v)
        else:
            flat[k] = _pp_val(v)
    return flat


_ABSENT = "-"
_UNCHANGED = "·"  # middle dot
_VAL_INLINE_MAX = 12  # values longer than this get a lookup code


def _build_candidate_flat(
    parent: dict[str, str],
    candidate_meta: dict,
) -> dict[str, str]:
    """Merge candidate overrides onto parent flat dict.

    When a candidate overrides a schema key, parent's dot-notation
    children for that key are removed first, then the candidate's
    expanded fields are added. Prompt-field rewrites (persona,
    task_intent, …) ride on ``candidate_meta["prompt_fields"]`` and are
    overlaid as top-level keys.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = _flatten_sp_summary(pp)
        flat.update(override_flat)
    prompt_fields = candidate_meta.get("prompt_fields") or {}
    for field_name, value in prompt_fields.items():
        if value:
            flat[field_name] = str(value)
    return flat


def _group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group diff keys by pipeline node in execution order."""
    if not node_param_keys:
        return [("", diff_keys)]

    key_to_node: dict[str, str] = {}
    for sname, keys in node_param_keys.items():
        for k in keys:
            key_to_node[k] = sname
    for k in diff_keys:
        if k not in key_to_node:
            base = k.split(".")[0]
            if base in key_to_node:
                key_to_node[k] = key_to_node[base]

    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        sname = key_to_node.get(k, "")
        groups.setdefault(sname, []).append(k)

    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]


def _print_sp_diff(
    columns: list[tuple[str, dict[str, str]]],
    node_param_keys: dict[str, list[str]] | None = None,
    round_num: int | None = None,
) -> None:
    """Print N-column diff table with lookup codes for long values.

    Only rows where at least one column differs are shown. Short values
    (<=12 chars) are shown inline; longer values get a letter code
    ``[a]``..``[z]`` with full text in a legend below the table. When
    ``node_param_keys`` is provided, rows are grouped by pipeline node.
    """
    if len(columns) < 2:
        return

    all_keys: set[str] = set()
    for _, d in columns:
        all_keys.update(d.keys())
    diff_keys = []
    for k in sorted(all_keys):
        vals = [d.get(k) for _, d in columns]
        if len(set(vals)) > 1:
            diff_keys.append(k)
    if not diff_keys:
        return

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
    # Bump col_w to fit any inline value (<= _VAL_INLINE_MAX) so short-but-
    # wider-than-label values like "**{answer}**" don't overflow into the
    # next cell.
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

    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    print(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    print(_node_line(hdr))

    groups = _group_diff_keys(diff_keys, node_param_keys)
    for _gi, (node_name, group_keys) in enumerate(groups):
        if node_name and len(groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            print(_node_line(f"{DIM}{sep}{RESET}"))
        for k in group_keys:
            cells = []
            prev_val = None
            for _, d in columns:
                v = d.get(k)
                cells.append(_cell(v, prev_val))
                prev_val = v
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w}}" for c in cells)
            print(_node_line(row))

    if legend:
        print(_node_line(""))
        print(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            print(_node_line(f"  {code} {full}"))
