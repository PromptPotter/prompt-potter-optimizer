"""Post-hoc + summary renderers shared by CLI, notebook, and (future) webapp.

Pure functions over disk-loaded dicts (``index.json``, trial JSONs,
``dashboard.json``, hard-sample artifacts) and in-memory lists
(``campaign_rounds``, ``scoring_set_events``). No I/O, no global state.

Sections:
- round-table renderers (campaign summary, flip tracking, lineage)
- scoring-set evolution (swap log + Rasch fit)
- hard-sample heatmap
- log.md digest (consumes the heatmap inline)
- completion banner (final feedback-cycle box)
- dashboard / show-status
- feedback-cycle preflight
- experiment dashboard (overview + detail by experiment ID)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.config import compute_preflight_metrics
from promptpotter.application.campaign.cycle_store import resolve_campaign_id
from promptpotter.application.campaign.data import (
    extract_campaign_baseline,
    summarize_dataset_runs,
)
from promptpotter.application.campaign.utils import (
    diff_campaign_config,
    load_and_apply_experiment,
)
from promptpotter.application.scoring.metrics import count_failures
from promptpotter.presentation.views.display_primitives import (
    BOLD,
    CYAN,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
)
from promptpotter.presentation.views.formatting import fmt_pct, render_pipeline_overrides

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.results import RunResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


# --- round-table renderers (per-cycle trajectory / flips / lineage) -------


def _lineage_refs(rd: dict) -> tuple[str, str, str]:
    """Pull ``(id, parent_id, changes_description)`` from a round dict.

    Tries the disk shape (``opt_search_point`` dict from ``model_dump``,
    where lineage is nested under ``opt_search_point["lineage"]``) first,
    then the notebook shape (``prompt_fields`` as an OptSearchPoint object
    with a ``.lineage`` attribute), then falls back to ``prompt_fields_id``
    from the campaign-index summary.
    """
    osp = rd.get("opt_search_point")
    if isinstance(osp, dict):
        lin = osp.get("lineage") or {}
        return (
            str(lin.get("id", "")),
            str(lin.get("parent_id", "") or ""),
            str(lin.get("changes_description", "") or ""),
        )
    pf = rd.get("prompt_fields")
    if pf is not None and not isinstance(pf, dict):
        lin = getattr(pf, "lineage", None)
        if lin is not None:
            return (
                str(getattr(lin, "id", "") or ""),
                str(getattr(lin, "parent_id", "") or ""),
                str(getattr(lin, "changes_description", "") or ""),
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
    """Baseline → final per-query flip delta. Empty string when < 2 rounds (caller falls back)."""
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


# --- scoring-set evolution (Rasch swap log) -------------------------------


def render_scoring_set(
    events: list[dict],
    *,
    sample_query_lookup: dict[int, str] | None = None,
    hardness_top_n: int = 10,
) -> str:
    """Pretty-print the scoring-set swap log + latest hardness leaderboard.

    Returns an empty string when *events* is empty so callers can append
    unconditionally without leaking a stray header.
    """
    if not events:
        return ""

    lookup = sample_query_lookup or {}

    lines = [
        "\nSCORING SET",
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
            size = e.get("new_scoring_set_size", "-")
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


def collect_scoring_set_events(rounds: list[dict]) -> list[dict]:
    """Walk a list of trial dicts and return their accumulated ``scoring_set_events``.

    Each trial JSON carries the running event log up to its round. Reading
    the latest trial gives the full history; reading earlier trials gives
    progressive snapshots.
    """
    seen: dict[int, dict] = {}
    for rd in rounds:
        for ev in rd.get("scoring_set_events", []) or []:
            r = int(ev.get("round", -1))
            seen[r] = ev
    return [seen[r] for r in sorted(seen)]


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


# --- dashboard / show-status ---------------------------------------------


def _get(d: dict, key: str, default: Any = "-") -> Any:
    v = d.get(key, default)
    return default if v is None or v == "" else v


def render_dashboard(dashboard: dict) -> str:
    """Pretty-print the slim ``dashboard.json`` state."""
    if not dashboard:
        return "No dashboard data."

    d = dashboard
    lines = [
        "\nDASHBOARD",
        "=" * 70,
        f"  phase         : {_get(d, 'phase')}",
        f"  round         : {_get(d, 'round')}",
        f"  layer         : {_get(d, 'layer')}",
        f"  candidate     : {_get(d, 'candidate')}",
        f"  query         : {_get(d, 'query')}",
        f"  patience      : {_get(d, 'patience')}",
        f"  baseline      : {fmt_pct(d.get('baseline'))}",
        f"  best          : {fmt_pct(d.get('best'))}",
        f"  current       : {fmt_pct(d.get('current_acc'))}",
        f"  cycle_id      : {_get(d, 'cycle_id')}",
        f"  degraded      : {_get(d, 'degraded_count', 0)}",
        f"  errors        : {_get(d, 'error_count', 0)}",
        f"  queries_total : {_get(d, 'total_queries_scored', 0)}",
        f"  backend_calls : {_get(d, 'total_backend_calls', 0)}",
        f"  n_variants    : {_get(d, 'n_variants')}",
        f"  sp_budget     : {_get(d, 'sp_budget_ttest')}",
    ]
    if d.get("query_in_flight"):
        payload = d.get("current_query_payload") or ""
        lines += [
            "  in flight     :",
            f"    since       : {_get(d, 'query_started_at')}",
            f"    payload     : {payload}",
        ]

    hitl = d.get("hitl") or {}
    lines += [
        "",
        "HITL",
        "=" * 70,
        f"  requested_state      : {_get(hitl, 'requested_state')}",
        f"  pause_point          : {_get(hitl, 'pause_point')}",
        f"  stop_reason          : {_get(hitl, 'stop_reason')}",
    ]
    return "\n".join(lines)


def render_status(
    dashboard: dict,
    control: dict | None = None,
    result: dict | None = None,
) -> str:
    """Human form for ``show-status`` — dashboard + last result.

    HITL signals are nested inside ``dashboard["hitl"]`` so the separate
    ``control`` arg is accepted but ignored (kept for caller shape).
    """
    del control
    parts = [render_dashboard(dashboard)]
    if result:
        parts.append(
            "\nLAST RESULT\n" + "=" * 70 + "\n"
            f"  best_accuracy : {fmt_pct(result.get('best_accuracy'))}\n"
            f"  best_round    : {result.get('best_round', '-')}\n"
            f"  n_rounds      : {result.get('n_rounds', '-')}\n"
            f"  stop_reason   : {result.get('stop_reason', '-')}"
        )
    return "\n".join(parts)


# --- feedback-cycle preflight --------------------------------------------


def render_preflight(
    config: CampaignConfig,
    session: Session | None,
    campaign_rounds: list,
    dataset: list,
    *,
    exclude_nodes: list[str] | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render the two-section preflight walkthrough as a string."""
    bl = extract_campaign_baseline(campaign_rounds)
    baseline_acc = bl.baseline_acc
    instruction = bl.instruction
    baseline_results = bl.baseline_results

    instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    instr_preview = instr_preview or "(empty)"

    excluded = list(exclude_nodes or config.exclude_nodes)
    schema = pipeline_schema or (session.pipeline_schema if session else None)
    m = compute_preflight_metrics(config, session, len(dataset), exclude_nodes=excluded)

    opt = config.optimization
    model_name = config.optimizer_llm.model or "(default)"
    n_failures = count_failures(baseline_results) if baseline_results else 0
    n_prior = len(baseline_results) if baseline_results else 0
    scoring = "composite (pipeline-aware)" if schema else "accuracy"

    lines: list[str] = [
        "",
        "=" * 70,
        f"  {BOLD}FEEDBACK CYCLE PRE-FLIGHT{RESET}",
        "=" * 70,
        f"  Baseline accuracy      : {baseline_acc:.1%}",
        f"  Baseline prompt        : {instr_preview}",
        "  " + "-" * 66,
        f"  Max rounds             : {opt.max_rounds or 'unlimited'}",
        f"  Candidates per round   : {opt.n_variants}",
        f"  Queries per eval       : {m.queries_label}",
        f"  Improvement threshold  : {opt.improvement_threshold:.1%}",
        f"  Patience (L1)          : {opt.l1_patience} rounds",
        f"  L2 (refine context)    : {m.l2_label}",
        f"  L3 (modify plan)       : {m.l3_label}",
        "  " + "-" * 66,
        f"  Candidate model        : {model_name}",
        f"  Creativity             : {opt.creativity}",
        f"  Pipeline               : {m.pipeline_label}",
    ]
    if m.nodes_detail:
        lines.append(f"    Nodes                : {m.nodes_detail}")
    if excluded:
        lines.append(f"    Excluded             : {', '.join(excluded)}")

    lines += [
        "",
        f"  {BOLD}ROUND PIPELINE{RESET} (what happens each round)",
        "  " + "-" * 66,
        f"  {CYAN}1. BASELINE INPUT{RESET}",
        f"     Prompt: {instr_preview}",
        f"     Accuracy: {baseline_acc:.1%}  |  Prior results: {n_prior}",
        f"  {CYAN}2. FAILURE ANALYSIS{RESET}",
        f"     Up to {opt.max_failures} failures extracted (currently {n_failures} available)",
        f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}",
        "     LLM generates from failures + L1 critique + SearchMemory",
        f"  {CYAN}4. LLM CANDIDATE GENERATION{RESET}",
        f"     Model: {model_name}  |  Temperature: {opt.creativity}",
        f"     Candidates: {opt.n_variants}",
        "     Output: prompt + pipeline_params_override per candidate",
        f"  {CYAN}5. BACKEND EVALUATION{RESET}",
        f"     Queries per candidate: {m.queries_label}",
        f"     Scoring: {scoring}",
        f"  {CYAN}6. WINNER SELECTION{RESET}",
        f"     Criterion: best accuracy >= baseline + {opt.improvement_threshold:.1%}",
        f"  {CYAN}7. LOOP CONTROL{RESET}",
        f"     Patience: {opt.l1_patience} stalls → stop  |  L2: {m.l2_label}  |  L3: {m.l3_label}",
        "  " + "-" * 66,
    ]

    if m.est_calls is not None:
        lines.append(
            f"  Est. backend calls     : ~{m.est_calls}"
            f"  ({opt.max_rounds}r, {opt.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )
    else:
        per_round = m.eff_queries + (opt.n_variants - 1) * int(m.eff_queries * 0.6)
        lines.append(
            f"  Est. backend calls/round: ~{per_round}"
            f"  ({opt.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )

    lines.append("=" * 70)
    return "\n".join(lines)


# --- experiment dashboard (overview + detail by experiment ID) -----------


def _resume_hint(
    status: str,
    n_trials: int,
    *,
    indent: str = "  ",
    is_matching: bool = True,
) -> str:
    """One-line resume/replay/fresh hint for a campaign."""
    if not is_matching:
        return f"{indent}→ Config does NOT match — update campaign_config to resume"
    if status == "completed" and n_trials > 0:
        return f"{indent}→ Re-run will REPLAY from cache (campaign completed)"
    if n_trials > 0:
        return f"{indent}→ Re-run will RESUME from round {n_trials}"
    return f"{indent}→ Re-run starts fresh"


def _format_campaign_summary(
    store: Stores,
    backend_id: str,
    campaign: dict,
    *,
    active_id: str | None = None,
    show_updated: bool = False,
) -> list[str]:
    """One campaign's summary block (header line + inline config + optional updated)."""
    cid = campaign["campaign_id"]
    status = campaign["status"]
    n = campaign["n_trials"]
    best = f"{campaign['best_accuracy']:.1%}"
    base = f"{campaign['baseline_accuracy']:.1%}"
    is_active = cid == active_id

    out: list[str] = []
    if active_id is not None:
        marker = "  ●" if is_active else "   "
        tag = "  <-- active" if is_active else ""
        out.append(f"{marker} {cid}  {status:<12} {n} rounds  best={best}  base={base}{tag}")
    else:
        out.append(f"  {cid}  {status}  {n} rounds  best={best}  base={base}")

    full = store.campaigns.load(backend_id, cid)
    cfg = full.get("config", {}) if full else {}
    if cfg:
        model = str(cfg.get("model", "?"))[:30]
        patience = cfg.get("l1_patience", "?")
        max_r = cfg.get("max_rounds", "?")
        sample = cfg.get("sp_budget_ttest", "?")
        indent = "      " if active_id is not None else "    "
        out.append(f"{indent}patience={patience}  rounds={max_r}  sample={sample}  model={model}")

    if show_updated:
        updated = campaign["updated_at"][:16].replace("T", " ")
        out.append(f"    updated: {updated}")
    return out


def _config_diff(diffs: dict, campaign_id: str) -> str:
    """Pretty-print a diff dict (output of ``diff_campaign_config``)."""
    lines = ["", f"Diff: current config vs {campaign_id}", "=" * 60]
    if not diffs:
        lines.append("  (identical — will resume this campaign)")
    else:
        for k, v in diffs.items():
            sv_str = str(v.stored)[:40] if v.stored is not None else "(none)"
            cv_str = str(v.current)[:40] if v.current is not None else "(none)"
            lines.append(f"  {k}: {sv_str} → {cv_str}")
    lines.append("=" * 60)
    return "\n".join(lines)


def _detect_active_campaign(
    session: Session | None,
    campaign_config: CampaignConfig | None,
    dataset: list | None,
    baseline_prompt_fields: dict | None,
) -> str | None:
    """Compute the cycle hash of the current config + dataset, or None."""
    if not (campaign_config and dataset):
        return None
    try:
        from promptpotter.domain.cycle_identity import cycle_config_identity
        from promptpotter.domain.opt_search_point import OptSearchPoint

        ps = session.pipeline_schema if session else None
        base_pp = ps.to_pipeline_params() if ps else {}
        baseline_osp = OptSearchPoint.from_prompt_fields(baseline_prompt_fields or {})
        baseline_jsp = baseline_osp.to_job_search_point(base_pipeline_params=base_pp, schema=ps)
        return cycle_config_identity(baseline_jsp, dataset)
    except Exception:
        logger.debug("Could not compute active campaign ID", exc_info=True)
        return None


def render_experiment_dashboard(
    *,
    store: Stores | None = None,
    backend_id: str = "",
    experiment_id: str | None = None,
    campaign_config: CampaignConfig | None = None,
    dataset: list | None = None,
    pipeline_params: dict | None = None,
    baseline_prompt_fields: dict | None = None,
    session: Session | None = None,
) -> tuple[str, dict, CampaignConfig | None]:
    """Unified experiment dashboard — overview or detail by experiment ID.

    Returns ``(rendered_text, pipeline_params, campaign_config)``. When
    ``experiment_id`` is provided, applies stored overrides via
    ``load_and_apply_experiment``; the returned ``campaign_config`` reflects
    those overrides so the notebook can write them back.
    """
    if session is not None:
        store = store or session.store
        backend_id = backend_id or session.backend_id

    pre_lines: list[str] = []
    if experiment_id and session is not None and campaign_config is not None:
        campaign_config, pipeline_params = load_and_apply_experiment(
            session, campaign_config, experiment_id, pipeline_params
        )
        if pipeline_params:
            session.pipeline_params = pipeline_params
        pre_lines.append(f"  Loaded config from experiment {experiment_id}")

    full_id: str | None = None
    if experiment_id is not None:
        assert store is not None
        full_id = resolve_campaign_id(store, backend_id, experiment_id)
        if full_id is None:
            return "\n".join(pre_lines), pipeline_params or {}, campaign_config

    active_id = _detect_active_campaign(session, campaign_config, dataset, baseline_prompt_fields)

    # Detail mode
    if full_id is not None:
        assert store is not None
        campaign = store.campaigns.load(backend_id, full_id)
        if campaign is None:
            pre_lines.append(f"Campaign {full_id} not found.")
            return "\n".join(pre_lines), pipeline_params or {}, campaign_config

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        lines = [
            *pre_lines,
            "",
            "=" * 72,
            f"  EXPERIMENT: {full_id}",
            f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Base: {base}",
            f"  Updated: {updated}",
        ]

        cfg = campaign.get("config", {})
        if cfg:
            lines.append("")
            lines.append("  Config (copy to campaign_config to resume):")
            for k in (
                "max_rounds",
                "l1_patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sp_budget_ttest",
                "seed",
            ):
                if k in cfg:
                    lines.append(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                pp_keys = sorted(pp.keys())
                pp_summary = ", ".join(
                    f"{k}={str(pp[k])[:25]}" if not isinstance(pp[k], (dict, list)) else f"{k}=..."
                    for k in pp_keys[:6]
                )
                if len(pp_keys) > 6:
                    pp_summary += f" +{len(pp_keys) - 6} more"
                lines.append(f"    pipeline_params: {{{pp_summary}}}")

        if campaign_config is not None:
            schema = session.pipeline_schema if session else None
            diffs = diff_campaign_config(campaign.get("config", {}), campaign_config, schema)
            lines.append(_config_diff(diffs, full_id))

        is_active = full_id == active_id
        lines.append(_resume_hint(status, n, is_matching=is_active))
        lines.append("=" * 72)
        lines.append("")
        return "\n".join(lines), pipeline_params or {}, campaign_config

    # Overview mode
    assert store is not None
    runs = store.dataset_runs.list_all(backend_id)
    run_summary = summarize_dataset_runs(runs)
    source_str = ", ".join(f"{v} {k}" for k, v in sorted(run_summary.by_source.items()))
    campaigns = store.campaigns.list_all(backend_id)

    lines = [
        *pre_lines,
        "",
        "=" * 72,
        f"  EXPERIMENT DASHBOARD ({backend_id})",
        "=" * 72,
    ]
    if runs:
        lines.append(f"  Dataset runs: {run_summary.total} total ({source_str})")
        if run_summary.best_accuracy > 0:
            lines.append(
                f"  Best result: {run_summary.best_accuracy:.1%} ({run_summary.best_name})"
            )
    else:
        lines.append("  Dataset runs: none")

    if not campaigns:
        lines.append("")
        lines.append("  No campaigns yet.")
    else:
        lines.append("")
        lines.append("  Campaigns (most recent first):")
        for c in sorted(campaigns, key=lambda x: x["updated_at"], reverse=True):
            lines.extend(_format_campaign_summary(store, backend_id, c, active_id=active_id))
            if c["campaign_id"] == active_id:
                lines.append(_resume_hint(c["status"], c["n_trials"], indent="      "))
            lines.append("")

    lines.append("=" * 72)
    lines.append('  Set experiment_id="<short_id>" to see full config and diff')
    if active_id:
        lines.append(f"  Active: {active_id}")
    elif campaign_config is not None:
        lines.append("  No matching campaign — feedback cycle will create new")
    lines.append("")

    return "\n".join(lines), pipeline_params or {}, campaign_config
