"""Live display primitives — pure formatting, zero business logic.

ANSI color codes, box-drawing helpers, CI/p-value/delta formatters,
scoreboard, interrupt banner, display-tag module state, per-query
result line formatter, and the shared candidate render kernel
(``build_candidate_summary`` + ``fmt_candidate_header``) consumed by
both the CLI and notebook displays. Also provides shared round-summary
render functions (``render_progress_table``, ``render_round_stats``,
``render_patience_status``) so both entry points show identical output.
Depends only on ``shared`` utilities and ``application/`` imports guarded
under ``try/except`` for best-effort sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from promptpotter.application.optimization.elimination import FATAL_WARNINGS
from promptpotter.application.optimization.utils import extract_warning_types
from promptpotter.presentation.views.formatting import fmt_ci, fmt_pvalue
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.scoring import extract_display_answer
from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "CandidateSummary",
    "build_candidate_summary",
    "fmt_candidate_header",
    "fmt_ci",
    "fmt_pp_override",
    "fmt_pvalue",
    "format_elimination_summary",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
]


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


def _append_annotation(line: str, indent: str, color: str, emoji: str, text: str) -> str:
    """Append a per-query annotation line under the query it describes.

    ``indent`` mirrors the query line's leading whitespace so the emoji sits
    directly under the HIT/MISS tag instead of floating at column 0.
    """
    return line + f"\n{indent}{color}{emoji} {text}{RESET}"


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

    # Per-LLM-node token column: `[tag] in=N out=M` groups in pipeline order.
    # Values are prefixed with `~` when the entry is a chars/4 estimate rather
    # than a provider-exact count.
    step_tokens = pd.get("step_tokens") or {}
    tok_col = ""
    if step_tokens:
        groups = []
        for node_name, entry in step_tokens.items():
            tag_name = _DISPLAY_TAGS.get(node_name, node_name[:4])
            mark = "~" if entry.get("estimated") else ""
            groups.append(
                f"[{tag_name}] in={mark}{entry.get('input', 0)} out={mark}{entry.get('output', 0)}"
            )
        tok_col = " " + " ".join(groups)
        # Dedup: single-LLM pipeline, no cache marker, step tag identical to
        # the token group's tag → drop standalone step tag.
        if len(step_tokens) == 1 and not cached:
            only_node = next(iter(step_tokens))
            only_tag = _DISPLAY_TAGS.get(only_node, only_node[:4])
            if step == f"[{only_tag}]":
                step = ""

    indent = prefix if prefix else ""

    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    sid = r.get("sample_id")
    sid_col = f"#{sid:03d}" if sid is not None else "    "
    step_block = f" {step}" if step else ""
    if err:
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} ERR:{str(err)[:40]!r} gt:{gt!r} q:{q!r}"

    line = f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} -> {pred!r} gt:{gt!r} q:{q!r}"

    _ann_indent = " " * len(indent) if indent else "      "

    diag = pd.get("diagnostics", {})
    warnings = diag.get("warnings", [])
    if warnings:
        for w in warnings:
            stats = w.get("stats")
            if stats:
                msg = f"{stats['min']} min, {stats['usable']} usable, {stats['fetched']} fetched, {stats['requested']} requested"
            else:
                msg = w["message"]
            line += f"\n{_ann_indent}{YELLOW}⚠ {w['step']}: {msg}{RESET}"

    if r.get("retry_of_degraded"):
        comp = r.get("rerun_comparison") or {}
        detail = f"; result: {comp['hit_change']}" if comp.get("hit_change") else ""
        if comp.get("rank_change"):
            detail += f" (rank {comp['rank_change']})"
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f504",
            f"cache had pipeline warnings → reran{detail}",
        )
    elif r.get("samplescan_resolved"):
        cfg = r.get("samplescan_config") or {}
        n = cfg.get("n_candidates", "?")
        thr = cfg.get("resolved_threshold", "?")
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f52c",
            f"cache had warnings + rerun still degraded → resampled {n} fresh calls "
            f"(threshold {thr}); result accepted",
        )
    elif r.get("switched_out"):
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f500",
            "query degrades ≥50% of the time historically → using cached answer "
            "(resampling would likely degrade again)",
        )
    elif r.get("persistently_degraded"):
        line = _append_annotation(
            line,
            _ann_indent,
            RED,
            "⚠",
            "entire stale-data ladder exhausted → still degraded; "
            "score counts but flag this candidate",
        )
    elif r.get("degraded_observed"):
        # Fatal warnings kill the candidate on this same query (see
        # DegradationCheck fast-path). The "toward rerun" counter would
        # suggest "more data coming" — meaningless when the candidate is
        # already dead. The ⚠ warning line above tells the real story.
        if not any(w in FATAL_WARNINGS for w in extract_warning_types(r)):
            obs = r.get("degraded_obs_count", "?")
            threshold = r.get("degraded_obs_threshold", "?")
            line = _append_annotation(
                line,
                _ann_indent,
                DIM,
                "↩",
                f"pipeline warning observed; {obs}/{threshold} occurrences toward rerun "
                f"trigger (not yet at threshold)",
            )

    return line


# ---------------------------------------------------------------------------
# Candidate render kernel — shared by CliDisplay and NotebookDisplay.
# Both entry points classify + format the same way; only the outer wrapping
# (flat tqdm.write vs box-drawing) differs.
# ---------------------------------------------------------------------------


def fmt_pp_override(pp: dict | None) -> str:
    """Flatten a nested pipeline_params override to ``node.key: val  …``.

    Returns ``""`` when the override is empty or None. Callers supply the
    leading label (e.g. ``cand 2/5  ``) and any ANSI wrapping.
    """
    if not pp:
        return ""
    parts: list[str] = []
    for node, val in pp.items():
        if isinstance(val, dict):
            for k, v in val.items():
                parts.append(f"{node}.{k}: {_pp_val(v)}")
        else:
            parts.append(f"{node}: {_pp_val(val)}")
    return "  ".join(parts)


def fmt_candidate_header(
    idx: int,
    total: int,
    changes_description: str,
    pp_override: dict | None,
) -> str:
    """One-line pre-scoring header: ``cand k/N  <mutation>`` or ``cand k/N  parent re-eval``.

    Fired from ``on_candidate_started`` before the backend calls start. The
    mutation body comes from ``pp_override`` when present (authoritative
    machine-readable diff), else falls back to the human-written
    ``changes_description`` from the L1 variant plan, else the parent tag.
    """
    label = f"cand {idx + 1}/{total}"
    body = fmt_pp_override(pp_override)
    if not body and changes_description:
        body = changes_description.strip()
    body = f"{DIM}parent re-eval{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}  {body}"


def format_elimination_summary(ctx: dict, prior_label: str | None = None) -> str:
    """One-line eliminated-candidate summary.

    Example: ``✂ eliminated q7/15  p=0.023 *  vs C3 (of 3 priors)``.
    ``prior_label`` is the resolved label of the prior that triggered the
    Wilcoxon rejection; falls back to the integer index in ctx when absent.
    """
    q = int(ctx.get("queries_evaluated", 0))
    qt = int(ctx.get("total_queries", 0))
    p = float(ctx.get("triggered_p", 1.0))
    n_priors = int(ctx.get("n_priors", 0))

    if prior_label is None:
        idx = ctx.get("triggered_by_prior_idx", ctx.get("triggered_by_prior", -1))
        prior_label = f"prior #{idx}" if isinstance(idx, int) and idx >= 0 else "prior"

    return (
        f"{YELLOW}✂ eliminated q{q}/{qt}{RESET}  "
        f"{fmt_pvalue(p)}  vs {prior_label} (of {n_priors} prior{'s' if n_priors != 1 else ''})"
    )


@dataclass(frozen=True)
class CandidateSummary:
    """Structured candidate render — displays pick plain vs box wrapping.

    Pieces the two displays can compose independently:

    - ``tag`` — compact status slot (``80.0% [77-82%]`` or ``INVALID``);
      notebook puts it on the top-right of the box, CLI appends it to
      ``cand k/N``.
    - ``body_line`` — the mutations + hits + delta line; the notebook
      renders it as an inner box line, the CLI as an indented second line.
    - ``detail_lines`` — ordered extras (elimination summary, composite,
      degraded count, or validation-failure entries); rendered inline by
      both displays, with the notebook folding the last entry into the
      bottom info rule when possible.
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


def _fmt_validation_failure_lines(failures: list[dict]) -> tuple[str, ...]:
    """Render one '⚠ axis = value ∉ allowed' + '↳ scored 0' pair per failure."""
    out: list[str] = []
    for vf in failures:
        axis = vf.get("axis", "?")
        value = vf.get("value", "?")
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:3]) + (
            f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
        )
        out.append(f"{YELLOW}⚠{RESET} {axis} = {value!r}  ∉ [{allowed_str}]")
        out.append("  ↳ scored 0 (no backend call); L2 directive will name this value")
    return tuple(out)


def build_candidate_summary(scores: dict, baseline_acc: float) -> CandidateSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).
    """
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
        failures = scores.get("validation_failures") or []
        return CandidateSummary(
            status="invalid",
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=_fmt_validation_failure_lines(failures),
        )

    acc = scores["accuracy"]
    hits = scores.get("hits", 0)
    n = scores.get("total", 0)
    ci_lo, ci_hi = wilson_ci(hits, n)
    delta = acc - baseline_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    if scores.get("escalation_aborted"):
        scored_q = scores.get("scored_queries", n)
        expected_q = scores.get("expected_queries", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
        return CandidateSummary(
            status="aborted",
            tag=tag,
            body_line=f"{mutations_chunk}{hit_str}  vs baseline: {_fmt_delta(delta)}",
            detail_lines=(),
        )

    body_line = f"{mutations_chunk}{hits}/{n} hits  vs baseline: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    if scores.get("elimination_stopped"):
        elim_ctx = scores.get("elimination_context") or {}
        prior_label = elim_ctx.get("triggered_by_prior_label")
        detail_lines.append(format_elimination_summary(elim_ctx, prior_label))

    comp = scores.get("composite")
    if comp is not None and comp != acc:
        detail_lines.append(f"composite={comp:.4f}")
    degraded = scores.get("degraded_queries", 0)
    if degraded:
        detail_lines.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")

    status: Literal["ok", "eliminated"] = (
        "eliminated" if scores.get("elimination_stopped") else "ok"
    )
    return CandidateSummary(
        status=status,
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
    )


# ---------------------------------------------------------------------------
# Shared round-summary renderers — used by both CliDisplay and NotebookDisplay
# so both entry points produce identical round-boundary output.
# All functions return multi-line strings; callers route through print() or
# tqdm.write() depending on the entry point.
# ---------------------------------------------------------------------------


def render_progress_table(campaign_rounds: list[dict]) -> str:
    """Round-over-round trajectory table: accuracy, composite, rolling avg, trend, plateau.

    ``campaign_rounds`` items must have at minimum ``round``, ``accuracy``.
    ``composite`` is optional; column appears only when it differs from
    accuracy in at least one round.
    """
    lines: list[str] = []
    _accs: list[float] = []
    has_comp = any(
        rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
        for rd in campaign_rounds
    )
    if has_comp:
        lines.append(
            _node_line(
                f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
                f" {'Rolling Avg':>13s} {'Trend':>8s}"
            )
        )
    else:
        lines.append(
            _node_line(f"{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}")
        )

    for rd in campaign_rounds:
        acc = rd["accuracy"]
        _accs.append(acc)
        window_slice = _accs[-8:]
        rolling = sum(window_slice) / len(window_slice)
        if len(_accs) <= 1:
            trend = "-"
        else:
            d = acc - _accs[-2]
            if abs(d) < 0.001:
                trend = "+0.0%  <-- plateau"
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        rl = "G" if rd.get("round") == "grid" else str(rd["round"])
        if has_comp:
            comp = rd.get("composite", acc)
            lines.append(
                _node_line(f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}")
            )
        else:
            lines.append(_node_line(f"  {rl:<5s} {acc:>8.1%} {rolling:>12.1%}  {trend}"))

    if len(_accs) >= 3:
        recent = _accs[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            lines.append(
                _node_line(
                    f"{YELLOW}-- Plateau: rolling avg stable at"
                    f" {recent_avg:.1%} for 3 rounds{RESET}"
                )
            )

    lines.append(_node_line(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns an empty string when ``round_result.results`` is empty.
    """
    lines: list[str] = []
    hits = round_result.hits
    total = round_result.total
    if total == 0 and round_result.candidate_scores:
        best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
        hits = best.get("hits", 0)
        total = best.get("total", 0)
    lines.append(
        _node_line(
            f"hits: {hits}/{total}  |  evaluated: {round_result.candidates_scored} candidates"
        )
    )

    if not round_result.results:
        return "\n".join(lines)

    try:
        from collections import Counter

        from promptpotter.application.optimization.utils import (
            candidate_keys_from_schema,
            get_candidates,
        )
        from promptpotter.application.scoring.metrics import find_rank

        candidate_keys = candidate_keys_from_schema(pipeline_schema)
        results = round_result.results
        n_results = len(results)
        terminations: Counter[str] = Counter()
        degraded = 0
        for r in results:
            pd = r.get("pipeline_data") or {}
            terminations[pd.get("terminated_at", "unknown")] += 1
            if (pd.get("diagnostics") or {}).get("warnings"):
                degraded += 1

        if terminations:
            lines.append(
                _node_line(
                    f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in terminations.most_common())}"
                )
            )
        if degraded > 0:
            lines.append(_node_line(f"Degradation: {degraded / n_results:.0%}"))

        valid = [r for r in results if not is_error_result(r)]
        if valid:

            def recall_at_k(k: int) -> float:
                hit_count = 0
                for r in valid:
                    rank = find_rank(
                        get_candidates(r, candidate_keys),
                        r.get("ground_truth", ""),
                    )
                    if rank is not None and rank <= k:
                        hit_count += 1
                return hit_count / len(valid)

            lines.append(
                _node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}")
            )
    except Exception:
        pass

    return "\n".join(lines)


def render_patience_status(
    improved: bool,
    l1_stall_count: int,
    l1_patience: int,
) -> str:
    """Green tick on improvement; yellow patience counter; red stop on exhaustion."""
    lines: list[str] = []
    if improved:
        lines.append(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        return "\n".join(lines)
    lines.append(
        _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")
    )
    if l1_stall_count >= l1_patience:
        lines.append(
            _node_line(
                f"{RED}Stopping: patience exhausted ({l1_patience} consecutive stalls){RESET}"
            )
        )
    return "\n".join(lines)
