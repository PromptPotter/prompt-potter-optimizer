"""Live display primitives — pure formatting, zero business logic.

ANSI color codes, box-drawing helpers, scoreboard, interrupt banner,
display-tag module state, and small numeric helpers (``_pp_val``,
``_fmt_delta``). Higher-level renderers consuming these primitives live
in ``views/round_render.py`` (per-query / per-candidate / per-round
output). The tqdm bar tracker lives inline in ``live.py``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def fmt_pct(value: Any) -> str:
    """Render a fraction in [0, 1] as ``"XX.X%"``; non-numerics → ``"-"``."""
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "-"


def fmt_ci(lower: float, upper: float) -> str:
    """Format a 95% CI bracket: ``[X.X%-Y.Y%]``."""
    return f"[{lower:.1%}-{upper:.1%}]"


def fmt_pvalue(p: float) -> str:
    """Format a p-value with significance tier (***, **, *, ns)."""
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"


def _visible_len(text: str) -> int:
    """Length of text after stripping ANSI escape codes."""
    return len(_ANSI_RE.sub("", text))


def _truncate_visible(text: str, max_visible: int) -> str:
    """Truncate ``text`` to at most ``max_visible`` visible chars, preserving
    ANSI escape sequences and appending RESET if any sequence was emitted."""
    if max_visible <= 0:
        return ""
    out: list[str] = []
    visible = 0
    saw_ansi = False
    i = 0
    n = len(text)
    while i < n and visible < max_visible:
        if text[i] == "\033" and i + 1 < n and text[i + 1] == "[":
            j = text.find("m", i + 2)
            if j != -1:
                out.append(text[i : j + 1])
                saw_ansi = True
                i = j + 1
                continue
        out.append(text[i])
        visible += 1
        i += 1
    if saw_ansi:
        out.append("\033[0m")
    return "".join(out)


# ANSI foreground colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

# Display geometry — single source of truth for terminal widths
BOX_WIDTH = 70  # standard box width
NODE_FRAME_WIDTH = 74  # node frame width (phase display)
_W = BOX_WIDTH  # internal alias
_NW = NODE_FRAME_WIDTH


def _h_rule_labeled(
    lc: str,
    rc: str,
    label: str = "",
    label_right: str = "",
    *,
    width: int = _W,
    fill: str = "─",
) -> str:
    """Horizontal rule with embedded labels: ``LC─ label ──── label_right ─RC``."""
    inner = width - 4
    left = f" {label} " if label else ""
    right = f" {label_right} " if label_right else ""
    pad = inner - _visible_len(left) - _visible_len(right)
    return f"{lc}{fill}{left}{fill * max(pad, 1)}{right}{fill}{rc}"


def _h_rule(lc: str, rc: str, *, width: int = _W, fill: str = "─") -> str:
    """Plain horizontal rule: ``LC───...───RC``."""
    return f"{lc}{fill * (width - 2)}{rc}"


def _h_text(lw: str, rw: str, text: str, *, width: int = _W) -> str:
    """Content line walled with ``LW`` / ``RW``: ``LW  text ...  RW``.

    Truncates with ``…`` when ``text`` exceeds inner width so the closing
    wall always lands at column ``width - 1``.
    """
    inner = width - 4
    vis = _visible_len(text)
    if vis > inner:
        text = _truncate_visible(text, inner - 1) + "…"
        vis = inner
    pad = inner - vis
    return f"{lw}  {text}{' ' * pad}{rw}"


def _box_top(label: str = "", label_right: str = "", *, width: int = _W) -> str:
    return _h_rule_labeled("┌", "┐", label, label_right, width=width)


def _box_bottom(width: int = _W) -> str:
    return _h_rule("└", "┘", width=width)


def _box_bottom_info(text: str, width: int = _W) -> str:
    return _h_rule_labeled("└", "┘", text, width=width)


def _box_line(text: str, width: int = _W) -> str:
    return _h_text("│", "│", text, width=width)


def _fmt_delta(val: float) -> str:
    """Format accuracy delta with color: green positive, red negative, yellow zero."""
    if abs(val) < 0.001:
        return f"{YELLOW}+0.0%{RESET}"
    color = GREEN if val > 0 else RED
    return f"{color}{val:+.1%}{RESET}"


def _node_top(label: str, label_right: str = "", width: int = _NW) -> str:
    return _h_rule_labeled("├", "┤", label, label_right, width=width)


def _node_bottom(width: int = _NW) -> str:
    return _h_rule("├", "┤", width=width)


def _node_line(text: str) -> str:
    return f"│  {text}"


def _node_block(label: str, *lines: str, label_right: str = "") -> str:
    """Render a node block: ``├─ LABEL ─┤`` + content lines + ``├──┤``."""
    parts = [_node_top(label, label_right)]
    parts.extend(_node_line(line) for line in lines)
    parts.append(_node_bottom())
    return "\n".join(parts)


def _dbox_block(title: str, *lines: str) -> str:
    """Render a double-box block with title header and content lines."""

    def line(text: str) -> str:
        return _h_text("║", "║", text, width=_W)

    parts = [_h_rule("╔", "╗", width=_W, fill="═"), line(title)]
    parts.append(_h_rule("╠", "╣", width=_W, fill="═"))
    parts.extend(line(t) for t in lines)
    parts.append(_h_rule("╚", "╝", width=_W, fill="═"))
    return "\n".join(parts)


def _round_rule(label: str, label_right: str = "", width: int = _NW) -> str:
    """Heavy round separator with labels.

    Returns a 3-line string: heavy rule, label line, heavy rule.
    """
    rule = "━" * width
    inner = f"  {label}"
    if label_right:
        pad = width - len(inner) - len(label_right) - 2
        inner = f"{inner}{' ' * max(pad, 2)}{label_right}"
    return f"{rule}\n{inner}\n{rule}"


def _pp_val(v: object) -> str:
    """Format a pipeline param value for display (no truncation)."""
    return f"{v:g}" if isinstance(v, float) else str(v)


def _scoreboard(
    candidate_scores: list[dict],
    winner_label: str,
    baseline_accuracy: float,
) -> str:
    """Format ranked candidate scoreboard as a box with 95% CI.

    ``candidate_scores`` items: {candidate_id, accuracy, composite_fitness?, hits, total}.
    Returns multi-line string ready to print.
    """
    if not candidate_scores:
        return ""

    ranked = sorted(
        candidate_scores,
        key=lambda s: (s.get("composite_fitness", s["accuracy"]), s["accuracy"]),
        reverse=True,
    )
    w = 78

    hdr = (
        f"{'#':<4s}{'Label':<8s}{'Accuracy':>9s}  {'95% CI':>16s}  {'Composite':>9s}  {'Delta':>7s}"
    )
    lines = [f"  {_box_top('SCOREBOARD', width=w)}", f"  {_box_line(hdr, width=w)}"]

    for i, s in enumerate(ranked, 1):
        label = s.get("label", f"C{i}")[:8]
        acc = s["accuracy"]
        ci_str = fmt_ci(*wilson_ci(s.get("hits", 0), s.get("total", 0)))
        delta = acc - baseline_accuracy
        delta_str = f"{delta:+.1%}" if abs(delta) >= 0.001 else "---"
        aborted = s.get("escalation_aborted", False)
        if aborted:
            winner_mark = f"  {YELLOW}(aborted){RESET}"
        elif label == winner_label:
            winner_mark = f"  {GREEN}{BOLD}*{RESET}"
        else:
            winner_mark = ""
        comp_val = s.get("composite_fitness") if s.get("composite_fitness") is not None else acc
        comp_part = f"   {comp_val:>8.4f}"
        row = f"{i:<4d}{label:<8s}{acc:>8.1%}   {ci_str:>16s}{comp_part}   {delta_str:>7s}{winner_mark}"
        lines.append(f"  {_box_line(row, width=w)}")

    lines.append(f"  {_box_bottom(width=w)}")
    return "\n".join(lines)


# Display tags — populated from _build_display_tags() at init.
# Mutated in place by set_display_tags so importers can keep a stable
# reference (``from .display import DISPLAY_TAGS``).
DISPLAY_TAGS: dict[str, str] = {}

_WIRE_TYPE_TAGS: dict[str, str] = {
    "generation": "ai",
    "retriever": "retr",
    "tool": "tool",
    "cache": "cach",
}


def _build_display_tags(schema) -> dict[str, str]:
    """Compute ``{node_name: tag}`` with auto-enumeration for duplicates.

    Resolution: ``_WIRE_TYPE_TAGS[node.wire_type]`` → ``node.name[:4]``.
    """
    from collections import Counter

    base_tags: list[tuple[str, str]] = [
        (n.name, _WIRE_TYPE_TAGS.get(n.wire_type, "") or n.name[:4]) for n in schema.nodes
    ]
    tag_counts = Counter(tag for _, tag in base_tags)
    tag_seq: dict[str, int] = {}
    result: dict[str, str] = {}
    for name, tag in base_tags:
        if tag_counts[tag] > 1:
            tag_seq[tag] = tag_seq.get(tag, 0) + 1
            result[name] = f"{tag}_{tag_seq[tag]}"
        else:
            result[name] = tag
    return result


def set_display_tags(schema) -> None:
    """Set display tags from a PipelineSchema. Call once at pipeline init.

    Mutates ``DISPLAY_TAGS`` in place so other modules importing it keep
    a live reference.
    """
    DISPLAY_TAGS.clear()
    if schema:
        DISPLAY_TAGS.update(_build_display_tags(schema))


def _step_tag(step_name: str | None) -> str:
    if step_name is None:
        return ""
    return f"[{DISPLAY_TAGS.get(step_name, step_name[:4])}]"


# ===========================================================================
# Live-display formatting helpers shared across views.
# Markdown/box helpers consumed by ``live.py``, ``reports.py``, and the
# notebook ↔ Claude exchange channel; plus the ``fmt_*`` numeric formatters
# (``fmt_pct`` / ``fmt_ci`` / ``fmt_pvalue``) — single import surface.
# ===========================================================================

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
