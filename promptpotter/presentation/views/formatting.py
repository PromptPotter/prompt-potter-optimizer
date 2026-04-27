"""Live-display formatting helpers shared across views.

Two markdown / box helpers consumed by ``live.py``, ``reports.py``, and the
notebook ↔ Claude exchange channel; plus a re-export of the ``fmt_*``
numeric formatters from ``display_primitives`` so callers have a single
import surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.presentation.views.display_primitives import (
    DIM,
    RESET,
    _box_bottom,
    _box_line,
    _box_top,
    fmt_ci,
    fmt_pct,
    fmt_pvalue,
)

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "fmt_ci",
    "fmt_pct",
    "fmt_pvalue",
    "render_markdown_box",
    "render_pipeline_overrides",
]


def render_markdown_box(title: str, content: str, empty_label: str, *, width: int = 74) -> str:
    """Render a titled box around ``content``, or a dim empty label."""
    if not content:
        return f"  {DIM}{empty_label}{RESET}"
    out = [f"  {_box_top(title, width=width)}"]
    for line in content.split("\n"):
        out.append(f"  {_box_line(line, width=width)}")
    out.append(f"  {_box_bottom(width=width)}")
    return "\n".join(out)


def render_pipeline_overrides(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render ``pipeline_params`` as a copy-paste-ready ``pipeline_overrides`` block.

    Nested format ``{"node_name": {"param": value}}``.  When ``pipeline_schema``
    is given, only keys listed in each node's ``param_keys`` are shown; nodes
    without a schema entry fall back to all key/value pairs.  Returns an empty
    string when there is nothing to render.
    """
    if not pipeline_params:
        return ""

    node_entries: list[tuple[str, dict]] = []
    for key, val in pipeline_params.items():
        if key == "steps" or not isinstance(val, dict):
            continue
        tunable: dict = {}
        if pipeline_schema:
            node = pipeline_schema.get_node(key)
            if node:
                tunable = {k: v for k, v in val.items() if k in node.param_keys}
        if not tunable:
            tunable = val
        if tunable:
            node_entries.append((key, tunable))

    if not node_entries:
        return ""

    rule = "─" * 60
    parts = [
        "  Copy-paste pipeline_overrides:",
        f"  {rule}",
        '  "pipeline_overrides": {',
    ]
    for node_name, params in node_entries:
        parts.append(f'      "{node_name}": {{')
        for param, val in params.items():
            parts.append(f'          "{param}": {val!r},')
        parts.append("      },")
    parts.append("  }")
    parts.append(f"  {rule}")
    return "\n".join(parts)
