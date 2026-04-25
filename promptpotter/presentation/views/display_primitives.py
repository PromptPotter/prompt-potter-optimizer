"""Live display primitives — pure formatting, zero business logic.

ANSI color codes, box-drawing helpers, scoreboard, interrupt banner,
display-tag module state, and small numeric helpers (``_pp_val``,
``_fmt_delta``). Higher-level renderers consuming these primitives live
in sibling modules:

- ``query_format._fmt_query_result`` — per-query HIT/MISS line
- ``candidate_view`` — candidate header + summary card
- ``round_summary`` — round trajectory + stats + patience

Depends only on ``shared`` utilities and ``views.formatting`` (for
``fmt_ci``).
"""

from __future__ import annotations

import re

from promptpotter.shared.statistics import wilson_ci

from .formatting import fmt_ci

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text: str) -> int:
    """Length of text after stripping ANSI escape codes."""
    return len(_ANSI_RE.sub("", text))


# ANSI foreground colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

# Display geometry — single source of truth for terminal widths
BOX_WIDTH = 70  # standard box width
NODE_FRAME_WIDTH = 74  # node frame width (phase display)
_W = BOX_WIDTH  # internal alias
_NW = NODE_FRAME_WIDTH


def _box_top(label: str = "", label_right: str = "", width: int = _W) -> str:
    """Single-line top: ``┌─ label ───── label_right ─┐``."""
    inner = width - 4  # minus ┌─ prefix and ─┐ suffix
    left = f" {label} " if label else ""
    right = f" {label_right} " if label_right else ""
    fill = inner - len(left) - len(right)
    return f"┌─{left}{'─' * max(fill, 1)}{right}─┐"


def _box_bottom(width: int = _W) -> str:
    """Single-line bottom: ``└───...───┘``."""
    return f"└{'─' * (width - 2)}┘"


def _box_bottom_info(text: str, width: int = _W) -> str:
    """Bottom frame with embedded text: ``└─ text ───...───┘``."""
    inner = width - 4
    label = f" {text} " if text else ""
    fill = max(inner - _visible_len(label), 0)
    return f"└─{label}{'─' * fill}─┘"


def _box_line(text: str, width: int = _W) -> str:
    """Single-line content: ``│  text ...  │``."""
    inner = width - 4
    pad = max(inner - _visible_len(text), 0)
    return f"│  {text}{' ' * pad}│"


def _dbox_top(width: int = _W) -> str:
    """Double-line top: ``╔═══...═══╗``."""
    return f"╔{'═' * (width - 2)}╗"


def _dbox_bottom(width: int = _W) -> str:
    """Double-line bottom: ``╚═══...═══╝``."""
    return f"╚{'═' * (width - 2)}╝"


def _dbox_sep(width: int = _W) -> str:
    """Double-line separator: ``╠═══...═══╣``."""
    return f"╠{'═' * (width - 2)}╣"


def _dbox_line(text: str, width: int = _W) -> str:
    """Double-line content: ``║  text ...  ║``."""
    inner = width - 4
    pad = max(inner - _visible_len(text), 0)
    return f"║  {text}{' ' * pad}║"


def _dotted_line(label: str = "", width: int = _W) -> str:
    """Dotted separator: ``┄┄┄ label ┄┄┄...┄┄┄``."""
    if label:
        pad = width - len(label) - 2
        left = pad // 2
        right = pad - left
        return f"{'┄' * left} {label} {'┄' * right}"
    return "┄" * width


def _fmt_delta(val: float) -> str:
    """Format accuracy delta with color: green positive, red negative, yellow zero."""
    if abs(val) < 0.001:
        return f"{YELLOW}+0.0%{RESET}"
    if val > 0:
        return f"{GREEN}{val:+.1%}{RESET}"
    return f"{RED}{val:+.1%}{RESET}"


def _node_top(label: str, label_right: str = "", width: int = _NW) -> str:
    """Node entry banner: ``├─ LABEL ──────── label_right ─┤``."""
    inner = width - 4
    left = f" {label} " if label else ""
    right = f" {label_right} " if label_right else ""
    fill = inner - len(left) - len(right)
    return f"├─{left}{'─' * max(fill, 1)}{right}─┤"


def _node_bottom(width: int = _NW) -> str:
    """Node closing rule: ``├──────...──────┤``."""
    return f"├{'─' * (width - 2)}┤"


def _node_line(text: str) -> str:
    """Indented node content: ``│  text``."""
    return f"│  {text}"


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


def _pp_val(v) -> str:
    """Format a pipeline param value for display.

    No truncation — callers decide when to use legend codes instead.
    """
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (dict, list)):
        return str(v)
    return str(v)


def _scoreboard(
    candidate_scores: list[dict],
    winner_label: str,
    baseline_accuracy: float,
) -> str:
    """Format ranked candidate scoreboard as a box with 95% CI.

    ``candidate_scores`` items: {candidate_id, accuracy, composite?, hits, total}.
    Returns multi-line string ready to print.
    """
    if not candidate_scores:
        return ""

    def _sort_key(s):
        return (s.get("composite", s["accuracy"]), s["accuracy"])

    ranked = sorted(candidate_scores, key=_sort_key, reverse=True)
    w = 78

    lines = []
    lines.append(f"  {_box_top('SCOREBOARD', width=w)}")

    has_composite = any(
        s.get("composite") is not None and s.get("composite") != s["accuracy"] for s in ranked
    )
    if has_composite:
        hdr = (
            f"{'#':<4s}{'Label':<8s}{'Accuracy':>9s}  {'95% CI':>16s}"
            f"  {'Composite':>9s}  {'Delta':>7s}"
        )
    else:
        hdr = f"{'#':<4s}{'Label':<8s}{'Accuracy':>9s}  {'95% CI':>16s}  {'Delta':>7s}"
    lines.append(f"  {_box_line(hdr, width=w)}")

    for i, s in enumerate(ranked, 1):
        label = s.get("label", f"C{i}")[:8]
        acc = s["accuracy"]
        hits = s.get("hits", 0)
        total = s.get("total", 0)
        ci_lo, ci_hi = wilson_ci(hits, total)
        ci_str = fmt_ci(ci_lo, ci_hi)
        delta = acc - baseline_accuracy
        delta_str = f"{delta:+.1%}" if abs(delta) >= 0.001 else "---"
        aborted = s.get("escalation_aborted", False)
        is_winner = label == winner_label and not aborted
        winner_mark = f"  {GREEN}{BOLD}*{RESET}" if is_winner else ""
        if aborted:
            winner_mark = f"  {YELLOW}(aborted){RESET}"

        if has_composite:
            comp = s.get("composite", acc)
            row = (
                f"{i:<4d}{label:<8s}{acc:>8.1%}   {ci_str:>16s}"
                f"   {comp:>8.4f}   {delta_str:>7s}{winner_mark}"
            )
        else:
            row = f"{i:<4d}{label:<8s}{acc:>8.1%}   {ci_str:>16s}   {delta_str:>7s}{winner_mark}"

        lines.append(f"  {_box_line(row, width=w)}")

    lines.append(f"  {_box_bottom(width=w)}")
    return "\n".join(lines)


def _print_interrupt_banner(
    operation: str,
    *,
    completed: str = "",
    saved: str = "",
    resume_hint: str = "",
) -> None:
    """Print a consistent [INTERRUPTED] banner for notebook cell interrupts."""
    print(f"\n{'=' * 70}")
    print(f"  {YELLOW}{BOLD}[INTERRUPTED]{RESET} {operation}")
    if completed:
        print(f"  Completed: {completed}")
    if saved:
        print(f"  Saved: {saved}")
    if resume_hint:
        print(f"  Resume: {resume_hint}")
    print(f"{'=' * 70}")


# Display tags — populated from _build_display_tags() at init.
# Mutated in place by set_display_tags so importers can keep a stable
# reference (``from .display_primitives import _DISPLAY_TAGS``).
_DISPLAY_TAGS: dict[str, str] = {}

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

    Mutates ``_DISPLAY_TAGS`` in place so other modules importing it keep
    a live reference.
    """
    _DISPLAY_TAGS.clear()
    if schema:
        _DISPLAY_TAGS.update(_build_display_tags(schema))


def _step_tag(step_name: str | None) -> str:
    if step_name is None:
        return ""
    return f"[{_DISPLAY_TAGS.get(step_name, step_name[:4])}]"
