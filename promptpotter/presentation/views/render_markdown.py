"""Markdown render target — typed View → markdown string for ``log.md``.

``to_markdown(LogMdView)`` produces the per-cycle digest the CLI runner
writes after each round. The hard-sample heatmap is plain text rendered
inside a fenced block; ``render_hard_sample_heatmap`` is exposed for
callers that want the heatmap alone (the CLI standalone hard-sample run).
"""

from __future__ import annotations

import json
from typing import Any

from promptpotter.presentation.views.view_models import (
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.shared.composite import render_composite_block

__all__ = ["render_hard_sample_heatmap", "to_markdown"]


# --- hard-sample-sorter heatmap ------------------------------------------

_HIT = "█"
_MISS = "▒"
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _cell_index(artifact: dict) -> dict[tuple[str, int], bool]:
    return {(c["c"], int(c["s"])): bool(c["hit"]) for c in artifact.get("cells", [])}


def render_hard_sample_heatmap(
    artifact: dict[str, Any],
    *,
    sample_query_lookup: dict[int, str] | None = None,
) -> str:
    """Pretty-print the hard-sample-sorter heatmap + hardness leaderboard."""
    if not artifact or artifact.get("disabled") or not artifact.get("n_observations"):
        return ""

    candidate_order: list[str] = list(artifact.get("candidate_order", []))
    sample_order: list[int] = [int(s) for s in artifact.get("sample_order", [])]
    if not candidate_order or not sample_order:
        return ""

    cells = _cell_index(artifact)
    rasch = artifact.get("rasch") or {}
    theta = rasch.get("theta") or {}
    delta = rasch.get("delta") or {}

    n_cand = artifact.get("n_candidates", len(candidate_order))
    n_samp = artifact.get("n_samples", len(sample_order))
    total_cand = artifact.get("total_candidates", n_cand)
    total_samp = artifact.get("total_samples", n_samp)
    truncated = bool(artifact.get("truncated"))

    label_w = max(_HEATMAP_LABEL_W, min(24, max(len(c) for c in candidate_order)))
    cell_w = _HEATMAP_CELL_W

    lines = [
        "\nHARD-SAMPLE HEATMAP",
        "=" * 70,
        f"  candidates : {n_cand} of {total_cand}"
        + (" (truncated)" if truncated and n_cand < total_cand else ""),
        f"  samples    : {n_samp} of {total_samp}"
        + (" (truncated)" if truncated and n_samp < total_samp else ""),
        f"  observed cells : {artifact['n_observations']}",
        f"  legend : {_HIT} hit   {_MISS} miss   {_UNMEASURED} not measured",
    ]

    header_pad = " " * (label_w + 3)
    lines.append("")
    lines.append(header_pad + "hardest ──── sample_id ────→ easiest")
    lines.append(header_pad + "".join(f"{(sid // 10) % 10:>{cell_w}d}" for sid in sample_order))
    lines.append(header_pad + "".join(f"{sid % 10:>{cell_w}d}" for sid in sample_order))

    for cid in candidate_order:
        row_cells = []
        for sid in sample_order:
            hit = cells.get((cid, sid))
            if hit is None:
                row_cells.append(_UNMEASURED * cell_w)
            elif hit:
                row_cells.append(_HIT * cell_w)
            else:
                row_cells.append(_MISS * cell_w)
        t = theta.get(cid)
        theta_str = f"{t:>+5.2f}" if isinstance(t, (int, float)) else "  ?  "
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {theta_str}  {''.join(row_cells)}")

    top_samples = sample_order[:10]
    if top_samples:
        lookup = sample_query_lookup or {}
        lines.append("")
        lines.append(f"  Hardness leaderboard (top {len(top_samples)})")
        lines.append(f"    {'sample_id':>10s} {'delta':>8s} {'delta_se':>10s}  query")
        lines.append(f"    {'-' * 10} {'-' * 8} {'-' * 10}  {'-' * 40}")
        delta_se = rasch.get("delta_se") or {}
        for sid in top_samples:
            d = float(delta.get(str(sid), 0.0))
            se = float(delta_se.get(str(sid), 0.0))
            query = (lookup.get(sid) or "")[:40]
            lines.append(f"    {sid:>10d} {d:>+8.3f} {se:>10.3f}  {query}")

    return "\n".join(lines)


# --- log.md digest -------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x:.1%}"


def _json_block(label: str, value: Any) -> list[str]:
    if not value:
        return []
    return [
        f"**{label}:**",
        "",
        "```json",
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
        "```",
        "",
    ]


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _spark(values: list[float]) -> str:
    """ASCII sparkline for a [0, 1] series using Unicode block elements."""
    if not values:
        return ""
    out: list[str] = []
    for v in values:
        v_clamped = min(1.0, max(0.0, float(v)))
        idx = min(len(_SPARK_BLOCKS) - 1, int(v_clamped * len(_SPARK_BLOCKS)))
        out.append(_SPARK_BLOCKS[idx])
    return "".join(out)


def _render_p_best_trajectory(rd: RoundDigestView) -> list[str]:
    """Render the per-round P(best) trajectory sparkline section.

    Skipped silently when the JSONL stream wasn't available (resumed
    cycles, pre-PoBB rounds).
    """
    if not rd.p_best_trajectory:
        return []
    # Sort by final P(best) desc so the round winner reads first.
    ordered = sorted(
        rd.p_best_trajectory.items(),
        key=lambda kv: -(kv[1][-1] if kv[1] else 0.0),
    )
    lines: list[str] = ["", "P(best) trajectory:", "```"]
    for cid, traj in ordered[:8]:
        if not traj:
            continue
        spark = _spark(traj)
        final = traj[-1] * 100
        suffix = ""
        if final >= 50.0:
            suffix = " [winner]"
        elif final < 5.0:
            suffix = " [stopped]"
        lines.append(f"  {cid[:10]:<10} {spark}  {final:5.1f}%{suffix}")
    lines.append("```")
    return lines


def _render_round(
    rd: RoundDigestView,
    *,
    formula: str | None,
    baseline_composite: float | None,
) -> list[str]:
    parts: list[str] = [
        f"### Round {rd.round} — {rd.label} ({_fmt_pct(rd.accuracy)})",
        "",
        f"- improved: **{'yes' if rd.improved else 'no'}**",
        f"- hits: {rd.hits}/{rd.total}",
        f"- composite: `{rd.composite:.4f}`",
    ]
    if rd.changes_description:
        parts.append(f"- changes: {rd.changes_description}")
    if rd.l2_directive:
        parts.append(f"- L2 directive: {rd.l2_directive}")
    if rd.l1_yield < 1.0:
        n_total = rd.candidates_scored
        n_valid = max(0, n_total - rd.l1_n_no_op - rd.l1_n_duplicate)
        bits: list[str] = []
        if rd.l1_n_no_op:
            bits.append(f"{rd.l1_n_no_op} no-op")
        if rd.l1_n_duplicate:
            bits.append(f"{rd.l1_n_duplicate} dup")
        parts.append(f"- L1 yield: {n_valid}/{n_total} ({', '.join(bits)})")
    composite_block = render_composite_block(
        rd.composite,
        rd.evaluators,
        formula,
        baseline=baseline_composite,
        use_short_names=False,
    )
    if composite_block:
        parts += ["", "```", *composite_block, "```"]
    if rd.l1_critique_text:
        parts += ["", "> " + rd.l1_critique_text.replace("\n", "\n> ")]
    parts += _render_p_best_trajectory(rd)
    parts.append("")
    return parts


def _render_hard_samples(view: HardSamplesView | None) -> list[str]:
    if view is None:
        return []
    heatmap = render_hard_sample_heatmap(
        view.artifact,
        sample_query_lookup=view.sample_query_lookup,
    ).strip()
    if not heatmap:
        return []
    return ["## Hard Samples", "", "```", heatmap, "```", ""]


def _render_forks(forks: tuple[ForkSummaryView, ...]) -> list[str]:
    if not forks:
        return []
    parts = ["## Forks", ""]
    for f in forks:
        short = f.cycle_id.split("_", 1)[-1] if "_" in f.cycle_id else f.cycle_id
        rounds_word = "round" if f.n_rounds == 1 else "rounds"
        line = (
            f"- `{short}` — {f.mode or '(unknown)'} · "
            f"best {_fmt_pct(f.best_accuracy)} "
            f"(baseline {_fmt_pct(f.baseline_accuracy)}, {f.n_rounds} {rounds_word})"
        )
        if f.stop_reason:
            line += f" · {f.stop_reason}"
        parts.append(line)
    parts.append("")
    return parts


def to_markdown(view: LogMdView) -> str:
    """Render a ``LogMdView`` into the full ``log.md`` document."""
    status = view.status
    parts: list[str] = [
        f"# Campaign {status.campaign_id or '(unknown cycle)'}",
        "",
    ]
    if status.parent_session_id:
        parts += [f"_session: `{status.parent_session_id}`_", ""]

    parts += [
        "## Status",
        "",
        f"- status: **{status.status}**",
        f"- stop reason: `{status.stop_reason}`",
        f"- baseline: {_fmt_pct(status.baseline_accuracy)}",
        (
            f"- best: {_fmt_pct(status.best_accuracy)}"
            + (f" (round {status.best_round})" if status.best_round is not None else "")
        ),
    ]
    if view.family_best is not None:
        fb_acc, fb_holder = view.family_best
        if fb_acc > status.best_accuracy and fb_holder != status.campaign_id:
            short = fb_holder.split("_", 1)[-1] if "_" in fb_holder else fb_holder
            parts.append(f"- family best: {_fmt_pct(fb_acc)} (in fork `{short}`)")
    parts.append(f"- rounds completed: {status.rounds_completed}")
    if status.started_at:
        parts.append(f"- started: {status.started_at}")
    if status.finished_at:
        parts.append(f"- finished: {status.finished_at}")
    parts += ["", *_render_forks(view.forks), "## Rounds", ""]

    if not view.rounds:
        parts += ["_No rounds yet._", ""]
    for rd in view.rounds:
        parts += _render_round(
            rd,
            formula=view.formula,
            baseline_composite=view.baseline_composite,
        )

    parts += _render_hard_samples(view.hard_samples)

    if view.final is not None:
        parts.append("## Final Winner")
        parts.append("")
        parts += _json_block("Prompt fields", view.final.winner_prompt_fields)
        parts += _json_block("Pipeline params", view.final.winner_pipeline_params)

    return "\n".join(parts).rstrip() + "\n"
