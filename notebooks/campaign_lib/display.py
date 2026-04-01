"""Display helpers: ANSI colors, progress output, formatting utilities."""

from __future__ import annotations

import re

from .stats import fmt_ci, wilson_ci

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
    # Constants
    "RESET",
    "YELLOW",
    "_box_bottom",
    "_box_line",
    # Box-drawing helpers
    "_box_top",
    "_dbox_bottom",
    "_dbox_line",
    "_dbox_sep",
    "_dbox_top",
    "_dotted_line",
    "_fmt_delta",
    # Interrupt handling
    "_print_interrupt_banner",
    "_scoreboard",
    "show_axis_profiles",
    # Campaign results display
    "show_campaign_summary",
    "show_flip_tracking",
    "show_lineage_chain",
    # Display functions
    "show_progress",
    # Scan analytics
    "show_scan_leaderboard",
    "show_scan_query_difficulty",
]

# ANSI foreground colors
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"

# ---------------------------------------------------------------------------
# Box-drawing helpers (pure formatting, no business logic)
# ---------------------------------------------------------------------------

# Display geometry — single source of truth for terminal widths
BOX_WIDTH = 70          # standard box width
NODE_FRAME_WIDTH = 74   # node frame width (phase_display.py)
_W = BOX_WIDTH          # internal alias


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
        s.get("composite") is not None and s.get("composite") != s["accuracy"]
        for s in ranked
    )
    if has_composite:
        hdr = (f"{'#':<4s}{'Label':<8s}{'Accuracy':>9s}  {'95% CI':>16s}"
               f"  {'Composite':>9s}  {'Delta':>7s}")
    else:
        hdr = (f"{'#':<4s}{'Label':<8s}{'Accuracy':>9s}  {'95% CI':>16s}"
               f"  {'Delta':>7s}")
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
            row = (f"{i:<4d}{label:<8s}{acc:>8.1%}   {ci_str:>16s}"
                   f"   {comp:>8.4f}   {delta_str:>7s}{winner_mark}")
        else:
            row = (f"{i:<4d}{label:<8s}{acc:>8.1%}   {ci_str:>16s}"
                   f"   {delta_str:>7s}{winner_mark}")

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


_STEP_SHORT_TAGS: dict[str, str] = {
    "cache_lookup": "cache",
    "fuzzy_matching": "fuzzy",
    "web_search": "web",
    "entity_profiling": "prof",
    "token_matching": "token",
    "llm_ranking": "llm",
}


def _step_tag(step_name: str | None) -> str:
    if step_name is None:
        return ""
    return f"[{_STEP_SHORT_TAGS.get(step_name, step_name[:5])}]"


def _infer_terminated_step(step_timings: dict) -> str | None:
    """Infer last executed step from timing dict (insertion-order fallback)."""
    last = None
    for name, t in step_timings.items():
        if t is not None:
            last = name
    return last


def _find_gt_rank(r: dict) -> int | None:
    """Find ground truth rank in candidates. Returns 1-indexed rank or None."""
    gt = r.get("ground_truth", "")
    if not gt:
        return None
    pd = r.get("pipeline_data") or {}
    ranked = pd.get("ranked_candidates", [])
    for i, c in enumerate(ranked):
        name = c.get("candidate", "") if isinstance(c, dict) else str(c)
        if name == gt:
            return i + 1
    # Fallback: token_matched_candidates (tuple/list format)
    token = pd.get("token_matched_candidates", [])
    for i, c in enumerate(token):
        name = c[0] if isinstance(c, (list, tuple)) else str(c)
        if name == gt:
            return i + 1
    return None


def _fmt_query_result(r: dict, cached: bool = False, *, prefix: str = "") -> str:
    """Format a single query result as a HIT/MISS line with timing.

    When *prefix* is given it replaces the default 8-space indent so the
    caller can merge a counter into the same line.
    """
    q = (r.get("query") or "")[:45]
    pred = (r.get("predicted") or "")[:35]
    err = r.get("error")
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
    if precomp:
        last_cached = _STEP_SHORT_TAGS.get(precomp[-1], precomp[-1][:5])
        step = f"{DIM}{last_cached}\U0001f4d6{RESET}[]{step}{cache_marker}"
    else:
        step = f"{step}{cache_marker}"

    indent = prefix if prefix else ""

    sw = 10 if cached else 8
    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    if err:
        return f"{indent}{time_col} {tag} {step:>{sw}s}  {q:<45s}  ERR: {str(err)[:40]}"

    line = f"{indent}{time_col} {tag} {step:>{sw}s}  {q:<45s}  -> {pred}"

    # Append pipeline degradation warnings from diagnostics
    diag = pd.get("diagnostics", {})
    warnings = diag.get("warnings", [])
    if warnings:
        warn_indent = " " * len(indent) + " " * (sw + 2)
        for w in warnings:
            line += f"\n{warn_indent}{YELLOW}\u26a0 {w['step']}: {w['message']}{RESET}"

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
        print(f"\n{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
              f" {'Rolling Avg':>13s} {'Trend':>8s}")
    else:
        print(f"\n{'Round':<7s} {'Accuracy':>9s}"
              f" {'Rolling Avg':>13s} {'Trend':>8s}")
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
            print(
                f"  {round_label:<5s} {acc:>8.1%} {comp:>9.4f}"
                f" {rolling_avg:>12.1%}  {trend_str}"
            )
        else:
            print(
                f"  {round_label:<5s} {acc:>8.1%}"
                f" {rolling_avg:>12.1%}  {trend_str}"
            )

    # Plateau detection
    if len(accuracies) >= 3:
        recent = accuracies[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            print(f"  {YELLOW}-- Plateau: rolling avg stable at"
                  f" {recent_avg:.1%} for 3 rounds{RESET}")



def show_axis_profiles(profiles: list[dict]) -> None:
    """Display axis profiles as a formatted table."""
    if not profiles:
        print("No axis profiles to display.")
        return

    print(f"\n{'Rank':<5s} {'Axis':<25s} {'Type':<15s} "
          f"{'Card':<5s} {'Range':<8s} {'Budget':<8s}")
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


def show_scan_leaderboard(
    scan_df,
    axis_profiles: list[dict],
) -> None:
    """Display variant leaderboard and per-axis statistics from scan results."""
    import pandas as pd
    from IPython.display import display as ipy_display

    if scan_df.empty:
        print("No scan data to display.")
        return

    # --- A. Variant leaderboard ---
    sort_col = "composite" if "composite" in scan_df.columns else "accuracy"
    ranked = scan_df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    rows = []
    for i, r in ranked.iterrows():
        label = r["value_preview"]
        if r["delta"] == 0.0:
            label = f"{label} (baseline)"
        rows.append({
            "rank": i + 1,
            "axis": r["axis"],
            "variant": label,
            "accuracy": f"{r['accuracy']:.1%}",
            "delta": f"{r['delta']:+.1%}" if r["delta"] != 0.0 else "-",
            "hits/total": f"{r['hits']}/{r['total']}",
            "errors": int(r.get("errors", 0)),
        })

    print("VARIANT LEADERBOARD (all scan combos)")
    print("=" * 70)
    ipy_display(pd.DataFrame(rows))

    # --- B. Per-axis statistics ---
    profile_lookup = {p["axis"]: p for p in axis_profiles}
    axis_rows = []
    for axis_name, grp in scan_df.groupby("axis", sort=False):
        prof = profile_lookup.get(axis_name, {})
        accs = grp["accuracy"]
        axis_rows.append({
            "axis": axis_name,
            "type": prof.get("axis_type", grp["axis_type"].iloc[0]),
            "variants": len(grp),
            "mean_acc": f"{accs.mean():.1%}",
            "std_acc": f"{accs.std():.1%}" if len(grp) > 1 else "-",
            "best_acc": f"{accs.max():.1%}",
            "worst_acc": f"{accs.min():.1%}",
            "sensitivity": f"{prof.get('sensitivity_range', accs.max() - accs.min()):.3f}",
            "budget": prof.get("exploration_budget", "?"),
        })

    print("\nPER-AXIS STATISTICS")
    print("=" * 70)
    ipy_display(pd.DataFrame(axis_rows))


def show_scan_query_difficulty(
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
    scan_summaries = [s for s in summaries if s.get("source") == "sensitivity_scan"]

    if not scan_summaries:
        print("No sensitivity_scan runs found.")
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
                    "n_evals": 0, "n_hits": 0, "n_errors": 0,
                }
            qs = query_stats[q]
            qs["n_evals"] += 1
            if item.get("hit"):
                qs["n_hits"] += 1
            if item.get("error"):
                qs["n_errors"] += 1

    if not query_stats:
        print("No query-level data found in scan runs.")
        return pd.DataFrame()

    # Build rows
    rows = []
    for query, qs in query_stats.items():
        n = qs["n_evals"]
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

        rows.append({
            "query": query[:60],
            "ground_truth": qs["ground_truth"][:50],
            "hit_rate": hit_rate,
            "hits/evals": f"{qs['n_hits']}/{n}",
            "error_rate": error_rate,
            "classification": classification,
        })

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
        rows.append({
            "round": rd["round"],
            "label": rd["label"][:40],
            "hit@1": rd["hits"],
            "total": rd["total"],
            "accuracy": f"{rd['accuracy']:.1%}",
            "prompt_id": rd["prompt_fields"].id[:12],
        })

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
            flips.append({
                "query": br["query"][:50],
                "flip": "MISS->HIT" if f_hit else "HIT->MISS",
                "base_pred": br["predicted"][:35],
                "final_pred": fr["predicted"][:35],
                "ground_truth": br["ground_truth"][:35],
            })

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
            f"{arrow}[{ps.id[:12]}] Round {rd['round']}: "
            f"{rd['label'][:40]} ({rd['accuracy']:.1%})"
        )
        if ps.parent_id:
            print(
                f"       parent: {parent}  |  "
                f"changes: {ps.changes_description or 'none'}"
            )
