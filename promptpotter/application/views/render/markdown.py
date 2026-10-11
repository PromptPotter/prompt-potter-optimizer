from __future__ import annotations

import json
from typing import Any

from promptpotter.application.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct as _fmt_pct
from promptpotter.application.views.render.primitives import fmt_fitness, overlap_series
from promptpotter.application.views.view_models import (
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.domain.phases import STOP_REASON_INFO, StopReason
from promptpotter.domain.results import DISPLAY_METRIC_INFO, RunStanding
from promptpotter.domain.ruler import THETA_CAVEAT_INFO
from promptpotter.domain.spend import RATE_PRICED_LABEL, CloseSpend, calls_rate_priced
from promptpotter.shared.composite import render_composite_fitness_block


def _ending_lines(stop_reason: StopReason | None) -> list[str]:
    if stop_reason is None:
        return ["- ended: not yet"]
    info = STOP_REASON_INFO[stop_reason]
    return [
        f"- ended: **{info.label}** (`{stop_reason.value}`)",
        *([f"- next: {info.next_step}"] if info.next_step else []),
    ]


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
    if not rd.p_best_trajectory:
        return []
    # The ELECTED arm first: a round is won on θ lift, and the highest final P(best) is often not it.
    ordered = sorted(
        rd.p_best_trajectory.items(),
        key=lambda kv: (kv[0] != rd.winner_id, -(kv[1][-1] if kv[1] else 0.0)),
    )
    # Each row is one arm against ITS OWN priors, so two finals side by side are no comparison.
    lines: list[str] = ["", "P(best) trajectory (each arm against its own priors):", "```"]
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
        label = rd.candidate_labels.get(cid)
        name = f"{label} ({cid[:10]})" if label else cid[:10]
        lines.append(f"  {name:<19} {spark}  {final:5.1f}%{suffix}")
    lines.append("```")
    return lines


def _render_round_cost(rd: RoundDigestView) -> str:
    """PER BUCKET, never pooled: one pooled cache ratio is the largest bucket's share under everyone's name."""
    if rd.spend is None:
        return ""
    bits: list[str] = []
    for kind, bucket in rd.spend.by_kind.items():
        if not bucket.sent:
            if bucket.incurred_usd > 0:
                bits.append(f"{kind} $0.0000 (${bucket.incurred_usd:.4f} replayed)")
            continue
        priced = (
            f" + ${bucket.rate_priced_usd:.4f} {RATE_PRICED_LABEL}"
            if calls_rate_priced(bucket.rate_priced_usd)
            else ""
        )
        bits.append(f"{kind} ${bucket.used_usd:.4f}{priced} {bucket.prefix.badge}")
    by_bucket = f" ({' · '.join(bits)})" if bits else ""
    return f"- spend: {rd.spend.billed_beside_incurred()}{by_bucket}"


def _render_round(rd: RoundDigestView, *, formula: str | None) -> list[str]:
    parts: list[str] = [
        f"### Round {rd.round} — {rd.label} ({_fmt_pct(rd.accuracy)})",
        "",
        f"- improved: **{'yes' if rd.improved else 'no'}**",
        f"- samples: {rd.total}",
        f"- composite_fitness: `{fmt_fitness(rd.composite_fitness)}`",
    ]
    if rd.ability is not None:
        parts.append(
            f"- {DISPLAY_METRIC_INFO['ability'].label}: `{rd.ability.theta:+.3f}` "
            f"({rd.ability.scale()})"
        )
        if rd.ability.caveat is not None:
            parts.append(
                f"- θ caveat: `{rd.ability.caveat.value}` — "
                f"{THETA_CAVEAT_INFO[rd.ability.caveat].head}"
            )
    if series := overlap_series(rd.overlap):
        parts.append(f"- overlap: {series}")
    if rd.verdict_reason:
        parts.append(f"- verdict: {rd.verdict_reason}")
    if cost := _render_round_cost(rd):
        parts.append(cost)
    if rd.changes_description:
        parts.append(f"- changes: {rd.changes_description}")
    parts += [f"- {f.label}: {f.text}" for f in rd.facts if f.kind == "stat"]
    if rd.composite_fitness is not None:
        composite_fitness_block = render_composite_fitness_block(
            rd.composite_fitness,
            rd.evaluators,
            formula,
            reference=rd.composite_floor,
            use_short_names=False,
        )
        parts += ["", "```", *composite_fitness_block, "```"]
    for note in (f for f in rd.facts if f.kind == "note"):
        parts += ["", "> " + note.text.replace("\n", "\n> ")]
    parts += _render_p_best_trajectory(rd)
    parts.append("")
    return parts


def _render_hard_samples(view: HardSamplesView | None) -> list[str]:
    if view is None:
        return []
    heatmap = render_hard_sample_heatmap(view).strip()
    if not heatmap:
        return []
    return ["## Hard Samples", "", "```", heatmap, "```", ""]


def _selection(standing: RunStanding | None) -> str:
    return "nothing selected" if standing is None else standing.selection_line


def _spent(spent: CloseSpend) -> str:
    search = "unpriced" if spent.search_usd is None else f"${spent.search_usd:.4f}"
    return (
        f"search {search} · billed ${spent.billed_usd:.4f} · "
        f"{RATE_PRICED_LABEL} ${spent.rate_priced_usd:.4f} · {spent.calls} calls · "
        f"{spent.tokens} tokens · {spent.worked_s:.0f}s worked"
    )


def _render_forks(forks: tuple[ForkSummaryView, ...]) -> list[str]:
    if not forks:
        return []
    parts = ["## Forks", ""]
    for f in forks:
        short = f.cycle_id.split("_", 1)[-1] if "_" in f.cycle_id else f.cycle_id
        rounds_word = "round" if f.n_rounds == 1 else "rounds"
        line = f"- `{short}` — {f.kind} · {_selection(f.standing)} ({f.n_rounds} {rounds_word})"
        if f.stop_reason is not None:
            line += f" · {f.stop_reason.value}"
        parts.append(line)
    parts.append("")
    return parts


def to_markdown(view: LogMdView) -> str:
    status = view.status
    parts: list[str] = [
        f"# Campaign {status.campaign_id or '(unknown cycle)'}",
        "",
    ]

    parts += [
        "## Status",
        "",
        *([f"- optimizer: `{status.optimizer}`"] if status.optimizer else []),
        *_ending_lines(status.stop_reason),
        f"- selection: {_selection(status.standing)}",
    ]
    if status.standing is not None and status.standing.spent is not None:
        parts.append(f"- cost at the last close: {_spent(status.standing.spent)}")
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
        parts += _json_block("Prompt fields", view.final.result_prompt_fields)
        parts += _json_block("Pipeline params", view.final.result_pipeline_params)

    return "\n".join(parts).rstrip() + "\n"


__all__ = ["to_markdown"]
