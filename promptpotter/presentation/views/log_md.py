"""Human-readable per-cycle digest written to ``campaigns/{cycle_id}/log.md``.

Pure derived render — consumes ``index.json`` + per-round trial dicts (and
optionally the hard-samples artifact dict). Regenerated each round-complete
and at finalize. Safe to delete; nothing reads it for state.
"""

from __future__ import annotations

import json
from typing import Any

from promptpotter.presentation.views.formatting import fmt_pct
from promptpotter.presentation.views.hard_sample_heatmap import render_hard_sample_heatmap

__all__ = ["render_log_md"]


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


def render_log_md(
    index: dict[str, Any],
    trials: list[dict[str, Any]],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
) -> str:
    """Render ``log.md`` from ``index.json`` + a list of trial dicts."""
    final = index.get("final") or {}
    best_round = index.get("best_round")
    parts: list[str] = [
        f"# Campaign {index.get('campaign_id') or '(unknown cycle)'}",
        "",
    ]
    if parent := index.get("parent_session_id"):
        parts += [f"_session: `{parent}`_", ""]

    parts += [
        "## Status",
        "",
        f"- status: **{index.get('status', 'active')}**",
        f"- stop reason: `{final.get('stop_reason') or index.get('stop_reason') or '(running)'}`",
        f"- baseline: {fmt_pct(index.get('baseline_accuracy', 0.0) or 0.0)}",
        (
            f"- best: {fmt_pct(index.get('best_accuracy', 0.0) or 0.0)}"
            + (f" (round {best_round})" if best_round is not None else "")
        ),
        f"- rounds completed: {index.get('n_trials', 0)}",
    ]
    for k, label in (("started_at", "started"), ("finished_at", "finished")):
        if v := final.get(k):
            parts.append(f"- {label}: {v}")
    parts += ["", "## Rounds", ""]

    if not trials:
        parts += ["_No rounds yet._", ""]
    for trial in trials:
        osp = trial.get("opt_search_point") or {}
        lineage = osp.get("lineage") or {}
        rnd = trial.get("round", "?")
        label = (trial.get("label") or "").strip() or f"round_{rnd}"
        parts += [
            f"### Round {rnd} — {label} ({fmt_pct(trial.get('accuracy', 0.0) or 0.0)})",
            "",
            f"- improved: **{'yes' if trial.get('improved') else 'no'}**",
            f"- hits: {trial.get('hits', 0)}/{trial.get('total', 0)}",
        ]
        if changes := (lineage.get("changes_description") or "").strip():
            parts.append(f"- changes: {changes}")
        if directive := (osp.get("l2_directive") or "").strip():
            parts.append(f"- L2 directive: {directive}")
        if critique := (osp.get("l1_critique_text") or "").strip():
            parts += ["", "> " + critique.replace("\n", "\n> ")]
        parts.append("")

    if heatmap := render_hard_sample_heatmap(hard_samples_artifact or {}).strip():
        parts += ["## Hard Samples", "", "```", heatmap, "```", ""]

    if final:
        parts.append("## Final Winner")
        parts.append("")
        parts += _json_block("Prompt fields", final.get("winner_prompt_fields"))
        parts += _json_block("Pipeline params", final.get("winner_pipeline_params"))

    return "\n".join(parts).rstrip() + "\n"
