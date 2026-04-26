"""SearchPoint diff view-model — flatten/build/group helpers for renderer consumption.

Application layer because it consumes ``PipelineSchema`` and pipeline-param
shape; the renderer in ``presentation/views/sp_diff.py`` takes the already-
flattened columns and emits the N-column ANSI table. This split keeps
display pure and lets a future webapp consume the same flat dicts.
"""

from __future__ import annotations

__all__ = [
    "build_candidate_flat",
    "flatten_sp_summary",
    "group_diff_keys",
]


def _fmt_pp_val(v: object) -> str:
    """Format a pipeline param value for display. No truncation."""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: dict | None) -> dict[str, str]:
    """Flatten SearchPoint dimensions into dot-notation display dict.

    - Scalar pipeline params: ``key`` -> formatted value
    - JSON Schema params (type=object with properties): expand to
      ``key.field_name`` -> description string
    - Mutation-tuple lists: expand ``['+', name, ...]`` to ``key.name`` -> desc
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
                flat[sub_k] = _fmt_pp_val(sub_v)
        else:
            flat[k] = _fmt_pp_val(v)
    return flat


def build_candidate_flat(parent: dict[str, str], candidate_meta: dict) -> dict[str, str]:
    """Merge candidate overrides onto parent flat dict.

    When a candidate overrides a schema key, parent's dot-notation
    children for that key are removed first, then the candidate's
    expanded fields are added. Prompt-field rewrites ride on
    ``candidate_meta["prompt_fields"]`` and overlay as top-level keys.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = flatten_sp_summary(pp)
        flat.update(override_flat)
    prompt_fields = candidate_meta.get("prompt_fields") or {}
    for field_name, value in prompt_fields.items():
        if value:
            flat[field_name] = str(value)
    return flat


def group_diff_keys(
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
