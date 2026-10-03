"""Live display primitives — pure formatting, zero business logic."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import ArmOutcome, resolved_fitness, scoreboard_rank_key
from promptpotter.domain.wounds import INVARIANT_REASONS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.views.view_models import ScoreEntry
    from promptpotter.domain.pipeline_schema import PipelineSchema

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def fmt_ci(lower: float | None, upper: float | None, *, spec: str) -> str:
    """Format a 95% CI bracket, or ``—`` where no interval exists. *spec* formats each bound — a share
    reads as ``{:.1%}``, while seconds, dollars and a signed lift are none of those and read raw.

    An absent interval must READ as absent: ``[0.0%, 0.0%]`` is a fabricated bracket claiming
    certainty about a measurement that never happened."""
    if lower is None or upper is None:
        return "—"
    return f"[{spec.format(lower)}, {spec.format(upper)}]"


def fmt_pvalue(p: float | None) -> str:
    """``None`` is a test that never ran — below two pairs nothing was tested, and a ``p=1.00 (ns)``
    there would misreport that as a test which found nothing."""
    if p is None:
        return "—"
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _truncate_visible(text: str, max_visible: int) -> str:
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
    inner = width - 4
    left = f" {label} " if label else ""
    right = f" {label_right} " if label_right else ""
    pad = inner - _visible_len(left) - _visible_len(right)
    return f"{lc}{fill}{left}{fill * max(pad, 1)}{right}{fill}{rc}"


def _h_rule(lc: str, rc: str, *, width: int = _W, fill: str = "─") -> str:
    return f"{lc}{fill * (width - 2)}{rc}"


def _h_text(lw: str, rw: str, text: str, *, width: int = _W) -> str:
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
    parts = [_node_top(label, label_right)]
    parts.extend(_node_line(line) for line in lines)
    parts.append(_node_bottom())
    return "\n".join(parts)


def _dbox_block(title: str, *lines: str) -> str:
    def line(text: str) -> str:
        return _h_text("║", "║", text, width=_W)

    parts = [_h_rule("╔", "╗", width=_W, fill="═"), line(title)]
    parts.append(_h_rule("╠", "╣", width=_W, fill="═"))
    parts.extend(line(t) for t in lines)
    parts.append(_h_rule("╚", "╝", width=_W, fill="═"))
    return "\n".join(parts)


def _round_rule(label: str, label_right: str = "", width: int = _NW) -> str:
    rule = "━" * width
    inner = f"  {label}"
    if label_right:
        pad = width - len(inner) - len(label_right) - 2
        inner = f"{inner}{' ' * max(pad, 2)}{label_right}"
    return f"{rule}\n{inner}\n{rule}"


def _scoreboard(
    candidate_scores: Sequence[ScoreEntry],
    winner_label: str,
    *,
    theta: bool,
) -> str:
    """Δ is blank where a row has no matched floor — the full-set rate is a different basis,
    not a fallback. ``theta`` is the selector's own declaration that it fits one per arm."""
    # Filter synthetic-zeroed variants (no_op / duplicate) — they did not burn an LLM call
    # and ranking them as 0.0% delta distorts the verdict. The set is imported, never
    # re-spelled: it belongs to the validator that EMITS these reasons.
    scored = [s for s in candidate_scores if s.invalid_reason not in INVARIANT_REASONS]
    if not scored:
        return ""

    ranked = sorted(
        scored,
        key=lambda s: scoreboard_rank_key(
            s.composite_fitness,
            s.accuracy,
            s.theta,
            is_selected=s.label == winner_label,
            is_partial=s.outcome is ArmOutcome.SKIPPED,
        ),
        reverse=True,
    )
    w = 108

    # Column ORDER is the row's, and the two disagreed: the header named Composite before 95% CI
    # while the row printed them the other way round, so every CI was read against the wrong
    # column. The interval brackets mean per-cell fitness — accuracy's own fold — so it sits
    # beside Accuracy, and `Ability θ` closes the table with what a θ selector decides on.
    # `Cells` leads the numbers because it is their basis: every rate to its right is read over it.
    theta_hdr = f"   {'Ability θ':>9s}" if theta else ""
    hdr = (
        f"{'#':<4s}{'Label':<8s}{'Cells':>7s}   {'Accuracy':>8s}   {'95% CI':>16s}   "
        f"{'Composite':>9s}{theta_hdr}   {'Delta':>7s}"
    )
    lines = [f"  {_box_top('SCOREBOARD', width=w)}", f"  {_box_line(hdr, width=w)}"]

    for i, s in enumerate(ranked, 1):
        label = (s.label or "")[:8]
        acc = s.accuracy
        ci_str = fmt_ci(s.mean_fitness_ci_lo, s.mean_fitness_ci_hi, spec="{:.1%}")
        # A row whose matched floor genuinely scored 0.0 keeps its 0.0 — `or` cannot tell
        # that from absence.
        row_parent = s.reference_accuracy
        delta = acc - row_parent if row_parent is not None and acc is not None else None
        delta_str = f"{delta:+.1%}" if delta is not None and abs(delta) >= 0.001 else "---"
        if s.outcome.cut_short:
            winner_mark = f"  {YELLOW}({s.outcome}){RESET}"
        elif label == winner_label:
            winner_mark = f"  {GREEN}{BOLD}*{RESET}"
        else:
            winner_mark = ""
        comp_val = resolved_fitness(s.composite_fitness, acc)
        # "---", never "0.000": a candidate outside the election fit has no ability, and while the
        # ruler is cold NO row has one — a zero there would read as a measured mid-scale ability.
        theta_str = "---" if s.theta is None else f"{s.theta:+.3f}"
        theta_cell = f"   {theta_str:>9s}" if theta else ""
        cells = f"{s.scored}/{s.expected}" if s.expected else str(s.total)
        acc_str = "—" if acc is None else f"{acc:.1%}"
        row = (
            f"{i:<4d}{label:<8s}{cells:>7s}   {acc_str:>8s}   {ci_str:>16s}   "
            f"{comp_val:>9.4f}{theta_cell}   {delta_str:>7s}{winner_mark}"
        )
        lines.append(f"  {_box_line(row, width=w)}")

    lines.append(f"  {_box_bottom(width=w)}")
    return "\n".join(lines)


# `ai` marks a node that OWNS a model (`is_llm`, as `llm_only` does); an optimizer node, which
# owns none, reads better as `l1_g`/`l1_c` than as `ai_1`/`ai_2`.
_WIRE_TYPE_TAGS: dict[NodeKind, str] = {
    NodeKind.RETRIEVER: "retr",
    NodeKind.TOOL: "tool",
    NodeKind.CACHE: "cach",
}


def display_tags(schema: PipelineSchema | None) -> dict[str, str]:
    """Node name → the short tag a sample line prints it under; a run with no schema has none."""
    if not schema:
        return {}
    base_tags: list[tuple[str, str]] = [
        # An UNDECLARED node has no kind to read a tag off, so it falls to its own initials —
        # the same place a declared kind this map does not carry lands.
        (
            n.name,
            "ai"
            if n.is_llm
            else (_WIRE_TYPE_TAGS.get(n.wire_type) if n.wire_type else None) or n.name[:4],
        )
        for n in schema.nodes
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


def _step_tag(step_name: str | None, tags: dict[str, str]) -> str:
    if step_name is None:
        return ""
    return f"[{tags.get(step_name, step_name[:4])}]"


# ===========================================================================
# Live-display formatting helpers shared across views.
# Markdown/box helpers consumed by the readout and the notebook ↔ Claude exchange
# channel; plus the ``fmt_*`` numeric formatters
# (``fmt_ci`` / ``fmt_pvalue``) — single import surface.
# ===========================================================================

__all__ = [
    "fmt_ci",
    "fmt_pvalue",
    "render_pipeline_overlay",
]


def render_pipeline_overlay(
    pipeline_params: dict[str, Any] | None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render ``pipeline_params`` as a copy-paste-ready ``pipeline_overlay`` block. With a schema, only
    each node's ``param_keys`` are shown; a node absent from the schema falls back to every pair."""
    if not pipeline_params:
        return ""

    node_entries: list[tuple[str, dict[str, Any]]] = []
    for key, val in node_config_items(pipeline_params):
        tunable: dict[str, Any] = {}
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
        "  Copy-paste pipeline_overlay:",
        f"  {rule}",
        '  "pipeline_overlay": {',
    ]
    for node_name, params in node_entries:
        parts.append(f'      "{node_name}": {{')
        for param, val in params.items():
            parts.append(f'          "{param}": {val!r},')
        parts.append("      },")
    parts.append("  }")
    parts.append(f"  {rule}")
    return "\n".join(parts)
