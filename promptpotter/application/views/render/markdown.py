"""``log.md`` render target — the per-cycle digest rewritten after each round: per-round block, sparklines, heatmap,
fork siblings, final winner."""

from __future__ import annotations

import json
from typing import Any

from promptpotter.application.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.application.views.view_models import (
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
    SweepSummaryView,
)
from promptpotter.domain.results import overlap_series
from promptpotter.shared.composite import render_composite_fitness_block


def _fmt_pct(x: float | None) -> str:
    """``—`` for a measurement that was never taken. Rendering absence as ``0.0%`` is the one
    reading an operator cannot recover from: it looks like a campaign whose origin scored nothing,
    which is the shape of a broken pipeline rather than of a cycle that never got there."""
    return "—" if x is None else f"{x:.1%}"


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
    if not values:
        return ""
    out: list[str] = []
    for v in values:
        v_clamped = min(1.0, max(0.0, float(v)))
        idx = min(len(_SPARK_BLOCKS) - 1, int(v_clamped * len(_SPARK_BLOCKS)))
        out.append(_SPARK_BLOCKS[idx])
    return "".join(out)


def _render_p_best_trajectory(rd: RoundDigestView) -> list[str]:
    """Per-round P(best) sparkline section; silent when JSONL is absent (resumed cycles, pre-PoBB rounds)."""
    if not rd.p_best_trajectory:
        return []
    # The ELECTED arm first, then by final P(best) desc — a round is won on θ lift, and the arm
    # this posterior likes most is regularly not it.
    ordered = sorted(
        rd.p_best_trajectory.items(),
        key=lambda kv: (kv[0] != rd.winner_id, -(kv[1][-1] if kv[1] else 0.0)),
    )
    lines: list[str] = ["", "P(best) trajectory:", "```"]
    for cid, traj in ordered[:8]:
        if not traj:
            continue
        spark = _spark(traj)
        final = traj[-1] * 100
        suffix = ""
        if cid == rd.winner_id:
            suffix = " [winner]"
        elif final < 5.0:
            suffix = " [stopped]"
        lines.append(f"  {cid[:10]:<10} {spark}  {final:5.1f}%{suffix}")
    lines.append("```")
    return lines


def _render_round(rd: RoundDigestView, *, formula: str | None) -> list[str]:
    parts: list[str] = [
        f"### Round {rd.round} — {rd.label} ({_fmt_pct(rd.accuracy)})",
        "",
        f"- improved: **{'yes' if rd.improved else 'no'}**",
        f"- samples: {rd.total}",
        f"- composite_fitness: `{rd.composite_fitness:.4f}`",
    ]
    if rd.ability is not None:
        # The cross-round series, with the ruler it was read on beside it: accuracy above is
        # subset-relative and this is not, so they can move in opposite directions legitimately.
        parts.append(f"- ability θ: `{rd.ability.theta:+.3f}` ({rd.ability.scale()})")
    if series := overlap_series(rd.overlap):
        # The one row two rounds can be differenced on — `accuracy` above is read on whatever
        # subset this round bought, and the acquisition does not hold it still.
        parts.append(f"- overlap: {series}")
    if rd.verdict_reason:
        parts.append(f"- verdict: {rd.verdict_reason}")
    if rd.changes_description:
        parts.append(f"- changes: {rd.changes_description}")
    if rd.l1_yield < 1.0:
        n_total = rd.candidates_scored
        n_valid = max(0, n_total - rd.l1_n_no_op - rd.l1_n_duplicate - rd.l1_n_repeat)
        bits: list[str] = []
        if rd.l1_n_no_op:
            bits.append(f"{rd.l1_n_no_op} no-op")
        if rd.l1_n_duplicate:
            bits.append(f"{rd.l1_n_duplicate} dup")
        if rd.l1_n_repeat:
            bits.append(f"{rd.l1_n_repeat} repeat")
        parts.append(f"- L1 yield: {n_valid}/{n_total} ({', '.join(bits)})")
    composite_fitness_block = render_composite_fitness_block(
        rd.composite_fitness,
        rd.evaluators,
        formula,
        # THIS round's matched floor, the same one the terminal compares against — the two
        # printed different Δ for one round while this read the whole-cycle origin composite.
        parent=rd.matched_parent_composite,
        use_short_names=False,
    )
    if composite_fitness_block:
        parts += ["", "```", *composite_fitness_block, "```"]
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
        order=view.order,
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
            f"(origin {_fmt_pct(f.origin_accuracy)}, {f.n_rounds} {rounds_word})"
        )
        if f.stop_reason:
            line += f" · {f.stop_reason}"
        parts.append(line)
    parts.append("")
    return parts


def to_markdown(view: LogMdView) -> str:
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
        f"- origin: {_fmt_pct(status.origin_accuracy)}",
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
    scored_rounds = status.rounds_completed - status.gen_only_rounds
    if status.gen_only_rounds:
        parts.append(
            f"- rounds completed: {scored_rounds} scored (+ {status.gen_only_rounds} gen-only)"
        )
    else:
        parts.append(f"- rounds completed: {status.rounds_completed}")
    if status.started_at:
        parts.append(f"- started: {status.started_at}")
    if status.finished_at:
        parts.append(f"- finished: {status.finished_at}")
    parts += ["", *_render_forks(view.forks), "## Rounds", ""]

    if not view.rounds:
        parts += ["_No rounds yet._", ""]
    for rd in view.rounds:
        parts += _render_round(rd, formula=view.formula)

    parts += _render_hard_samples(view.hard_samples)

    if view.final is not None:
        parts.append("## Final Winner")
        parts.append("")
        parts += _json_block("Prompt fields", view.final.winner_prompt_fields)
        parts += _json_block("Pipeline params", view.final.winner_pipeline_params)

    return "\n".join(parts).rstrip() + "\n"


def render_sweep_summary(view: SweepSummaryView) -> str:
    lines = [
        f"# Sweep batch {view.batch_id}",
        "",
        f"- Parent cycle: `{view.parent_cycle_id}`",
        f"- Family root: `{view.family_root}`",
        f"- Started: {view.started_at}",
        f"- Completed: {view.completed_at}",
        f"- Forks minted: {view.n_minted} of {view.n_payloads}",
        "",
        "## Payloads",
        "",
        "| Source | Status | Cycle |",
        "|---|---|---|",
    ]
    for row in view.payloads:
        lines.append(f"| `{row.source_file}` | {row.status} | `{row.cycle_id}` |")
    return "\n".join(lines) + "\n"


__all__ = ["render_sweep_summary", "to_markdown"]
