"""Round-table renderers: campaign summary, flip tracking, lineage, progress.

All functions take ``rounds: list[dict]`` — either trial dicts loaded from
``campaigns/{cycle_id}/trials/trial_NNNN.json`` (CLI path) or the notebook's
in-memory ``campaign_rounds`` list. Both shapes carry ``round``, ``label``,
``accuracy``, ``hits``, ``total``, ``results``. Lineage-facing fields
(``id``, ``parent_id``, ``changes_description``) can come from either
``opt_search_point`` (disk) or a ``prompt_fields`` object (notebook).
"""

from __future__ import annotations

from typing import Any


def _lineage_refs(rd: dict) -> tuple[str, str, str]:
    """Pull ``(id, parent_id, changes_description)`` from a round dict.

    Tries the disk shape (``opt_search_point`` dict from ``model_dump``)
    first, then the notebook shape (``prompt_fields`` as an OptSearchPoint
    object with attributes), then falls back to ``prompt_fields_id`` from
    the campaign-index summary.
    """
    osp = rd.get("opt_search_point")
    if isinstance(osp, dict):
        return (
            str(osp.get("id", "")),
            str(osp.get("parent_id", "") or ""),
            str(osp.get("changes_description", "") or ""),
        )
    pf = rd.get("prompt_fields")
    if pf is not None and not isinstance(pf, dict):
        return (
            str(getattr(pf, "id", "") or ""),
            str(getattr(pf, "parent_id", "") or ""),
            str(getattr(pf, "changes_description", "") or ""),
        )
    return str(rd.get("prompt_fields_id", "") or ""), "", ""


def render_campaign_summary(rounds: list[dict]) -> str:
    """Per-round comparison table — round, accuracy, hits, label, prompt id."""
    if not rounds:
        return "No campaign rounds."
    has_composite = any(
        rd.get("composite") is not None and rd.get("composite") != rd.get("accuracy", 0)
        for rd in rounds
    )
    header = f"  {'Round':<7s} {'Accuracy':>9s}"
    sep = f"  {'-' * 7} {'-' * 9}"
    if has_composite:
        header += f" {'Composite':>10s}"
        sep += f" {'-' * 10}"
    header += f" {'Hits':>9s} {'Label':<40s} {'Prompt':<12s}"
    sep += f" {'-' * 9} {'-' * 40} {'-' * 12}"

    lines = [
        f"\nCAMPAIGN SUMMARY ({len(rounds)} rounds)",
        "=" * 90,
        header,
        sep,
    ]
    for rd in rounds:
        rnd = str(rd.get("round", "?"))
        acc = rd.get("accuracy", 0) or 0
        hits = rd.get("hits", 0)
        total = rd.get("total", 0)
        label = str(rd.get("label", ""))[:40]
        pid, _, _ = _lineage_refs(rd)
        pid_short = pid[:12] if pid else ""
        hits_str = f"{hits:>3d}/{total:<3d}"
        if has_composite:
            comp = rd.get("composite") or acc
            lines.append(
                f"  {rnd:<7s} {acc:>8.1%} {comp:>10.4f} {hits_str:>9s} "
                f"{label:<40s} {pid_short:<12s}"
            )
        else:
            lines.append(f"  {rnd:<7s} {acc:>8.1%} {hits_str:>9s} {label:<40s} {pid_short:<12s}")
    return "\n".join(lines)


def render_flip_tracking(rounds: list[dict]) -> str:
    """Baseline → final per-query flip delta. Empty string when < 2 rounds."""
    if len(rounds) < 2:
        return ""
    base_r: list[dict[str, Any]] = rounds[0].get("results", []) or []
    final_r: list[dict[str, Any]] = rounds[-1].get("results", []) or []
    if not (base_r and final_r):
        return ""

    flips = []
    for br, fr in zip(base_r, final_r, strict=False):
        if br.get("hit") != fr.get("hit"):
            flips.append(
                {
                    "query": str(br.get("query", ""))[:50],
                    "flip": "MISS->HIT" if fr.get("hit") else "HIT->MISS",
                    "base_pred": str(br.get("predicted", ""))[:30],
                    "final_pred": str(fr.get("predicted", ""))[:30],
                }
            )
    gained = sum(1 for f in flips if f["flip"] == "MISS->HIT")
    lost = sum(1 for f in flips if f["flip"] == "HIT->MISS")

    lines = [
        f"\nFLIP TRACKING (baseline -> round {rounds[-1].get('round', '?')})",
        f"  Gained (MISS->HIT): {gained}",
        f"  Lost   (HIT->MISS): {lost}",
        f"  Net change:         {gained - lost:+d}",
    ]
    if flips:
        lines.append("")
        lines.append(f"  {'Query':<50s} {'Flip':<10s} {'Base pred':<30s} {'Final pred':<30s}")
        lines.append(f"  {'-' * 50} {'-' * 10} {'-' * 30} {'-' * 30}")
        for f in flips:
            lines.append(
                f"  {f['query']:<50s} {f['flip']:<10s} {f['base_pred']:<30s} {f['final_pred']:<30s}"
            )
    return "\n".join(lines)


def render_lineage(rounds: list[dict]) -> str:
    """Parent → child chain across rounds, with change descriptions."""
    if not rounds:
        return ""
    lines = ["\nLINEAGE CHAIN", "=" * 50]
    for i, rd in enumerate(rounds):
        pid, parent, changes = _lineage_refs(rd)
        pid_short = pid[:12] if pid else "?"
        parent_short = parent[:12] if parent else "root"
        arrow = "  " if i == 0 else "  -> "
        acc = rd.get("accuracy", 0) or 0
        label = str(rd.get("label", ""))[:40]
        lines.append(f"{arrow}[{pid_short}] Round {rd.get('round', '?')}: {label} ({acc:.1%})")
        if parent:
            lines.append(f"       parent: {parent_short}  |  changes: {changes or 'none'}")
    return "\n".join(lines)


def render_progress(rounds: list[dict], window: int = 8) -> str:
    """Training-style rolling accuracy table + plateau marker."""
    if not rounds:
        return "No rounds to display."

    has_composite = any(
        rd.get("composite") is not None and rd.get("composite") != rd.get("accuracy", 0)
        for rd in rounds
    )

    if has_composite:
        header = (
            f"\n  {'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
            f" {'Rolling Avg':>13s} {'Trend':>10s}"
        )
    else:
        header = f"\n  {'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>10s}"
    lines = ["\nPROGRESS", header]

    accuracies: list[float] = []
    for rd in rounds:
        acc = rd.get("accuracy", 0) or 0
        accuracies.append(acc)
        window_slice = accuracies[-window:]
        rolling_avg = sum(window_slice) / len(window_slice)

        if len(accuracies) <= 1:
            trend_str = "-"
        else:
            delta = acc - accuracies[-2]
            if abs(delta) < 0.001:
                trend_str = "+0.0% plateau"
            elif delta > 0:
                trend_str = f"+{delta:.1%}"
            else:
                trend_str = f"{delta:.1%}"

        rnd = str(rd.get("round", "?"))
        if has_composite:
            comp = rd.get("composite") or acc
            lines.append(f"  {rnd:<7s} {acc:>8.1%} {comp:>10.4f} {rolling_avg:>12.1%}  {trend_str}")
        else:
            lines.append(f"  {rnd:<7s} {acc:>8.1%} {rolling_avg:>12.1%}  {trend_str}")

    if len(accuracies) >= 3:
        recent = accuracies[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            lines.append(f"  -- Plateau: rolling avg stable at {recent_avg:.1%} for 3 rounds")
    return "\n".join(lines)
