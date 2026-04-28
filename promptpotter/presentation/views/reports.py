"""Post-hoc summary renderers shared by CLI, notebook, and (future) webapp.

Pure functions over disk-loaded dicts (``index.json``, trial JSONs,
hard-sample artifacts). No I/O, no global state.

Sections:
- hard-sample heatmap (consumed inline by log.md)
- log.md digest
- completion banner (final feedback-cycle box)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.presentation.views.composite_render import render_composite_block
from promptpotter.presentation.views.display_primitives import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
)
from promptpotter.presentation.views.formatting import fmt_pct, render_pipeline_overrides

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RunResult
    from promptpotter.domain.pipeline_schema import PipelineSchema


# --- hard-sample-sorter heatmap -------------------------------------------

_HIT = "█"
_MISS = "▒"
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _cell_index(artifact: dict) -> dict[tuple[str, int], bool]:
    """``{(cid, sid): hit}`` lookup from the flat cells array."""
    return {(c["c"], int(c["s"])): bool(c["hit"]) for c in artifact.get("cells", [])}


def render_hard_sample_heatmap(
    artifact: dict,
    *,
    sample_query_lookup: dict[int, str] | None = None,
) -> str:
    """Pretty-print the hard-sample-sorter heatmap + hardness leaderboard.

    Returns an empty string for disabled or empty-observation artifacts so
    callers can append unconditionally. All truncation decisions live in the
    builder; this renderer just draws whatever axes it was handed.
    """
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


# --- log.md digest --------------------------------------------------------


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

    # Per-round formula source: stored on index.json::final at finalize.
    # log.md is the permanent record so it carries the FULL formula
    # (allowed to wrap), with full evaluator names. The 3-line short form
    # is for live surfaces only.
    formula = final.get("scorer_round_formula")
    baseline_composite = final.get("baseline_composite")

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
            f"- composite: `{trial.get('composite', 0.0) or 0.0:.4f}`",
        ]
        if changes := (lineage.get("changes_description") or "").strip():
            parts.append(f"- changes: {changes}")
        if directive := (osp.get("l2_directive") or "").strip():
            parts.append(f"- L2 directive: {directive}")
        composite_block = render_composite_block(
            trial.get("composite", 0.0) or 0.0,
            dict(trial.get("evaluators") or {}),
            formula,
            baseline=baseline_composite,
            use_short_names=False,
        )
        if composite_block:
            parts += ["", "```", *composite_block, "```"]
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


# --- completion banner (printed after a feedback cycle finishes) ----------


def render_completion(
    result: RunResult,
    *,
    best_round: dict,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render the closing summary box (interrupted vs complete) + pipeline overrides."""
    interrupted = result.stop_reason == "interrupted"
    title = (
        f"{YELLOW}{BOLD}INTERRUPTED{RESET} — stopped by user"
        if interrupted
        else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
    )

    fields: list[str] = [
        f"Rounds       {result.n_rounds:<15d}"
        f"Best         {best_round['accuracy']:.1%} (round {best_round['round']})",
        f"Stop reason  {result.stop_reason}",
    ]
    if interrupted:
        fields.append("Resume: re-run this cell -- rounds auto-restore")
    if result.cycle_id:
        fields.append(f"Cycle ID     {result.cycle_id}")
    if result.session_id:
        fields.append(f"Session      {result.session_id}")
    if result.langfuse_trace_id:
        fields.append(f"Langfuse     {result.langfuse_trace_id}")

    out = ["", _dbox_block(title, *fields)]

    if overrides_block := render_pipeline_overrides(result.winner_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overrides_block)

    return "\n".join(out)
