"""Display helpers: ANSI colors, progress output, formatting utilities."""

from __future__ import annotations

__all__ = [
    # Constants
    "RESET", "BOLD", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN",
    # Display functions
    "display_progress", "display_suggestions", "display_axis_profiles",
]

# ANSI foreground colors
RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"

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


def _fmt_query_result(r: dict, cached: bool = False) -> str:
    """Format a single query result as a HIT/MISS line with timing."""
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

    if cached:
        time_str = " \u26a1"  # cached marker
    else:
        tt = pd.get("total_time")
        time_str = f" {tt:.1f}s" if tt is not None else ""

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

    if err:
        return f"        {tag} {step:>8s}  {q:<45s}  ERR: {str(err)[:40]}{time_str}"

    return f"        {tag} {step:>8s}  {q:<45s}  -> {pred}{time_str}"


def display_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round."""
    if not campaign_rounds:
        print("No rounds to display.")
        return

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
        if rd.get("round") == "grid":
            round_label = "G"

        print(
            f"  {round_label:<5s} {acc:>8.1%} {rolling_avg:>12.1%}  {trend_str}"
        )


def display_suggestions(suggestions: dict, round_num: int) -> None:
    """Pretty-print failure patterns, parameter suggestions, and prompt phrases."""
    print(f"\n{'=' * 70}")
    print(f"LLM SUGGESTIONS FOR ROUND {round_num}")
    print(f"{'=' * 70}")

    print(f"\nSUMMARY: {suggestions.get('summary', '')}")

    print("\n--- FAILURE PATTERNS ---")
    for fp in suggestions.get("failure_patterns", []):
        print(
            f"  [{fp.get('category', '?')}] ~{fp.get('count', '?')} queries: "
            f"{fp.get('description', '')}"
        )
        for ex in fp.get("examples", [])[:2]:
            print(f"    e.g. {ex[:60]}")

    print("\n--- PARAMETER CHANGE SUGGESTIONS ---")
    for ps in suggestions.get("parameter_suggestions", []):
        print(
            f"  {ps.get('parameter', '?')}: "
            f"{ps.get('current_value', '?')} -> {ps.get('suggested_value', '?')}"
        )
        print(f"    Rationale: {ps.get('rationale', '')}")

    print("\n--- PROMPT PHRASE FRAGMENTS ---")
    for pf in suggestions.get("prompt_phrase_fragments", []):
        print(f"  [{pf.get('action', '?')}]")
        print(f"    Text: \"{pf.get('text', '')}\"")
        print(f"    Rationale: {pf.get('rationale', '')}")
        print()


def display_axis_profiles(profiles: list[dict]) -> None:
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
