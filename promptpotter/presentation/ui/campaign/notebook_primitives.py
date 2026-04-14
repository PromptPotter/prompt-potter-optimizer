"""Notebook display primitives — pure formatting, zero business logic.

ANSI color codes, box-drawing helpers, CI/p-value/delta formatters,
scoreboard, interrupt banner, display-tag module state, and per-query
result line formatter. Depends only on ``shared`` utilities; imported by
``notebook_phase`` and ``notebook_display``.
"""

from __future__ import annotations

import re

from promptpotter.shared.errors import is_error_result
from promptpotter.shared.scoring import extract_display_answer
from promptpotter.shared.statistics import wilson_ci


def fmt_ci(lower: float, upper: float) -> str:
    """Format a CI as '[X.X%-Y.Y%]'."""
    return f"[{lower:.1%}-{upper:.1%}]"


def fmt_pvalue(p: float) -> str:
    """Format a p-value with significance marker."""
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.3f} *"
    return f"p={p:.3f}"


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
    return f"\u250c\u2500{left}{'─' * max(fill, 1)}{right}\u2500\u2510"


def _box_bottom(width: int = _W) -> str:
    """Single-line bottom: ``└───...───┘``."""
    return f"\u2514{'─' * (width - 2)}\u2518"


def _box_bottom_info(text: str, width: int = _W) -> str:
    """Bottom frame with embedded text: ``└─ text ───...───┘``."""
    inner = width - 4
    label = f" {text} " if text else ""
    fill = max(inner - _visible_len(label), 0)
    return f"\u2514\u2500{label}{'─' * fill}\u2500\u2518"


def _box_line(text: str, width: int = _W) -> str:
    """Single-line content: ``│  text ...  │``."""
    inner = width - 4
    pad = max(inner - _visible_len(text), 0)
    return f"\u2502  {text}{' ' * pad}\u2502"


def _dbox_top(width: int = _W) -> str:
    """Double-line top: ``╔═══...═══╗``."""
    return f"\u2554{'═' * (width - 2)}\u2557"


def _dbox_bottom(width: int = _W) -> str:
    """Double-line bottom: ``╚═══...═══╝``."""
    return f"\u255a{'═' * (width - 2)}\u255d"


def _dbox_sep(width: int = _W) -> str:
    """Double-line separator: ``╠═══...═══╣``."""
    return f"\u2560{'═' * (width - 2)}\u2563"


def _dbox_line(text: str, width: int = _W) -> str:
    """Double-line content: ``║  text ...  ║``."""
    inner = width - 4
    pad = max(inner - _visible_len(text), 0)
    return f"\u2551  {text}{' ' * pad}\u2551"


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
    """Set display tags from a PipelineSchema. Call once at pipeline init."""
    global _DISPLAY_TAGS
    _DISPLAY_TAGS = _build_display_tags(schema) if schema else {}


def _step_tag(step_name: str | None) -> str:
    if step_name is None:
        return ""
    return f"[{_DISPLAY_TAGS.get(step_name, step_name[:4])}]"


def _infer_terminated_step(step_timings: dict) -> str | None:
    """Infer last executed step from timing dict (insertion-order fallback)."""
    last = None
    for name, t in step_timings.items():
        if t is not None:
            last = name
    return last


def _find_gt_rank(r: dict) -> int | None:
    """Find ground truth rank in candidates. Returns 1-indexed rank or None."""
    from promptpotter.application.scoring.metrics import find_rank

    gt = r.get("ground_truth", "")
    if not gt:
        return None
    pd = r.get("pipeline_data") or {}
    for key in ("ranked_candidates", "token_matched_candidates"):
        rank = find_rank(pd.get(key, []), gt)
        if rank is not None:
            return rank
    return None


def _fmt_query_result(
    r: dict,
    cached: bool = False,
    *,
    prefix: str = "",
    scoring_formula: str | None = None,
) -> str:
    """Format a single query result as a HIT/MISS line with timing.

    When *prefix* is given it replaces the default 8-space indent so the
    caller can merge a counter into the same line. *scoring_formula* is
    the dataset's scoring formula (from ``campaign_config["scoring"]``);
    when provided, ``predicted`` is parsed via ``extract_display_answer``
    so markers like ``**bold**`` or ``\\boxed{…}`` collapse to the
    single-token answer.
    """
    raw_pred = r.get("predicted") or ""
    pred = extract_display_answer(raw_pred, scoring_formula)[:30]
    gt = (r.get("ground_truth") or "").strip()[:30]
    q = ((r.get("query") or "").replace("\n", " ").strip())[:40]
    err = r.get("error") or ("pipeline error" if is_error_result(r) else None)
    pd = r.get("pipeline_data") or {}
    step_name = pd.get("terminated_at")
    if step_name is None:
        st = pd.get("step_timings")
        if st:
            step_name = _infer_terminated_step(st)
    step = _step_tag(step_name)

    tt = pd.get("total_time")

    if r.get("hit"):
        tag = "HIT"
    else:
        ranked = pd.get("ranked_candidates", [])
        n_cand = len(ranked)
        gt_rank = _find_gt_rank(r)
        if gt_rank is not None:
            tag = f"MISS {gt_rank}/{n_cand}"
        elif n_cand:
            tag = f"MISS --/{n_cand}"
        else:
            tag = "MISS"

    cache_marker = "\U0001f4d6" if cached else ""
    step = f"{step}{cache_marker}"

    indent = prefix if prefix else ""

    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    if err:
        return f"{indent}{time_col} {tag} {step} ERR:{str(err)[:40]!r} gt:{gt!r} q:{q!r}"

    line = f"{indent}{time_col} {tag} {step} -> {pred!r} gt:{gt!r} q:{q!r}"

    _ann_indent = ""

    diag = pd.get("diagnostics", {})
    warnings = diag.get("warnings", [])
    if warnings:
        for w in warnings:
            stats = w.get("stats")
            if stats:
                msg = f"{stats['min']} min, {stats['usable']} usable, {stats['fetched']} fetched, {stats['requested']} requested"
            else:
                msg = w["message"]
            line += f"\n{_ann_indent}{YELLOW}\u26a0 {w['step']}: {msg}{RESET}"

    if r.get("retry_of_degraded"):
        comp = r.get("rerun_comparison")
        rerun_detail = ""
        if comp:
            rerun_detail = f" | {comp['hit_change']}"
            if comp.get("rank_change"):
                rerun_detail += f" rank {comp['rank_change']}"
        line += f"\n{_ann_indent}{YELLOW}\U0001f504 rerun of degraded cache{rerun_detail}{RESET}"
    elif r.get("samplescan_probe"):
        line += f"\n{_ann_indent}{YELLOW}\U0001f52c samplescan probe{RESET}"
    elif r.get("switched_out"):
        line += f"\n{_ann_indent}{YELLOW}\U0001f500 switched out (unreliable){RESET}"
    elif r.get("persistently_degraded"):
        line += f"\n{_ann_indent}{RED}\u26a0 persistently degraded{RESET}"
    elif r.get("degraded_observed"):
        obs = r.get("degraded_obs_count", "?")
        threshold = r.get("degraded_obs_threshold", "?")
        line += (
            f"\n{_ann_indent}{DIM}\u21a9 degraded observed ({obs}/{threshold} toward rerun){RESET}"
        )

    return line
