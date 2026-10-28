from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from promptpotter.domain.origin_panel import LineRate, overlap_line, triggered_rates
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.arm_reading import ArmReading
    from promptpotter.domain.origin_panel import OverlapReading
    from promptpotter.domain.paired_reading import Coverage
    from promptpotter.domain.pipeline_schema import PipelineSchema

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def fmt_coverage(coverage: Coverage) -> str:
    """Dropped cells by cause: an ungraded one is the formula's to fix, an errored one the backend's."""
    dropped = ", ".join(
        f"{n} {cause}"
        for n, cause in (
            (coverage.excluded_unscored, "ungraded"),
            (coverage.excluded_faulted, "errored"),
        )
        if n
    )
    return f"{coverage.scored} shared" + (f" ({dropped} dropped)" if dropped else "")


def overlap_series(overlap: OverlapReading) -> str:
    line = overlap_line(overlap)
    if not line:
        return ""
    triggered = triggered_rates(overlap)

    def rate(m: LineRate) -> str:
        return f"{m.arm.label} {m.rate:.1%} (n={m.n})"

    lead = overlap.lead
    readings = (*overlap.earlier, lead, *(t.reading for t in overlap.triggered))
    bought = sum(
        member.bought
        for member in (lead.a, *(reading.b for reading in readings))
        if member is not None
    )
    paid = f", +{bought} measured" if bought else ""
    arms = "  →  ".join(map(rate, line))
    side = f"  ·  triggered: {', '.join(map(rate, triggered))}" if triggered else ""
    return f"origin panel of {len(overlap.positions)}{paid}: {arms}{side}"


def fmt_ci(lower: float | None, upper: float | None, *, spec: str) -> str:
    if lower is None or upper is None:
        return "—"
    return f"[{spec.format(lower)}, {spec.format(upper)}]"


@shapes_optimizer_prompt
def fmt_pct(x: float | None, spec: str = "{:.1%}") -> str:
    return "—" if x is None else spec.format(x)


def fmt_fitness(score: float | None) -> str:
    return "—" if score is None else f"{score:.4f}"


def fmt_pvalue(p: float | None) -> str:
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


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

BOX_WIDTH = 70
NODE_FRAME_WIDTH = 74
_W = BOX_WIDTH
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


def _scoreboard(ranked: Sequence[ArmReading]) -> str:
    """In the round's own rank; Δ is blank where a row has no matched floor."""
    if not ranked:
        return ""
    w = 108

    hdr = (
        f"{'#':<4s}{'Label':<8s}{'Cells':>7s}   {'Accuracy':>8s}   {'95% CI':>16s}   "
        f"{'Composite':>9s}   {'Ability θ':>9s}   {'Delta':>7s}"
    )
    lines = [f"  {_box_top('SCOREBOARD', width=w)}", f"  {_box_line(hdr, width=w)}"]

    for i, s in enumerate(ranked, 1):
        label = s.arm.label[:8]
        accuracy = None if s.own is None else s.own.accuracy
        composite = None if s.own is None else s.own.composite
        ci_str = fmt_ci(
            None if accuracy is None else accuracy.ci_lo,
            None if accuracy is None else accuracy.ci_hi,
            spec="{:.1%}",
        )
        lift = s.vs_reference.on_whole_set if s.vs_reference else None
        delta = None if lift is None else lift.estimate.value
        delta_str = f"{delta:+.1%}" if delta is not None and abs(delta) >= 0.001 else "---"
        if s.state == "cut":
            winner_mark = f"  {YELLOW}({s.end}){RESET}"
        elif s.election.selected:
            winner_mark = f"  {GREEN}{BOLD}*{RESET}"
        else:
            winner_mark = ""
        theta = None if s.ability is None else s.ability.theta
        theta_str = "---" if theta is None else f"{theta:+.3f}"
        cells = (
            f"{s.panel.scored}/{s.panel.expected}"
            if s.panel.expected and s.panel.scored is not None
            else str(0 if s.own is None else s.own.n)
        )
        acc_str = "—" if accuracy is None else f"{accuracy.value:.1%}"
        row = (
            f"{i:<4d}{label:<8s}{cells:>7s}   {acc_str:>8s}   {ci_str:>16s}   "
            f"{fmt_fitness(None if composite is None else composite.value):>9s}   "
            f"{theta_str:>9s}   {delta_str:>7s}{winner_mark}"
        )
        lines.append(f"  {_box_line(row, width=w)}")

    lines.append(f"  {_box_bottom(width=w)}")
    return "\n".join(lines)


# `ai` marks a node that OWNS a model (`is_llm`); an optimizer node owns none.
_KIND_TAGS: dict[NodeKind, str] = {
    NodeKind.RETRIEVER: "retr",
    NodeKind.TOOL: "tool",
    NodeKind.CACHE: "cach",
}


def display_tags(schema: PipelineSchema | None) -> dict[str, str]:
    if not schema:
        return {}
    base_tags: list[tuple[str, str]] = [
        # An UNDECLARED node has no kind to read a tag off, so it falls to its own initials.
        (
            n.name,
            "ai" if n.is_llm else (_KIND_TAGS.get(n.kind) if n.kind else None) or n.name[:4],
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


def render_pipeline_overlay(
    pipeline_params: dict[str, Any] | None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """A node absent from the schema shows every pair, not only its ``param_keys``."""
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
