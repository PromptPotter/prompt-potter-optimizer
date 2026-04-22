"""Adaptive sample-prefix renderer — consumes ``cycle.prefix_events``.

Per-round swap log + latest hardness leaderboard. Pure function; the input
is the list of event dicts persisted on each trial via ``Cycle.checkpoint``.

Shape per event (mirrors ``adaptive_prefix.build_prefix_event``):
    {
        "round": int,
        "reason": str,                # "swapped" | "no_viable_swap" | "disabled" | ...
        "swapped_out": list[int],
        "swapped_in": list[int],
        "new_prefix_size": int,
        "rasch": {n_candidates, n_samples, iterations, converged},
        "hardness_top": [{sample_id, delta, ci_width, n_obs}, ...],
    }
"""

from __future__ import annotations


def render_adaptive_prefix(
    events: list[dict],
    *,
    sample_query_lookup: dict[int, str] | None = None,
    hardness_top_n: int = 10,
) -> str:
    """Pretty-print the adaptive-prefix swap log + latest hardness leaderboard.

    Returns an empty string when *events* is empty so callers can append
    unconditionally without leaking a stray header.
    """
    if not events:
        return ""

    lookup = sample_query_lookup or {}

    lines = [
        "\nADAPTIVE PREFIX",
        "=" * 70,
        f"  events recorded : {len(events)}",
    ]

    swap_events = [e for e in events if e.get("swapped_in") or e.get("swapped_out")]
    if swap_events:
        lines.append("")
        lines.append(f"  {'Round':<7s} {'Out':<22s} {'In':<22s} {'Size':>5s}  Reason")
        lines.append(f"  {'-' * 7} {'-' * 22} {'-' * 22} {'-' * 5}  {'-' * 18}")
        for e in swap_events:
            out_ids = ",".join(str(s) for s in e.get("swapped_out", []))[:22]
            in_ids = ",".join(str(s) for s in e.get("swapped_in", []))[:22]
            size = e.get("new_prefix_size", "-")
            reason = str(e.get("reason", ""))[:18]
            round_label = f"{e.get('round', '?')!s}"
            size_label = f"{size!s}"
            lines.append(
                f"  {round_label:<7s} {out_ids:<22s} {in_ids:<22s} {size_label:>5s}  {reason}"
            )

    latest = events[-1]
    rasch = latest.get("rasch") or {}
    if rasch:
        lines.append("")
        lines.append("  Rasch (latest fit)")
        lines.append(
            f"    candidates : {rasch.get('n_candidates', '-')}   "
            f"samples : {rasch.get('n_samples', '-')}   "
            f"iters : {rasch.get('iterations', '-')}   "
            f"converged : {rasch.get('converged', '-')}"
        )

    hardness = latest.get("hardness_top") or []
    if hardness:
        lines.append("")
        lines.append(f"  Hardness leaderboard (top {min(hardness_top_n, len(hardness))})")
        lines.append(f"    {'sample_id':>10s} {'delta':>8s} {'ci_width':>10s} {'n_obs':>7s}  query")
        lines.append(f"    {'-' * 10} {'-' * 8} {'-' * 10} {'-' * 7}  {'-' * 40}")
        for h in hardness[:hardness_top_n]:
            sid = int(h.get("sample_id", -1))
            query = (lookup.get(sid) or "")[:40]
            lines.append(
                f"    {sid:>10d} "
                f"{float(h.get('delta', 0.0)):>+8.3f} "
                f"{float(h.get('ci_width', 0.0)):>10.3f} "
                f"{int(h.get('n_obs', 0)):>7d}  "
                f"{query}"
            )
    return "\n".join(lines)


def collect_prefix_events(rounds: list[dict]) -> list[dict]:
    """Walk a list of trial dicts and return their accumulated ``prefix_events``.

    Each trial JSON carries the running event log up to its round. Reading
    the latest trial gives the full history; reading earlier trials gives
    progressive snapshots. This helper hides that detail from callers — pass
    in any ordered list and get back a deduplicated, round-sorted log.
    """
    seen: dict[int, dict] = {}
    for rd in rounds:
        for ev in rd.get("prefix_events", []) or []:
            r = int(ev.get("round", -1))
            seen[r] = ev
    return [seen[r] for r in sorted(seen)]
