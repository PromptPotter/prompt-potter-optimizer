"""Notebook display — unified ANSI/formatter/phase/callback layer.

Three concerns in one module:
- Low-level formatting: ANSI colors, box-drawing helpers, scoreboards.
- Phase dispatch: ``_CycleDisplayState`` + ``_dispatch_phase`` render
  per-phase node frames for the feedback cycle.
- ``NotebookDisplay``: the callback bundle that wires everything into
  ``RunCallbacks`` for notebook runs.

Previously split across ``display.py`` / ``phase_display.py`` /
``display_callbacks.py`` — merged because the seam was not real (cyclic
imports, single consumer).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import (
    min_detectable_effect,
    proportion_test,
    wilson_ci,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunCallbacks
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.pipeline_schema import PipelineSchema


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


__all__ = [
    "BLUE",
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "MAGENTA",
    "RED",
    "RESET",
    "YELLOW",
    "NotebookDisplay",
    "_box_bottom",
    "_box_line",
    "_box_top",
    "_dbox_bottom",
    "_dbox_line",
    "_dbox_sep",
    "_dbox_top",
    "_dotted_line",
    "_fmt_delta",
    "_print_interrupt_banner",
    "_scoreboard",
    "format_pipeline_overrides",
    "show_axis_profiles",
    "show_campaign_summary",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_progress",
    "show_recon_leaderboard",
    "show_recon_query_difficulty",
]

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

# ---------------------------------------------------------------------------
# Box-drawing helpers (pure formatting, no business logic)
# ---------------------------------------------------------------------------

# Display geometry — single source of truth for terminal widths
BOX_WIDTH = 70  # standard box width
NODE_FRAME_WIDTH = 74  # node frame width (phase_display.py)
_W = BOX_WIDTH  # internal alias


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
    inner = width - 4  # minus └─ prefix and ─┘ suffix
    label = f" {text} " if text else ""
    fill = max(inner - _visible_len(label), 0)
    return f"\u2514\u2500{label}{'─' * fill}\u2500\u2518"


def _box_line(text: str, width: int = _W) -> str:
    """Single-line content: ``│  text ...  │``."""
    inner = width - 4  # minus │ + 2 spaces each side
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
        pad = width - len(label) - 2  # 2 spaces around label
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

    # Sort by composite (if present) then accuracy
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
    When multiple nodes resolve to the same base tag, append ``_1``, ``_2``, …
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
    # Try ranked_candidates first, then token_matched_candidates fallback
    for key in ("ranked_candidates", "token_matched_candidates"):
        rank = find_rank(pd.get(key, []), gt)
        if rank is not None:
            return rank
    return None


def _fmt_query_result(r: dict, cached: bool = False, *, prefix: str = "") -> str:
    """Format a single query result as a HIT/MISS line with timing.

    When *prefix* is given it replaces the default 8-space indent so the
    caller can merge a counter into the same line.
    """
    q = (r.get("query") or "")[:45]
    pred = (r.get("predicted") or "")[:35]
    err = r.get("error") or ("pipeline error" if is_error_result(r) else None)
    pd = r.get("pipeline_data") or {}
    step_name = pd.get("terminated_at")
    if step_name is None:
        st = pd.get("step_timings")
        if st:
            step_name = _infer_terminated_step(st)
    step = _step_tag(step_name)

    tt = pd.get("total_time")

    # Build tag: "HIT " or "MISS 3/20" with rank info inline
    if r.get("hit"):
        tag = "HIT "
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

    cache_marker = " \U0001f4d6" if cached else ""
    precomp = r.get("precomputed_through")
    if precomp and not cached:
        last_cached = _DISPLAY_TAGS.get(precomp[-1], precomp[-1][:4])
        step = f"{DIM}[{last_cached}\U0001f4d6]{RESET}->{step}{cache_marker}"
    else:
        step = f"{step}{cache_marker}"

    indent = prefix if prefix else ""

    sw = 10 if cached else 8
    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    if err:
        return f"{indent}{time_col} {tag} {step:>{sw}s}  {q:<45s}  ERR: {str(err)[:40]}"

    line = f"{indent}{time_col} {tag} {step:>{sw}s}  {q:<45s}  -> {pred}"

    _ann_indent = ""

    # Append pipeline degradation warnings from diagnostics
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

    # Stale data protocol actions
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


def show_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round."""
    if not campaign_rounds:
        print("No rounds to display.")
        return

    has_composite = any(
        rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
        for rd in campaign_rounds
    )

    if has_composite:
        print(
            f"\n{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
            f" {'Rolling Avg':>13s} {'Trend':>8s}"
        )
    else:
        print(f"\n{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}")
    accuracies = []

    for rd in campaign_rounds:
        acc = rd["accuracy"]
        accuracies.append(acc)
        n = len(accuracies)

        window_slice = accuracies[-window:]
        rolling_avg = sum(window_slice) / len(window_slice)

        if n <= 1:
            trend_str = "-"
        else:
            delta = acc - accuracies[-2]
            if abs(delta) < 0.001:
                trend_str = "+0.0%  <-- plateau"
            elif delta > 0:
                trend_str = f"+{delta:.1%}"
            else:
                trend_str = f"{delta:.1%}"

        round_label = str(rd["round"])

        if has_composite:
            comp = rd.get("composite", acc)
            print(f"  {round_label:<5s} {acc:>8.1%} {comp:>9.4f} {rolling_avg:>12.1%}  {trend_str}")
        else:
            print(f"  {round_label:<5s} {acc:>8.1%} {rolling_avg:>12.1%}  {trend_str}")

    # Plateau detection
    if len(accuracies) >= 3:
        recent = accuracies[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            print(
                f"  {YELLOW}-- Plateau: rolling avg stable at {recent_avg:.1%} for 3 rounds{RESET}"
            )


def show_axis_profiles(profiles: list[dict]) -> None:
    """Display axis profiles as a formatted table."""
    if not profiles:
        print("No axis profiles to display.")
        return

    print(f"\n{'Rank':<5s} {'Axis':<25s} {'Type':<15s} {'Card':<5s} {'Range':<8s} {'Budget':<8s}")
    print("-" * 70)
    for rank, p in enumerate(profiles, 1):
        print(
            f"  {rank:<3d} {p['axis']:<25s} {p['axis_type']:<15s} "
            f"{p['cardinality']:<5d} {p['sensitivity_range']:<8.3f} "
            f"{p['exploration_budget']:<8s}"
        )


# ---------------------------------------------------------------------------
# Scan analytics
# ---------------------------------------------------------------------------


def show_recon_leaderboard(
    recon_df,
    axis_profiles: list[dict],
) -> None:
    """Display variant leaderboard and per-axis statistics from scan results."""
    import pandas as pd
    from IPython.display import display as ipy_display

    if recon_df.empty:
        print("No scan data to display.")
        return

    # --- A. Variant leaderboard ---
    sort_col = "composite" if "composite" in recon_df.columns else "accuracy"
    ranked = recon_df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    rows = []
    for i, r in ranked.iterrows():
        label = r["value_preview"]
        if r["delta"] == 0.0:
            label = f"{label} (baseline)"
        rows.append(
            {
                "rank": i + 1,
                "axis": r["axis"],
                "variant": label,
                "accuracy": f"{r['accuracy']:.1%}",
                "delta": f"{r['delta']:+.1%}" if r["delta"] != 0.0 else "-",
                "hits/total": f"{r['hits']}/{r['total']}",
                "errors": int(r.get("errors", 0)),
            }
        )

    print("VARIANT LEADERBOARD (all scan combos)")
    print("=" * 70)
    ipy_display(pd.DataFrame(rows))

    # --- B. Per-axis statistics ---
    profile_lookup = {p["axis"]: p for p in axis_profiles}
    axis_rows = []
    for axis_name, grp in recon_df.groupby("axis", sort=False):
        prof = profile_lookup.get(axis_name, {})
        accs = grp["accuracy"]
        axis_rows.append(
            {
                "axis": axis_name,
                "type": prof.get("axis_type", grp["axis_type"].iloc[0]),
                "variants": len(grp),
                "mean_acc": f"{accs.mean():.1%}",
                "std_acc": f"{accs.std():.1%}" if len(grp) > 1 else "-",
                "best_acc": f"{accs.max():.1%}",
                "worst_acc": f"{accs.min():.1%}",
                "sensitivity": f"{prof.get('sensitivity_range', accs.max() - accs.min()):.3f}",
                "budget": prof.get("exploration_budget", "?"),
            }
        )

    print("\nPER-AXIS STATISTICS")
    print("=" * 70)
    ipy_display(pd.DataFrame(axis_rows))


def show_recon_query_difficulty(
    store,
    backend_id: str,
):
    """Aggregate per-query hit rates across scan runs and classify difficulty.

    Returns:
        DataFrame sorted by hit_rate ascending (hardest first).
    """
    import pandas as pd
    from IPython.display import display as ipy_display

    # Collect scan-source dataset runs
    summaries = store.dataset_runs.list_all(backend_id)
    scan_summaries = [s for s in summaries if s.get("source") == "run_recon"]

    if not scan_summaries:
        print("No run_recon runs found.")
        return pd.DataFrame()

    # Aggregate per-query stats
    query_stats: dict[str, dict] = {}  # key: query string
    for summary in scan_summaries:
        detail = store.dataset_runs.load_by_id(backend_id, summary["run_id"])
        if not detail:
            continue
        for item in detail.get("dataset_run_items", []):
            q = item.get("query", "")
            if not q:
                continue
            if q not in query_stats:
                query_stats[q] = {
                    "ground_truth": item.get("ground_truth", ""),
                    "n_measurements": 0,
                    "n_hits": 0,
                    "n_errors": 0,
                }
            qs = query_stats[q]
            qs["n_measurements"] += 1
            if item.get("hit"):
                qs["n_hits"] += 1
            if is_error_result(item):
                qs["n_errors"] += 1

    if not query_stats:
        print("No query-level data found in scan runs.")
        return pd.DataFrame()

    # Build rows
    rows = []
    for query, qs in query_stats.items():
        n = qs["n_measurements"]
        hit_rate = qs["n_hits"] / n if n else 0.0
        error_rate = qs["n_errors"] / n if n else 0.0

        if error_rate == 1.0:
            classification = "error"
        elif hit_rate == 1.0:
            classification = "easy"
        elif hit_rate == 0.0:
            classification = "hard"
        else:
            classification = "discriminating"

        rows.append(
            {
                "query": query[:60],
                "ground_truth": qs["ground_truth"][:50],
                "hit_rate": hit_rate,
                "hits/evals": f"{qs['n_hits']}/{n}",
                "error_rate": error_rate,
                "classification": classification,
            }
        )

    df = pd.DataFrame(rows).sort_values("hit_rate", ascending=True).reset_index(drop=True)

    # Summary header
    counts = df["classification"].value_counts()
    total = len(df)
    parts = []
    for cat in ("easy", "discriminating", "hard", "error"):
        c = counts.get(cat, 0)
        parts.append(f"{cat}: {c} ({c / total:.0%})")

    print(f"QUERY DIFFICULTY ({total} queries across {len(scan_summaries)} scan runs)")
    print("=" * 70)
    print(f"  {' | '.join(parts)}")
    print()
    ipy_display(df)

    return df


# ---------------------------------------------------------------------------
# Pipeline overrides display
# ---------------------------------------------------------------------------


def format_pipeline_overrides(
    pipeline_params: dict | None,
    pipeline_schema=None,
) -> None:
    """Print pipeline_params as a copy-paste ready ``pipeline_overrides`` dict.

    Output uses nested format: ``{"node_name": {"param": value}}``.
    """
    if not pipeline_params:
        return

    # Collect node → {param: value} entries
    node_entries: list[tuple[str, dict]] = []
    for key, val in pipeline_params.items():
        if key == "steps" or not isinstance(val, dict):
            continue
        # Filter to tunable params only (skip structural keys like prompt, model)
        tunable = {}
        if pipeline_schema:
            node = pipeline_schema.get_node(key)
            if node:
                tunable = {k: v for k, v in val.items() if k in node.param_keys}
        if not tunable:
            tunable = val
        if tunable:
            node_entries.append((key, tunable))

    if not node_entries:
        return

    print(f"\n  {CYAN}Copy-paste pipeline_overrides:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print('  "pipeline_overrides": {')
    for node_name, params in node_entries:
        print(f'      "{node_name}": {{')
        for param, val in params.items():
            print(f'          "{param}": {val!r},')
        print("      },")
    print("  }")
    print(f"  {DIM}{'─' * 60}{RESET}")


# ---------------------------------------------------------------------------
# Campaign results display
# ---------------------------------------------------------------------------


def show_campaign_summary(campaign_rounds: list) -> None:
    """Display campaign comparison table as DataFrame."""
    if not campaign_rounds:
        print("No campaign rounds to display.")
        return

    import pandas as pd
    from IPython.display import display as ipy_display

    rows = []
    for rd in campaign_rounds:
        rows.append(
            {
                "round": rd["round"],
                "label": rd["label"][:40],
                "hit@1": rd["hits"],
                "total": rd["total"],
                "accuracy": f"{rd['accuracy']:.1%}",
                "prompt_id": rd["prompt_fields"].id[:12],
            }
        )

    print(f"CAMPAIGN SUMMARY ({len(campaign_rounds)} rounds)")
    print(f"{'=' * 70}")
    ipy_display(pd.DataFrame(rows))


def show_flip_tracking(campaign_rounds: list) -> None:
    """Compare first vs last round, display per-query flip table."""
    if len(campaign_rounds) < 2:
        print("Need at least 2 rounds for flip tracking.")
        return

    base_r = campaign_rounds[0]["results"]
    final_r = campaign_rounds[-1]["results"]

    if not base_r or not final_r:
        print("Skipping flip tracking — baseline or final results are empty.")
        return

    import pandas as pd
    from IPython.display import display as ipy_display

    flips = []
    for br, fr in zip(base_r, final_r, strict=False):
        b_hit = br["hit"]
        f_hit = fr["hit"]
        if b_hit != f_hit:
            flips.append(
                {
                    "query": br["query"][:50],
                    "flip": "MISS->HIT" if f_hit else "HIT->MISS",
                    "base_pred": br["predicted"][:35],
                    "final_pred": fr["predicted"][:35],
                    "ground_truth": br["ground_truth"][:35],
                }
            )

    gained = sum(1 for f in flips if f["flip"] == "MISS->HIT")
    lost = sum(1 for f in flips if f["flip"] == "HIT->MISS")

    print(f"FLIP TRACKING (baseline -> round {campaign_rounds[-1]['round']})")
    print(f"  Queries gained (MISS->HIT): {gained}")
    print(f"  Queries lost (HIT->MISS):   {lost}")
    print(f"  Net change:                 {gained - lost:+d}")
    print()
    if flips:
        ipy_display(pd.DataFrame(flips))


def show_lineage_chain(campaign_rounds: list) -> None:
    """Display OptSearchPoint lineage chain across rounds."""
    if not campaign_rounds:
        print("No campaign rounds to display.")
        return

    print("LINEAGE CHAIN")
    print("=" * 50)
    for i, rd in enumerate(campaign_rounds):
        ps = rd["prompt_fields"]
        parent = ps.parent_id[:12] if ps.parent_id else "root"
        arrow = "  " if i == 0 else "  -> "
        print(
            f"{arrow}[{ps.id[:12]}] Round {rd['round']}: {rd['label'][:40]} ({rd['accuracy']:.1%})"
        )
        if ps.parent_id:
            print(f"       parent: {parent}  |  changes: {ps.changes_description or 'none'}")


# ---------------------------------------------------------------------------
# Phase-specific display formatters for the feedback cycle
#
# Three visual weights:
# - CYCLE boundaries: double-line box (╔═══╗) for cycle start/end
# - ROUND boundaries: heavy rule (━━━) between rounds
# - NODE transitions: ├─ NODE ─┤ entry frame with │ content lines
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Display state — tracks cycle metadata across callbacks (display-only)
# ---------------------------------------------------------------------------


@dataclass
class _CycleDisplayState:
    """Mutable display state threaded through phase/callback closures.

    Populated exclusively from PhaseEvent data — never touches services.
    """

    max_rounds: int = 0
    patience: int = 0
    l1_stall_count: int = 0
    round_num: int = 0
    baseline_accuracy: float = 0.0
    baseline_total: int = 0  # sample count for significance tests
    recon_brief: ReconBrief | None = None  # cached for scan reasoning display
    candidates_meta: list = field(default_factory=list)  # from l1_generate exit
    n_scoring_queries: int = 0  # from generate:exit, used in evaluate:enter banner
    current_pipeline_params: dict | None = None  # raw pp for candidate eval callback
    # 3-column SP diff tracking (flattened dot-notation dicts)
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    previous_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    # Pipeline node ordering for grouped diff display
    node_param_keys: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# Node-frame box-drawing helpers
# ---------------------------------------------------------------------------

_NW = NODE_FRAME_WIDTH


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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _pp_val(v) -> str:
    """Format a pipeline param value for display.

    No truncation here — the diff table legend shows full values,
    and inline cells use _VAL_INLINE_MAX to decide when to use a
    legend code instead.
    """
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (dict, list)):
        return str(v)
    return str(v)


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
            continue  # skip pipeline node list
        # JSON Schema object — drill into properties
        if isinstance(v, dict) and v.get("type") == "object" and "properties" in v:
            for prop_name, prop_def in v["properties"].items():
                desc = prop_def.get("description", prop_def.get("type", "?"))
                flat[f"{k}.{prop_name}"] = desc
        # Mutation tuple list: [['+', name, type, req, desc], ...]
        elif isinstance(v, list) and v and isinstance(v[0], list):
            for mutation in v:
                if not mutation:
                    continue
                op = mutation[0]
                if op == "+" and len(mutation) >= 5:
                    flat[f"{k}.{mutation[1]}"] = mutation[4]
                elif op == "~" and len(mutation) >= 6:
                    flat[f"{k}.{mutation[2]}"] = mutation[5]
                # '-' removals: absent = not in dict (handled by diff)
        # Plain nested dict (e.g. node overrides) — un-nest to flat param keys
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                flat[sub_k] = _pp_val(sub_v)
        else:
            flat[k] = _pp_val(v)
    return flat


_ABSENT = "-"
_UNCHANGED = "\u00b7"  # middle dot
_VAL_INLINE_MAX = 12  # values longer than this get a lookup code


def _build_candidate_flat(
    parent: dict[str, str],
    candidate_meta: dict,
) -> dict[str, str]:
    """Merge candidate overrides onto parent flat dict.

    When a candidate overrides a schema key (e.g. profiling_schema),
    all parent's dot-notation children for that key are removed first,
    then the candidate's expanded fields are added.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        # Remove parent's dot-children for any overridden schema keys
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = _flatten_sp_summary(pp)
        flat.update(override_flat)
    return flat


def _group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group diff keys by pipeline node in execution order.

    Returns ``(node_name, [keys])`` pairs.  When ``node_param_keys``
    is ``None``, returns a single unnamed group sorted alphabetically.
    """
    if not node_param_keys:
        return [("", diff_keys)]

    # Reverse map: flat_key → node_name (including dot-notation children)
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

    ``columns`` is a list of (label, flat_dict) pairs.
    Only rows where at least one column differs are shown.
    Short values (<=12 chars) are shown inline; longer values get a
    letter code [a]..[z] with full text in a legend below the table.

    When ``node_param_keys`` is provided, rows are grouped by pipeline
    node in execution order with separator lines between groups.
    """
    if len(columns) < 2:
        return

    # Collect all keys, filter to those that differ
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

    # Build lookup table: value → code letter
    lookup: dict[str, str] = {}  # long_value → "[a]"
    legend: list[tuple[str, str]] = []  # (code, full_value)
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

    # Column layout
    col_w = max(8, max(len(label) for label, _ in columns) + 2)
    max_key = max(len(k) for k in diff_keys)

    # Header
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    print(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    print(_node_line(hdr))

    # Rows — grouped by pipeline node
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

    # Legend
    if legend:
        print(_node_line(""))
        print(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            print(_node_line(f"  {code} {full}"))


# ---------------------------------------------------------------------------
# Phase handlers — one per (phase, event) pair
# ---------------------------------------------------------------------------


def _print_init_enter(d: dict, state: _CycleDisplayState) -> None:
    config = d["config"]
    dataset = d["dataset"]
    schema = config.pipeline_schema

    state.max_rounds = config.max_rounds or 0
    state.patience = config.l1_patience
    state.original_sp_flat = _flatten_sp_summary(
        schema.to_pipeline_params() if schema else None,
    )
    state.node_param_keys = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )

    model = config.model or "(default)"
    l2 = "enabled" if config.enable_l2 else "disabled"
    l3 = "enabled" if config.enable_l3 else "disabled"
    scan = "YES" if config.recon_brief is not None else "NO"
    sample = config.sp_budget_ttest
    state.baseline_total = sample

    print()
    print(_dbox_top())
    print(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    print(_dbox_sep())
    print(
        _dbox_line(f"Max rounds     {state.max_rounds or 999!s:<15s}Patience    {state.patience}")
    )
    print(_dbox_line(f"Candidates     {config.n_variants}"))
    sample_label = f"{sample} of {len(dataset)}"
    mde = min_detectable_effect(sample)
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(f"Min detectable {YELLOW}\u00b1{mde:.1%}{RESET} (\u03b1=0.05, 80% power)"))
    print(_dbox_line(f"Model          {model}"))
    print(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    print(_dbox_line(f"Scan context   {scan}"))
    critique = "enabled" if config.enable_critique else "disabled"
    print(_dbox_line(f"Critique       {critique}"))
    print(_dbox_bottom())


def _print_init_exit(d: dict, state: _CycleDisplayState) -> None:
    loop_state = d["state"]
    loop_env = d["env"]
    state.baseline_accuracy = loop_state.current_accuracy
    cycle_id = (loop_env.cycle_id or "?")[:12]
    samples = len(loop_env.scoring_dataset)
    obs = "ON" if (loop_env.scoring_ctx and loop_env.scoring_ctx.obs) else "OFF"
    print(
        f"  {GREEN}\u2713{RESET} Initialized  baseline={loop_state.current_accuracy:.1%}  "
        f"cycle={cycle_id}  samples={samples}  obs={obs}"
    )
    crit = loop_state.opt_sp.memory.critique_text
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = loop_env.resumed_from_round
    if resumed > 0:
        state_parts = []
        critique_chars = len(loop_state.opt_sp.memory.critique_text)
        task_context_keys = len(loop_state.opt_sp.task_context)
        l2_round = loop_state.escalation.l2.round
        if critique_chars:
            state_parts.append(f"critique={critique_chars} chars")
        if task_context_keys:
            state_parts.append(f"task_context={task_context_keys} keys")
        if l2_round:
            state_parts.append(f"l2_round={l2_round}")
        state_suffix = f"  ({', '.join(state_parts)})" if state_parts else ""
        print(f"    Resumed from round {resumed} ({resumed} rounds cached){state_suffix}")
    else:
        print("    Starting fresh (no prior rounds for this cycle)")


def _print_l1_generate_enter(d: dict, state: _CycleDisplayState) -> None:
    acc = d.get("current_accuracy", 0.0)
    preview = d.get("prompt_preview", "").replace("\n", " ").strip()
    if len(preview) > 50:
        preview = preview[:47] + "..."
    if not preview:
        preview = "(no baseline -- seed from scan or provide instruction)"
    n = d.get("n_variants", 0)
    model = d.get("model") or "(default)"
    creativity = d.get("creativity", 0.7)
    scan = "YES" if d.get("has_recon_brief") else "NO"
    crit = "YES" if d.get("has_critique") else "NO"

    # Rotate SP diff state
    new_flat = _flatten_sp_summary(
        d.get("pipeline_params"),
    )
    if state.round_num == 0:
        state.previous_sp_flat = state.original_sp_flat.copy()
    else:
        state.previous_sp_flat = state.current_sp_flat.copy()
    state.current_sp_flat = new_flat

    r_label = f"ROUND {state.round_num + 1}/{state.max_rounds or 999}"
    p_label = f"patience {state.l1_stall_count}/{state.patience}"

    # Heavy round separator
    print()
    print(_round_rule(r_label, p_label))

    # Generate node frame
    print()
    print(_node_top("GENERATE"))
    print(_node_line(f"Current best    {acc:.1%}"))
    print(_node_line(f"Prompt          {preview}"))
    print(
        _node_line(
            f"Candidates      {n}   Creativity: {creativity}   Scan: {scan}   Critique: {crit}"
        )
    )
    print(_node_line(f"Model           {model}"))

    # Scan reasoning inside node frame
    if state.recon_brief and d.get("has_recon_brief"):
        axes = state.recon_brief.improving_axes
        bl_acc = state.recon_brief.baseline_accuracy
        if axes:
            print(
                _node_line(
                    f"{CYAN}Scan focus:{RESET} {len(axes)} improving axes [{', '.join(axes)}]"
                )
            )
        if bl_acc > 0:
            print(_node_line(f"{CYAN}Scan baseline:{RESET} {bl_acc:.1%}"))

    print(_node_bottom())


def _print_l1_generate_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    state.n_scoring_queries = d.get("n_scoring_queries", 0)
    state.candidates_meta = d.get("candidates", [])

    print(f"  {GREEN}✓{RESET} {n} candidates generated ({source})")

    # Multi-column SP diff: Start | Parent | C1..CN
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", state.original_sp_flat),
        ("Parent", state.current_sp_flat),
    ]
    for c in state.candidates_meta:
        c_flat = _build_candidate_flat(state.current_sp_flat, c)
        columns.append((f"C{c['idx'] + 1}", c_flat))
    print()
    _print_sp_diff(
        columns,
        node_param_keys=state.node_param_keys,
        round_num=state.round_num + 1,
    )


def _print_l1_score_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    n_calls = n_cand * n_q
    calls_label = f"{n_cand} \u00d7 {n_q} = {n_calls} calls"

    # Raw pp for candidate eval callback (don't touch sp_flat — GENERATE owns it)
    scoring_pp = d.get("current_pipeline_params")
    if scoring_pp is not None:
        state.current_pipeline_params = scoring_pp

    print()
    print(_node_top("EVALUATE", calls_label))


def _print_l1_score_exit(d: dict, state: _CycleDisplayState) -> None:
    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    # Normalize labels to C{i+1} for display (service uses candidate_id)
    for i, s in enumerate(scores):
        s["label"] = f"C{i + 1}"
    non_aborted = [s for s in scores if not s.get("escalation_aborted")]
    best_s = (
        max(non_aborted, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]))
        if non_aborted
        else {}
    )
    winner = best_s.get("label", "?")

    # Scoreboard: full table for >3 candidates, compact 1-liner otherwise
    if len(scores) > 3:
        board = _scoreboard(scores, winner, state.baseline_accuracy)
        if board:
            print(board)
    elif scores:
        _ranked = sorted(
            scores, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]), reverse=True
        )
        parts = []
        for s in _ranked:
            lbl = s.get("label", "?")
            acc = s["accuracy"]
            ab = " (aborted)" if s.get("escalation_aborted") else ""
            parts.append(f"{lbl}={acc:.1%}{ab}")
        print(f"  Scoreboard: {' | '.join(parts)}")

    # Composite suffix (only when it differs from accuracy)
    comp_tag = ""
    if w_comp is not None and w_comp != w_acc:
        comp_tag = f"  composite={w_comp:.4f}"

    # Significance test
    winner_hits = best_s.get("hits", 0)
    winner_total = best_s.get("total", 0)

    # Result line
    if improved:
        delta = w_acc - state.baseline_accuracy
        bl_hits = round(state.baseline_accuracy * winner_total)
        p = proportion_test(winner_hits, winner_total, bl_hits, winner_total)
        sig_tag = f"  {fmt_pvalue(p)}"
        print(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
            f" (was {state.baseline_accuracy:.1%},"
            f" {_fmt_delta(delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {action}"
        )
        state.baseline_accuracy = w_acc
    else:
        print(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

    # Critique (fed forward to next l1_generate)
    crit = d.get("critique_text", "")
    if crit:
        crit_text = crit.replace("\n", " ").strip()
        print(f"  {CYAN}Critique:{RESET} {crit_text}")


def _print_escalation_enter(d: dict, state: _CycleDisplayState) -> None:
    check = d.get("check_name", "?")
    target = d.get("target", "?")
    rate = d.get("degraded_rate", 0)
    wtypes = d.get("warning_types", {})

    print()
    print(_node_top("ESCALATION", f"{check} \u2192 {target}"))
    print(_node_line(f"{YELLOW}Degraded: {rate:.0%} of queries{RESET}"))
    for wt, count in wtypes.items():
        print(_node_line(f"{wt}: {count} occurrences"))
    print(_node_bottom())


def _print_escalation_exit(d: dict, state: _CycleDisplayState) -> None:
    classifications = d.get("classifications", [])
    if classifications:
        print(f"  {CYAN}Warning classifications:{RESET}")
        for c in classifications:
            print(f"    {c['warning_type']}: {c['status']}")


def _print_refine_enter(d: dict, state: _CycleDisplayState) -> None:
    l2r = d.get("l2_round", "?")
    stalls = d.get("l1_stall_count", "?")
    acc = d.get("current_accuracy", 0.0)
    best = d.get("best_accuracy", 0.0)
    params = d.get("current_params", {})

    print()
    print(_node_top("L2 REFINE CONTEXT", f"L2 round {l2r}"))
    print(_node_line(f"L1 stalled {stalls} rounds  |  acc={acc:.1%}  best={best:.1%}"))
    if params:
        parts = []
        for k, v in list(params.items())[:5]:
            vs = str(v)
            if len(vs) > 30:
                vs = vs[:27] + "..."
            parts.append(f"{k}={vs}")
        extra = len(params) - 5
        params_str = ", ".join(parts)
        if extra > 0:
            params_str += f", +{extra} more"
        print(_node_line(f"Current params: {params_str}"))
    else:
        print(_node_line("Current params: (none)"))
    print(_node_line("LLM analyzing failure patterns..."))
    print(_node_bottom())


def _print_refine_exit(d: dict, state: _CycleDisplayState) -> None:
    import json

    n_changes = d.get("param_changes_count", 0)
    tc = f", {GREEN}task_context updated{RESET}" if d.get("task_context_changed") else ""
    _action = d.get("action", "continue")
    probe = f", {CYAN}action={_action}{RESET}" if _action != "continue" else ""
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} L2 decision: {n_changes} param changes{tc}{probe}")
    if desc:
        print(f"    {desc}")

    # Warning inventory one-liner
    warned = d.get("warned_queries", 0)
    top_w = d.get("top_warning", "")
    if warned:
        print(f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}")

    # Debug: show full L2 prompt and response
    l2_prompt = d.get("l2_prompt", "")
    if l2_prompt:
        print(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        for line in l2_prompt.split("\n"):
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END PROMPT ---{RESET}")

    l2_resp = d.get("l2_response")
    if l2_resp:
        print(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        formatted = json.dumps(l2_resp, indent=2)
        for line in formatted.split("\n")[:40]:
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END RESPONSE ---{RESET}")


def _print_probe_enter(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probe_queries", 0)
    queries = d.get("probe_queries", [])
    print()
    print(_node_top("PROBE ROUND", f"{n} queries"))
    print(_node_line("Testing warned queries with new settings..."))
    for q in queries[:5]:
        print(_node_line(f"  {q[:70]}"))
    if len(queries) > 5:
        print(_node_line(f"  ... +{len(queries) - 5} more"))
    print(_node_bottom())


def _print_probe_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probed", 0)
    hits = d.get("probe_hits", 0)
    if n:
        rate = hits / n
        color = GREEN if rate > 0.5 else YELLOW
        print(f"  {color}⚡ Probe: {hits}/{n} hits ({rate:.0%}){RESET}")
    else:
        print(f"  {YELLOW}⚡ Probe: no matching queries found{RESET}")


def _print_plan_enter(d: dict, state: _CycleDisplayState) -> None:
    l3r = d.get("l3_round", "?")
    l2_stalls = d.get("l2_stall_count", "?")
    plan = d.get("current_plan_preview", "")
    if len(plan) > 55:
        plan = plan[:52] + "..."

    print()
    print(_node_top("L3 MODIFY PLAN", f"L3 round {l3r}"))
    print(_node_line(f"L2 stalled {l2_stalls} rounds"))
    print(_node_line(f"Current plan: {plan}"))
    print(_node_line("LLM designing new strategy..."))
    print(_node_bottom())


def _print_plan_exit(d: dict, state: _CycleDisplayState) -> None:
    new_plan = d.get("new_plan_preview", "")
    if len(new_plan) > 55:
        new_plan = new_plan[:52] + "..."
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} New plan: {new_plan}")
    if desc:
        print(f"    {desc}")


def _print_backend_warning(d: dict, state: _CycleDisplayState) -> None:
    msg = d.get("message", "")
    advice = d.get("advice", "")
    resets = d.get("degradation_reset_count", 0)
    steps = d.get("problem_steps", [])
    wtypes = d.get("persistent_warning_types", {})

    print()
    print(_dbox_top())
    print(_dbox_line(f"{RED}{BOLD}BACKEND WARNING{RESET}"))
    print(_dbox_sep())
    print(_dbox_line(msg))
    print(_dbox_line(""))
    print(_dbox_line(advice))
    print(_dbox_sep())
    steps_str = ", ".join(steps) if steps else "unknown"
    print(_dbox_line(f"Resets: {resets}  |  Steps: {steps_str}"))
    for wt, count in wtypes.items():
        print(_dbox_line(f"  {wt}: {count} occurrences"))
    print(_dbox_bottom())


# ---------------------------------------------------------------------------
# Phase dispatch
# ---------------------------------------------------------------------------

_PHASE_HANDLERS: dict[str, Callable] = {
    "init:enter": _print_init_enter,
    "init:exit": _print_init_exit,
    "l1_generate:enter": _print_l1_generate_enter,
    "l1_generate:exit": _print_l1_generate_exit,
    "l1_score:enter": _print_l1_score_enter,
    "l1_score:exit": _print_l1_score_exit,
    "refine_strategy:enter": _print_refine_enter,
    "refine_strategy:exit": _print_refine_exit,
    "modify_plan:enter": _print_plan_enter,
    "modify_plan:exit": _print_plan_exit,
    "escalation:enter": _print_escalation_enter,
    "escalation:exit": _print_escalation_exit,
    "probe_round:enter": _print_probe_enter,
    "probe_round:exit": _print_probe_exit,
    "backend_warning:notify": _print_backend_warning,
}


def _dispatch_phase(event: PhaseEvent, state: _CycleDisplayState) -> None:
    """Route a PhaseEvent to its phase-specific formatter."""
    if event.round is not None:
        state.round_num = event.round
    key = f"{event.phase}:{event.event}"
    handler = _PHASE_HANDLERS.get(key)
    if handler:
        handler(event.data, state)
    else:
        # Fallback for unknown phases
        print(f"  [{event.phase.upper()} {event.event}] {event.data}")


# ---------------------------------------------------------------------------
# Notebook callback bundle — wires display into RunCallbacks
# ---------------------------------------------------------------------------


class NotebookDisplay:
    """Callback bundle for notebook optimization runs."""

    def __init__(
        self,
        *,
        campaign_rounds: list,
        baseline_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        recon_brief: ReconBrief | None = None,
    ) -> None:
        self.campaign_rounds = campaign_rounds
        self.baseline_acc = baseline_acc
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.initial_len = len(campaign_rounds)
        self.state = _CycleDisplayState(baseline_accuracy=baseline_acc)
        self.state.recon_brief = recon_brief
        self.query_counter = 0

    def as_callbacks(self) -> RunCallbacks:
        from promptpotter.application.campaign.callbacks import RunCallbacks

        return RunCallbacks(
            on_round_complete=self.on_round,
            on_candidate_scored=self.on_candidate,
            on_sample_scored=self.on_query,
            on_phase=self.on_phase,
        )

    def on_phase(self, event: PhaseEvent) -> None:
        _dispatch_phase(event, self.state)
        # Reset query counter on escalation exit (on_round is skipped)
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.query_counter = 0
        # On resume: clear stale optimization rounds before replay re-appends them
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].resumed_from_round > 0
        ):
            del self.campaign_rounds[self.initial_len :]

    def on_query(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, result: dict
    ) -> None:
        self.query_counter += 1
        is_cached = result.get("cached", False)
        prefix = f"  [{self.query_counter:>3d}] "
        print(_fmt_query_result(result, cached=is_cached, prefix=prefix), flush=True)

    def on_candidate(self, idx: int, total: int, scores: dict) -> None:
        label = f"C{idx + 1}"
        w = 66

        acc = scores["accuracy"]
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")
        from promptpotter.shared.statistics import wilson_ci

        ci_lo, ci_hi = wilson_ci(hits, n)
        delta = acc - self.state.baseline_accuracy

        # Validation-failure branch: candidate never ran the backend because
        # parse-time validation rejected an L1-proposed value as out-of-set.
        # Render with the ⚠ … ↳ "addressed by" convention so the user sees
        # both what happened and how the loop reacted.
        if scores.get("invalid"):
            failures = scores.get("validation_failures") or []
            acc_tag = f"{YELLOW}INVALID{RESET}"
            print(f"  {_box_top(f'{label}/{total}', acc_tag, width=w)}")
            for vf in failures:
                axis = vf.get("axis", "?")
                value = vf.get("value", "?")
                allowed = vf.get("allowed") or []
                allowed_str = ", ".join(allowed[:3]) + (
                    f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
                )
                cause = f"{YELLOW}\u26a0{RESET} {axis} = {value!r}  \u2209 [{allowed_str}]"
                response = "  \u21b3 scored 0 (no backend call); L2 directive will name this value"
                print(f"  {_box_line(cause, width=w)}")
                print(f"  {_box_line(response, width=w)}")
            print(f"  {_box_bottom(width=w)}")
            return

        # Line 1 (top frame): label + accuracy with CI
        acc_tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"
        print(f"  {_box_top(f'{label}/{total}', acc_tag, width=w)}")

        # Line 2 (content): cyan mutations + hits + vs baseline
        meta: dict[str, Any] = {}
        if idx < len(self.state.candidates_meta):
            meta = self.state.candidates_meta[idx]
        pp = meta.get("pipeline_params_override")
        parts: list[str] = []
        if pp:
            for node, val in pp.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        parts.append(f"{node}.{k}: {_pp_val(v)}")
                else:
                    parts.append(f"{node}: {_pp_val(val)}")
        mutations = f"{CYAN}{'  '.join(parts)}{RESET}  " if parts else ""
        if scores.get("escalation_aborted"):
            scored_q = scores.get("scored_queries", n)
            expected_q = scores.get("expected_queries", n)
            hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
        else:
            hit_str = f"{hits}/{n} hits"
        content = f"{mutations}{hit_str}  vs baseline: {_fmt_delta(delta)}"
        print(f"  {_box_line(content, width=w)}")

        # Line 3 (bottom frame): composite + degraded
        bottom_parts: list[str] = []
        if comp is not None and comp != acc:
            bottom_parts.append(f"composite={comp:.4f}")
        degraded = scores.get("degraded_queries", 0)
        if degraded:
            bottom_parts.append(f"{YELLOW}\u26a0 {degraded}/{n} degraded{RESET}")
        if bottom_parts:
            print(f"  {_box_bottom_info('  '.join(bottom_parts), width=w)}")
        else:
            print(f"  {_box_bottom(width=w)}")

    def on_round(self, round_result: RoundResult, l1_stall_count: int) -> None:
        self.query_counter = 0
        self.state.l1_stall_count = l1_stall_count

        self.campaign_rounds.append(
            {
                "round": len(self.campaign_rounds),
                "label": round_result.label,
                "accuracy": round_result.accuracy,
                "composite": round_result.composite,
                "hits": round_result.hits,
                "total": round_result.total,
                "improved": round_result.improved,
                "prompt_fields": OptSearchPoint.from_prompt_fields(round_result.prompt_fields),
                "results": round_result.results,
                "candidates_scored": round_result.candidates_scored,
                "candidate_scores": list(round_result.candidate_scores),
            }
        )

        rn = self.state.round_num + 1

        print()
        print(_node_top(f"ROUND {rn} SUMMARY"))

        self._print_progress_table()
        self._print_round_stats(round_result)
        self._print_patience_status(round_result, l1_stall_count)

        print(_node_bottom())

    def _print_progress_table(self) -> None:
        _accs: list[float] = []
        has_comp = any(
            rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
            for rd in self.campaign_rounds
        )
        if has_comp:
            print(
                _node_line(
                    f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
                    f" {'Rolling Avg':>13s} {'Trend':>8s}"
                )
            )
        else:
            print(_node_line(f"{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}"))

        for rd in self.campaign_rounds:
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
                print(_node_line(f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}"))
            else:
                print(_node_line(f"  {rl:<5s} {acc:>8.1%} {rolling:>12.1%}  {trend}"))

        if len(_accs) >= 3:
            recent = _accs[-3:]
            recent_avg = sum(recent) / len(recent)
            if all(abs(a - recent_avg) < 0.005 for a in recent):
                print(
                    _node_line(
                        f"{YELLOW}-- Plateau: rolling avg stable at"
                        f" {recent_avg:.1%} for 3 rounds{RESET}"
                    )
                )

        print(_node_line(""))

    def _print_round_stats(self, round_result: RoundResult) -> None:
        hits = round_result.hits
        total = round_result.total
        if total == 0 and round_result.candidate_scores:
            best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
            hits = best.get("hits", 0)
            total = best.get("total", 0)
        print(
            _node_line(
                f"hits: {hits}/{total}  |  evaluated: {round_result.candidates_scored} candidates"
            )
        )

        if not round_result.results:
            return

        try:
            from collections import Counter

            from promptpotter.application.optimization.nodes.critique import (
                candidate_keys_from_schema,
                get_candidates,
            )
            from promptpotter.application.scoring.metrics import find_rank

            candidate_keys = candidate_keys_from_schema(self.pipeline_schema)
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
                print(
                    _node_line(
                        f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in terminations.most_common())}"
                    )
                )
            if degraded > 0:
                print(_node_line(f"Degradation: {degraded / n_results:.0%}"))

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

                print(_node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}"))
        except Exception:
            pass  # stats are best-effort

    def _print_patience_status(self, round_result: RoundResult, l1_stall_count: int) -> None:
        if round_result.improved:
            print(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
            return
        print(
            _node_line(
                f"{YELLOW}⚠ No improvement ({l1_stall_count}/{self.l1_patience} patience){RESET}"
            )
        )
        if l1_stall_count >= self.l1_patience:
            print(
                _node_line(
                    f"{RED}Stopping: patience exhausted"
                    f" ({self.l1_patience} consecutive stalls){RESET}"
                )
            )
