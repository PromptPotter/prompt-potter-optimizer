"""Plain-text renderers for CLI `show-results` output.

Pure functions — return strings, never print. Callers decide what to do with
the output. Kept separate from ``campaign_runner`` so the CLI dispatch file
stays orchestration-only.
"""

from __future__ import annotations


def render_campaign_summary(rounds: list[dict]) -> str:
    """Campaign round table: round, accuracy, hits, label."""
    if not rounds:
        return "No campaign rounds."
    lines = [
        f"\nCAMPAIGN SUMMARY ({len(rounds)} rounds)",
        "=" * 70,
        f"  {'Round':<7s} {'Accuracy':>9s} {'Hits':>6s} {'Label':<40s}",
        f"  {'-' * 7} {'-' * 9} {'-' * 6} {'-' * 40}",
    ]
    for rd in rounds:
        rnd = str(rd.get("round", "?"))
        acc = rd.get("accuracy", 0)
        hits = rd.get("hits", 0)
        total = rd.get("total", 0)
        label = str(rd.get("label", ""))[:40]
        lines.append(f"  {rnd:<7s} {acc:>8.1%} {hits:>3d}/{total:<3d} {label}")
    return "\n".join(lines)


def render_flip_tracking(rounds: list[dict]) -> str:
    """MISS→HIT / HIT→MISS delta between baseline and final round.

    Returns an empty string when there is nothing to report (fewer than 2
    rounds, or missing per-query results).
    """
    if len(rounds) < 2:
        return ""
    base_r = rounds[0].get("results", [])
    final_r = rounds[-1].get("results", [])
    if not (base_r and final_r):
        return ""

    gained = lost = 0
    for br, fr in zip(base_r, final_r, strict=False):
        if br["hit"] != fr["hit"]:
            if fr["hit"]:
                gained += 1
            else:
                lost += 1

    return "\n".join(
        [
            f"\nFLIP TRACKING (baseline -> round {rounds[-1].get('round', '?')})",
            f"  Gained (MISS->HIT): {gained}",
            f"  Lost   (HIT->MISS): {lost}",
            f"  Net change:         {gained - lost:+d}",
        ]
    )


def render_lineage(rounds: list[dict]) -> str:
    """Parent→child lineage chain across campaign rounds."""
    if not rounds:
        return ""
    lines = ["\nLINEAGE CHAIN", "=" * 50]
    for i, rd in enumerate(rounds):
        ps = rd.get("prompt_fields")
        if ps is None:
            continue
        pid = getattr(ps, "id", "")[:12] if hasattr(ps, "id") else "?"
        parent = getattr(ps, "parent_id", "")
        parent_str = parent[:12] if parent else "root"
        arrow = "  " if i == 0 else "  -> "
        acc = rd.get("accuracy", 0)
        label = str(rd.get("label", ""))[:40]
        lines.append(f"{arrow}[{pid}] Round {rd.get('round', '?')}: {label} ({acc:.1%})")
        if parent:
            changes = getattr(ps, "changes_description", "") or "none"
            lines.append(f"       parent: {parent_str}  |  changes: {changes}")
    return "\n".join(lines)
