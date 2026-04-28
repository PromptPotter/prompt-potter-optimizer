"""Shared prompt formatting helpers for the optimizer pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import CandidateProposal
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "TrajectoryReport",
    "build_cross_candidate_diff",
    "build_trajectory_report",
    "candidate_summaries",
    "extract_runtime_failure_fields",
    "format_axis_digest_block",
    "format_escalation_report",
    "format_pipeline_section",
    "format_runtime_failure_line",
    "format_runtime_failures_for_l3",
    "summarize_warning_inventory",
    "warning_summary",
]


def format_pipeline_section(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """Build the pipeline parameters section for L2/L3 LLM prompts."""
    if not pipeline_schema:
        return ""
    param_keys = pipeline_schema.node_param_keys()
    if not param_keys:
        return ""
    lines = ["AVAILABLE PIPELINE PARAMETERS (in pipeline execution order):\n"]
    for step_name, keys in param_keys.items():
        current_vals = {}
        if pipeline_params:
            step_cfg = pipeline_params.get(step_name, {})
            if isinstance(step_cfg, dict):
                current_vals = {k: step_cfg.get(k, "?") for k in keys}
        lines.append(f"  {step_name}: {', '.join(sorted(keys))}")
        if current_vals:
            lines.append(f"    current: {json.dumps(current_vals)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def format_axis_digest_block(
    digest: dict | None,
    key_labels: dict[str, str],
    *,
    header: str = "",
) -> str:
    """Render an AxisIndex digest dict as a labelled block. Empty digest → ``""``."""
    if not digest:
        return ""
    entries = [f"  {label}: {val}" for key, label in key_labels.items() if (val := digest.get(key))]
    if not entries:
        return ""
    return "\n".join([header, *entries]) if header else "\n".join(entries)


def extract_runtime_failure_fields(rf: dict) -> tuple[int, str, str, int]:
    """Parse the common (rate_pct, dominant, cfg_str, n_evaluated) tuple from an RF dict."""
    rate_pct = round(float(rf.get("degraded_rate", 0.0)) * 100)
    dominant = rf.get("dominant_warning", "unknown")
    cfg = rf.get("observed_config") or {}
    cfg_parts = [f"{k}={v}" for k, v in cfg.items() if k != "prompt"]
    cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
    return rate_pct, dominant, cfg_str, rf.get("total_evaluated", 0)


def format_runtime_failure_line(rf: dict, label: str = "") -> list[str]:
    """Render one runtime-failure dict as a 2-line warning block.

    L2 passes a candidate ``label`` (shown as the prefix, with dominant
    warning type appended); L3 passes none (dominant is the prefix). Shared
    verb ("degraded") and shared ``extract_runtime_failure_fields`` parser.
    """
    rate_pct, dominant, cfg_str, n = extract_runtime_failure_fields(rf)
    if label:
        head = f"  ⚠ {label[:60]} — {rate_pct}% degraded on {n} queries, dominant={dominant}"
    else:
        head = f"  ⚠ {dominant} — {rate_pct}% degraded on {n} queries"
    return [head, f"    observed_config: {cfg_str}"]


def format_runtime_failures_for_l3(runtime_failures: list[dict] | None) -> str:
    """Render the accumulated RuntimeFailure trail for L3 modify_plan (L2 self-heal exhausted)."""
    if not runtime_failures:
        return ""

    lines = [
        "L3 RUNTIME FAILURE TRAIL — L2 SELF-HEALING EXHAUSTED",
        "  (these patterns survived L2's prior strategy adjustments; replan required)",
        "",
    ]
    for rf in runtime_failures:
        lines.extend(format_runtime_failure_line(rf))

    lines.append("")
    lines.append(
        "  ↳ Required L3 action: treat these as discovered constraints on the "
        "search space. Your replan must either change pipeline_params to "
        "escape the failing region (switch model, raise max_tokens floor, "
        "swap a node) OR change the plan text to steer L1/L2 around it. "
        "Do NOT propose a plan that re-enters the same failure mode."
    )
    return "\n".join(lines)


@dataclass
class TrajectoryReport:
    """Campaign trajectory; classification ∈ healthy/plateau/oscillating/ceiling."""

    text: str
    classification: str
    description: str
    recommended_action: str


def build_trajectory_report(rounds: list[Any]) -> TrajectoryReport | None:
    """Compute trend direction, stall streak, and classification from round accuracies."""
    if not rounds or len(rounds) < 2:
        return None

    accuracies = [r.accuracy for r in rounds]
    best_acc = max(accuracies)
    best_round = accuracies.index(best_acc)
    current_acc = accuracies[-1]
    gap = best_acc - current_acc
    rounds_since_best = len(accuracies) - 1 - best_round

    deltas = [accuracies[i] - accuracies[i - 1] for i in range(1, len(accuracies))]
    recent = deltas[-5:]
    improvements = sum(1 for d in recent if d > 0.005)
    regressions = sum(1 for d in recent if d < -0.005)
    flat = len(recent) - improvements - regressions

    stall = 0
    for d in reversed(deltas):
        if abs(d) < 0.01:
            stall += 1
        else:
            break

    if improvements > len(recent) * 0.6:
        direction = "improving"
    elif regressions > len(recent) * 0.6:
        direction = "degrading"
    elif stall >= 3:
        direction = "stalled"
    else:
        direction = "oscillating"

    delta_str = ", ".join(f"{d:+.1%}" for d in recent)
    text = (
        f"Trend: {direction} | "
        f"Current: {current_acc:.1%} | Best: {best_acc:.1%} (round {best_round}) | "
        f"Gap: {gap:.1%} | Stall: {stall} rounds | "
        f"Recent deltas: [{delta_str}]"
    )

    # Need ≥ 3 rounds to classify; fall back to healthy/mixed otherwise.
    if len(rounds) < 3:
        return TrajectoryReport(
            text=text,
            classification="healthy",
            description="Too few rounds to classify",
            recommended_action="continue current approach",
        )

    if improvements >= len(recent) * 0.5 and regressions <= 1:
        classification = "healthy"
        description = f"Improving — {improvements}/{len(recent)} recent rounds improved"
        action = "continue current approach"
    elif rounds_since_best >= 5 and stall >= 3:
        classification = "ceiling"
        description = (
            f"Hard ceiling at {best_acc:.1%} (round {best_round}) — "
            f"{rounds_since_best} rounds without new best"
        )
        action = "escalate — try fundamentally different axes or strategy"
    elif improvements > 0 and regressions > 0 and abs(improvements - regressions) <= 1:
        classification = "oscillating"
        description = (
            f"Oscillating — {improvements} improvements, {regressions} regressions "
            f"in last {len(recent)} rounds"
        )
        action = "narrow search space — candidates are exploring unstable region"
    elif flat >= len(recent) * 0.6 or stall >= 3:
        classification = "plateau"
        description = (
            f"Plateau — {stall} consecutive rounds with < 1% change, gap to best: {gap:.1%}"
        )
        action = "widen search — try different axes or larger parameter ranges"
    else:
        classification = "healthy"
        description = "Mixed progress — no clear pattern"
        action = "continue current approach"

    return TrajectoryReport(
        text=text,
        classification=classification,
        description=description,
        recommended_action=action,
    )


def build_cross_candidate_diff(
    winner_results: list[dict],
    all_candidate_results: dict[str, list[dict]],
    candidate_scores: list[dict],
) -> str | None:
    """Surface missed opportunities — queries other candidates hit but winner missed."""
    if not winner_results or not all_candidate_results or len(all_candidate_results) < 2:
        return None

    winner_hits: set[str] = set()
    winner_misses: set[str] = set()
    for r in winner_results:
        q = r.get("query", "")
        if not q:
            continue
        if r.get("hit"):
            winner_hits.add(q)
        else:
            winner_misses.add(q)

    if not winner_misses:
        return None

    missed_by: dict[str, list[str]] = {}
    for cand_id, results in all_candidate_results.items():
        desc = cand_id
        for cs in candidate_scores:
            if cs.get("label") == cand_id or str(cs.get("idx")) == cand_id:
                desc = cs.get("changes_description", cand_id)[:60]
                break

        for r in results:
            q = r.get("query", "")
            if q in winner_misses and r.get("hit"):
                missed_by.setdefault(q, []).append(desc)

    if not missed_by:
        return None

    sorted_missed = sorted(missed_by.items(), key=lambda x: -len(x[1]))
    parts = []
    for q, candidates in sorted_missed[:5]:
        parts.append(f"  {q[:60]} — solved by {len(candidates)} other candidate(s)")
    total = len(missed_by)
    return (
        f"{total} missed opportunities (queries other candidates solved but winner missed):\n"
        + "\n".join(parts)
    )


def candidate_summaries(proposals: list[CandidateProposal]) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = {k: getattr(cp.osp, k) for k in PROMPT_STRING_FIELDS if getattr(cp.osp, k)}
        summary: dict = {
            "idx": i,
            "changes_description": cp.osp.lineage.changes_description or "",
        }
        if cp.node_overrides:
            summary["pipeline_params_override"] = cp.node_overrides
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        summaries.append(summary)
    return summaries


def summarize_warning_inventory(tracker: dict[str, dict]) -> str:
    """Group queries by warning type with per-query hit/miss stats."""
    by_warning: dict[str, list[tuple[str, dict]]] = {}
    for query, entry in tracker.items():
        for wtype, _count in entry.get("warnings", {}).items():
            by_warning.setdefault(wtype, []).append((query, entry))

    if not by_warning:
        return ""

    max_rounds = max((e.get("rounds_seen", 0) for e in tracker.values()), default=0)
    lines = [f"## RECURRING PIPELINE WARNINGS (across {max_rounds} rounds)"]
    for wtype, entries in sorted(by_warning.items(), key=lambda x: -len(x[1])):
        lines.append(f"  {wtype} — {len(entries)} queries affected:")
        for query, entry in sorted(
            entries,
            key=lambda x: -x[1]["warnings"].get(wtype, 0),
        )[:10]:
            wcount = entry["warnings"].get(wtype, 0)
            seen = entry["rounds_seen"]
            hits = entry["hits"]
            lines.append(f"    {query[:70]}  ({wcount}/{seen} rounds, {hits} hits)")
    return "\n".join(lines)


def warning_summary(tracker: dict[str, dict]) -> tuple[int, str]:
    """Return (warned_count, top_warning_type) from the warning inventory."""
    if not tracker:
        return 0, ""
    warned_count = sum(1 for e in tracker.values() if e.get("warnings"))
    all_wtypes: dict[str, int] = {}
    for e in tracker.values():
        for wt, c in e.get("warnings", {}).items():
            all_wtypes[wt] = all_wtypes.get(wt, 0) + c
    top_warning = max(all_wtypes, key=all_wtypes.get) if all_wtypes else ""  # type: ignore[arg-type]
    return warned_count, top_warning


def format_escalation_report(
    escalation_check_result: dict | None,
    escalation_journal: list[dict] | None,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Build the escalation diagnostics section for L2 prompts (empty if no context)."""
    if not escalation_check_result:
        return ""

    dominant = escalation_check_result.get("dominant_warning", "unknown")
    step_name = dominant.split(":")[0] if ":" in dominant else "unknown"
    rate = escalation_check_result.get("degraded_rate", 0)

    wt = escalation_check_result.get("warning_types", {})
    wt_str = ", ".join(f"{k} ({v})" for k, v in sorted(wt.items(), key=lambda x: -x[1]))

    lines = [
        f"PIPELINE STABILITY REPORT ({step_name}):\n",
        f"  Current degradation: {rate:.0%} of queries ({wt_str})",
    ]

    step_cfg = (pipeline_params or {}).get(step_name, {})
    if isinstance(step_cfg, dict) and step_cfg:
        lines.append(f"  Current {step_name} config: {json.dumps(step_cfg)}")

    lines.append("")

    if escalation_journal:
        lines.append("  Tried configs and stability:")
        for entry in escalation_journal:
            step = entry.get("problem_step", "unknown")
            ec = entry.get("step_config", {})
            prev_rate = entry.get("degraded_rate", 0)
            outcome = entry.get("outcome_degraded_rate")
            outcome_str = f" -> {outcome:.0%}" if outcome is not None else ""
            cfg_parts = [f"{k}={v!r}" for k, v in sorted(ec.items())]
            lines.append(
                f"    Round {entry.get('round', '?')}: "
                f"{step} [{', '.join(cfg_parts) or 'defaults'}]"
                f" | {prev_rate:.0%} degraded{outcome_str}"
            )
        lines.append("")

    if pipeline_schema:
        all_keys = pipeline_schema.node_param_keys()
        step_keys = all_keys.get(step_name, set())
        if step_keys:
            lines.append(f"  Available {step_name} parameters: {', '.join(sorted(step_keys))}")

    lines.append(
        "  The configurations above are all unstable. Suggest different "
        "parameter values to stabilize the pipeline."
    )
    lines.append("")
    return "\n".join(lines) + "\n"
