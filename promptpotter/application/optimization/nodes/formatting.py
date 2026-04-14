"""Shared prompt formatting helpers for the optimizer pipeline.

Pure string formatting — no I/O, no LLM calls. Used by
l1_optimizer (L1 generate) for prompt assembly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.domain.search_point import TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.analysis import FailureAnalysis
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "L1PromptData",
    "L2IntelligenceData",
    "TrajectoryReport",
    "assess_candidate_diversity",
    "build_candidate_comparison",
    "build_trajectory_report",
    "candidate_summaries",
    "format_context_sections",
    "format_escalation_report",
    "format_l2_intelligence",
    "format_pipeline_section",
    "format_search_memory_block",
    "summarize_warning_inventory",
    "warning_summary",
]


def format_pipeline_section(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """Build the pipeline parameters section for L2/L3 LLM prompts.

    Returns an empty string when no schema is available, which causes the
    pipeline_params instructions to be omitted from the prompt.
    """
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


@dataclass
class L1PromptData:
    """Data bundle for ``format_context_sections()``."""

    task_context: TaskDecomposition | None = None
    critique_text: str = ""
    l2_directive: str = ""
    thinking_styles: list[str] | None = None
    plan: str = ""
    warning_inventory: dict | None = None
    escalation_journal: list[dict] | None = None
    is_probe_round: bool = False
    recon_brief: ReconBrief | None = None
    scan_compact: bool = False
    failure_analysis: FailureAnalysis | None = None
    search_memory_digest: dict | None = None
    pipeline_schema_text: str = ""


def format_search_memory_block(sm_digest: dict | None, key_labels: dict[str, str]) -> str:
    """Build HISTORICAL INTELLIGENCE block from search memory digest dict."""
    if not sm_digest:
        return ""
    lines = ["HISTORICAL INTELLIGENCE:"]
    for key, label in key_labels.items():
        val = sm_digest.get(key)
        if val:
            lines.append(f"  {label}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_runtime_failures_for_l3(runtime_failures: list[dict] | None) -> str:
    """Render the accumulated RuntimeFailure trail for L3 ``modify_plan``.

    This is the L2→L3 escalation signal on the runtime-failures rail:
    every entry in ``runtime_failures`` is a pattern that L2 has already
    seen (and tried to self-heal around) across prior rounds, yet the
    pattern survived. L3's job is to replan — change pipeline_params,
    node composition, or strategy plan — so L1/L2 stop re-entering the
    same failure region.
    """
    if not runtime_failures:
        return ""

    lines = [
        "L3 RUNTIME FAILURE TRAIL — L2 SELF-HEALING EXHAUSTED",
        "  (these patterns survived L2's prior strategy adjustments; replan required)",
        "",
    ]
    for rf in runtime_failures:
        rate_pct = round(float(rf.get("degraded_rate", 0.0)) * 100)
        dominant = rf.get("dominant_warning", "unknown")
        cfg = rf.get("observed_config") or {}
        cfg_parts = [f"{k}={v}" for k, v in cfg.items() if k not in ("prompt",)]
        cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
        lines.append(
            f"  ⚠ {dominant} — {rate_pct}% degradation on {rf.get('total_evaluated', 0)} queries"
        )
        lines.append(f"    observed_config: {cfg_str}")

    lines.append("")
    lines.append(
        "  ↳ Required L3 action: treat these as discovered constraints on the "
        "search space. Your replan must either change pipeline_params to "
        "escape the failing region (switch model, raise max_tokens floor, "
        "swap a node) OR change the plan text to steer L1/L2 around it. "
        "Do NOT propose a plan that re-enters the same failure mode."
    )
    return "\n".join(lines)


def format_context_sections(ctx: L1PromptData) -> str:
    """Build the L1 intelligence bundle — scan, escalation, critique, directives, plan."""
    sections: list[str] = []

    # Pipeline schema — valid nodes and parameters
    if ctx.pipeline_schema_text:
        sections.append(ctx.pipeline_schema_text)

    # Scan analytics — full on first round, sensitivity-only thereafter
    sc = ctx.recon_brief
    if sc:
        if ctx.scan_compact and sc.sensitivity_text:
            sections.append(f"SCAN:\n{sc.sensitivity_text}")
        else:
            scan_parts = [f"SCAN:\nTested values per axis:\n{sc.tested_values}"]
            if sc.sensitivity_text:
                scan_parts.append(f"Top sensitivity drivers:\n{sc.sensitivity_text}")
            sections.append("\n".join(scan_parts))

    # Failure analysis (Wave 1c)
    fa = ctx.failure_analysis
    if fa and fa.patterns:
        fa_lines = [f"FAILURE ANALYSIS ({fa.total_failures} failures / {fa.total_results} total):"]
        for i, pat in enumerate(fa.patterns[:3], 1):
            fa_lines.append(f"  {i}. {pat.name} — {pat.query_count} queries ({pat.fraction:.0%})")
            if pat.example_queries:
                examples = ", ".join(f'"{q}"' for q in pat.example_queries[:2])
                fa_lines.append(f"     Examples: {examples}")
            sig = {
                k: v
                for k, v in pat.signals.items()
                if k not in ("error", "degraded", "total_time_ms")
            }
            if sig:
                sig_str = ", ".join(f"{k}={v}" for k, v in list(sig.items())[:4])
                fa_lines.append(f"     Signals: {sig_str}")
        # Improvement directions
        fa_lines.append("IMPROVEMENT DIRECTIONS:")
        for i, pat in enumerate(fa.patterns[:3], 1):
            fa_lines.append(f"  {i}. Address {pat.name} ({pat.fraction:.0%} of failures)")
        sections.append("\n".join(fa_lines))

    # Historical intelligence from SearchMemory (Wave 3c)
    hi = format_search_memory_block(
        ctx.search_memory_digest,
        {
            "failure_clusters": "Common failure patterns",
            "dead_queries": "Dead queries (never hit)",
            "top_axes": "High-impact axes",
            "top_values": "Best-performing values",
        },
    )
    if hi:
        sections.append(hi)

    # Task context
    if ctx.task_context:
        tc_lines = "\n".join(f"  {k}: {v}" for k, v in ctx.task_context.items() if v)
        if tc_lines:
            sections.append(f"CONTEXT:\n{tc_lines}")

    # Escalation / probe — unified from escalation_journal.
    # Probe rounds always show (per-query warning detail IS the actionable data).
    # Non-probe: skip when l2_directive present — directive already absorbed
    # escalation data via L2 (no raw+digest double-exposure).
    escalation_journal = ctx.escalation_journal
    if escalation_journal:
        if ctx.is_probe_round:
            # Probe round — enriched with warning inventory and history
            probe_lines = [
                "PROBE ROUND: These queries have recurring pipeline warnings. "
                "Generate candidates that specifically address pipeline "
                "robustness for the affected steps.",
            ]
            if ctx.warning_inventory:
                inv_text = summarize_warning_inventory(ctx.warning_inventory)
                if inv_text:
                    probe_lines.append("")
                    probe_lines.append(inv_text)
                step_counts: dict[str, int] = {}
                for entry in ctx.warning_inventory.values():
                    for wtype in entry.get("warnings", {}):
                        step = wtype.split(":")[0] if ":" in wtype else wtype
                        step_counts[step] = step_counts.get(step, 0) + 1
                if step_counts:
                    dominant_step = max(step_counts, key=step_counts.get)  # type: ignore[arg-type]
                    probe_lines.append(
                        f"\nDominant problem step: {dominant_step} "
                        f"({step_counts[dominant_step]} warning occurrences)"
                    )
                    tried = [
                        ej for ej in escalation_journal if ej.get("problem_step") == dominant_step
                    ]
                    if tried:
                        probe_lines.append(f"Previous attempts targeting {dominant_step}:")
                        for ej in tried[-3:]:
                            wt = ej.get("warning_types", {})
                            probe_lines.append(
                                f"  - degraded_rate={ej.get('degraded_rate', 0):.0%}, warnings={wt}"
                            )
            sections.append("\n".join(probe_lines))
        elif not ctx.l2_directive:
            # Normal round without directive — compact escalation alert
            latest = escalation_journal[-1]
            rate = latest.get("degraded_rate", 0)
            problem_step = latest.get("problem_step", "unknown")
            alert_lines = [
                f"PIPELINE ISSUE: {rate:.0%} of queries degrade at the {problem_step} step.",
                "Address pipeline instability in your candidates.",
            ]
            if len(escalation_journal) > 1:
                alert_lines.append(
                    f"Previous {len(escalation_journal)} attempts have not resolved the issue.",
                )
            wtypes = latest.get("warning_types", {})
            if wtypes:
                alert_lines.append(f"Warning breakdown: {wtypes}")
            sections.append("\n".join(alert_lines))

    # L2 directive
    if ctx.l2_directive:
        sections.append(f"DIRECTIVE:\n{ctx.l2_directive}")

    # Critique — always show (L2 directive is strategic, critique has failure data)
    if ctx.critique_text:
        sections.append(f"CRITIQUE:\n{ctx.critique_text}")

    # Thinking styles
    if ctx.thinking_styles:
        styles = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(ctx.thinking_styles))
        sections.append(f"THINKING STYLES:\n{styles}")

    # Strategic plan
    if ctx.plan:
        sections.append(f"PLAN:\n{ctx.plan}")

    return "\n\n".join(sections)


@dataclass
class L2IntelligenceData:
    """Data bundle for ``format_l2_intelligence()``."""

    escalation_section: str = ""
    warning_inventory: dict | None = None
    critique_text: str = ""
    l2_directive: str = ""
    search_memory_digest: dict | None = None
    trajectory: TrajectoryReport | None = None
    candidate_comparison: str | None = None
    diversity_alert: str | None = None
    validation_failures: list[dict] | None = None
    """Aggregated parse-time validation failures from the prior round's
    candidates. Each entry carries axis/value/allowed/reason. Self-healing
    signal: tells L2 the L1 prompt produced structurally invalid output and
    L2 must produce a directive to prevent recurrence."""
    runtime_failures: list[dict] | None = None
    """NEW runtime-observed health failures from the prior round's candidates
    (e.g. max_tokens=150 producing 100%% empty_content_reasoning_fallback on
    reasoning models). L2-heals-itself rail: L2 must adjust its OWN strategy
    (directive, task_context, optimizer_params) to steer L1 away from the
    failing config region. Sibling of validation_failures but detected
    mid-evaluation, not at parse time."""
    runtime_failures_accumulated: list[dict] | None = None
    """Runtime failures surviving from earlier rounds despite L2's prior
    strategy adjustments. If this list is non-empty, L2's previous angle
    didn't work and it must try something different. When the list keeps
    growing across L2 rounds, L3 escalation uses the same trail to replan."""


def format_l2_intelligence(ctx: L2IntelligenceData) -> str:
    """Build the L2 refine_strategy intelligence bundle (mirrors ``format_context_sections``)."""
    sections: list[str] = []

    # Escalation OR per-query warnings — never both. The escalation
    # stability report already contains aggregate warning counts; appending
    # per-query breakdown is redundant. L2's job is strategic (meta-settings,
    # directive), not per-query targeting — that's for probe rounds (L1).
    esc = ctx.escalation_section
    if not esc and ctx.warning_inventory:
        warning_text = summarize_warning_inventory(ctx.warning_inventory)
        if warning_text:
            esc = warning_text + "\n"
    if esc:
        sections.append(esc)

    # Previous critique
    if ctx.critique_text:
        sections.append("CRITIQUE:\n" + ctx.critique_text)

    # Previous L2 directive
    if ctx.l2_directive:
        sections.append("PREVIOUS DIRECTIVE:\n" + ctx.l2_directive)

    # Round trajectory — progression summary + classification when unhealthy
    tr = ctx.trajectory
    if tr:
        sections.append(f"CAMPAIGN TRAJECTORY:\n  {tr.text}")
        if tr.classification != "healthy":
            sections.append(
                f"TRAJECTORY DIAGNOSIS: [{tr.classification.upper()}] "
                f"{tr.description}\n  Recommended: {tr.recommended_action}"
            )

    # Candidate comparison — what was tried last round
    if ctx.candidate_comparison:
        sections.append(f"LAST ROUND CANDIDATES:\n  {ctx.candidate_comparison}")

    # Diversity alert — mode collapse detection
    if ctx.diversity_alert:
        sections.append(f"DIVERSITY ALERT:\n  {ctx.diversity_alert}")

    # Validation failures from the prior round — self-healing signal.
    # L1 hallucinated values outside the user-declared allowed set. L2
    # must produce a directive that explicitly tells L1 not to do this
    # again. Without this section, L2 only sees aggregate accuracy and
    # has no idea why the candidates were structurally invalid.
    vfs = ctx.validation_failures or []
    if vfs:
        lines = [
            "L1 VALIDATION FAILURES (prior round produced structurally invalid candidates):",
        ]
        for vf in vfs:
            allowed = vf.get("allowed") or []
            allowed_str = ", ".join(allowed[:5]) + (
                f" (+{len(allowed) - 5} more)" if len(allowed) > 5 else ""
            )
            lines.append(
                f"  ⚠ axis={vf.get('axis')} proposed={vf.get('value')!r} reason={vf.get('reason')}"
            )
            lines.append(f"    allowed: [{allowed_str}]")
        lines.append(
            "  ↳ Required L2 action: produce a directive that names the disallowed "
            "value(s) explicitly and instructs L1 to choose only from the allowed "
            'set. Example: "For llm_only.model, use ONLY one of: <list>. Do NOT '
            'propose any other value such as gpt-4o." Self-healing depends on the '
            "directive being explicit."
        )
        sections.append("\n".join(lines))

    # Runtime failures — candidates that ran but produced high degradation
    # rates from runtime issues (e.g. max_tokens too low for a reasoning
    # model, prompt exceeds context window). Rendered in two partitions
    # so L2 sees *new this round* separately from *accumulated across rounds*.
    # The accumulated list is the real self-healing signal: if items there
    # survived L2's prior strategy adjustments, L2's last angle didn't work
    # and it must try a different one. (Runtime failures heal L2 itself,
    # not L1 — unlike validation_failures which L2 teaches L1 to avoid.)
    rfs_new = ctx.runtime_failures or []
    rfs_acc = ctx.runtime_failures_accumulated or []
    if rfs_new or rfs_acc:
        lines = [
            "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
            "  (candidates ran but produced high warning rates; L2 must adjust "
            "its own strategy — directive, task_context, optimizer_params — to "
            "steer L1 away from the failing config region)",
        ]

        def _render_rf(rf: dict) -> list[str]:
            rate_pct = round(float(rf.get("degraded_rate", 0.0)) * 100)
            dominant = rf.get("dominant_warning", "unknown")
            cfg = rf.get("observed_config") or {}
            cfg_parts = [f"{k}={v}" for k, v in cfg.items() if k not in ("prompt",)]
            cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
            changes = rf.get("candidate_changes") or ""
            head = (f"  ⚠ {changes[:60]}" if changes else "  ⚠") + (
                f" — {rate_pct}% degraded on "
                f"{rf.get('total_evaluated', 0)} queries, "
                f"dominant={dominant}"
            )
            return [head, f"    observed_config: {cfg_str}"]

        if rfs_new:
            lines.append("")
            lines.append("NEW (this round):")
            for rf in rfs_new:
                lines.extend(_render_rf(rf))

        if rfs_acc:
            lines.append("")
            lines.append(
                f"ACCUMULATED (surviving from earlier rounds, {len(rfs_acc)} patterns — "
                "L2's prior strategy adjustments did NOT reduce these):"
            )
            for rf in rfs_acc:
                lines.extend(_render_rf(rf))

        lines.append("")
        lines.append(
            "  ↳ Required L2 action: this is L2 self-healing, not L1 correction. "
            "Update your OWN outputs — tighten the directive to name the failing "
            "config range, refine task_context with the discovered constraint, or "
            "adjust optimizer_params (creativity, n_variants) to narrow L1's search "
            "around the safe region. Do NOT just parrot 'don't use X' to L1 — "
            'restructure the search. Example directive: "Reasoning models on this '
            "task need max_tokens ≥ 2000 to emit a final answer; propose variants "
            'only within that range." '
            "If ACCUMULATED entries persist across multiple L2 attempts, L3 will "
            "replan the pipeline itself next."
        )
        sections.append("\n".join(lines))

    # Historical intelligence from SearchMemory
    hi = format_search_memory_block(
        ctx.search_memory_digest,
        {
            "axis_rankings": "Axis impact rankings",
            "bottleneck_distribution": "Bottleneck distribution",
            "failure_group_insights": "Failure group x axis",
            "persistent_failures": "Persistent failures",
            "volatile_queries": "Volatile queries (oscillating)",
        },
    )
    if hi:
        sections.append(hi)

    return "\n\n".join(sections)


@dataclass
class TrajectoryReport:
    """Unified campaign-trajectory view. ``classification`` ∈ healthy/plateau/oscillating/ceiling."""

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


def assess_candidate_diversity(rounds: list[Any], window: int = 5) -> str | None:
    """Detect mode collapse — same axes dominating across recent rounds. Returns None when healthy."""
    if not rounds or len(rounds) < 3:
        return None

    recent = rounds[-window:]
    axis_counts: dict[str, int] = {}
    total_candidates = 0

    for r in recent:
        for cs in r.candidate_scores:
            override = cs.get("pipeline_params_override") or {}
            total_candidates += 1
            if not override:
                continue
            # Count which axes are being varied
            for node_name, node_params in override.items():
                if isinstance(node_params, dict):
                    for param in node_params:
                        axis_counts[f"{node_name}.{param}"] = (
                            axis_counts.get(f"{node_name}.{param}", 0) + 1
                        )
                else:
                    axis_counts[node_name] = axis_counts.get(node_name, 0) + 1

    if total_candidates < 6 or not axis_counts:
        return None

    sorted_axes = sorted(axis_counts.items(), key=lambda x: -x[1])
    top_axis, top_count = sorted_axes[0]
    concentration = top_count / total_candidates

    if concentration > 0.6:
        other_axes = len([a for a, c in sorted_axes if c >= 2])
        return (
            f"Low diversity: {top_axis} varied in {concentration:.0%} of "
            f"{total_candidates} candidates across {len(recent)} rounds "
            f"({other_axes} other axes explored). "
            "Consider directing L1 toward different axes."
        )

    if len(sorted_axes) >= 2:
        top2_count = sorted_axes[0][1] + sorted_axes[1][1]
        top2_concentration = top2_count / total_candidates
        if top2_concentration > 0.75:
            return (
                f"Narrow exploration: {sorted_axes[0][0]} and {sorted_axes[1][0]} "
                f"dominate ({top2_concentration:.0%} of {total_candidates} candidates). "
                "Consider broadening to other axes."
            )

    return None


def build_candidate_comparison(candidate_scores: list[dict]) -> str | None:
    """Compact per-candidate summary (accuracy, description, delta from winner) for L2."""
    if not candidate_scores or len(candidate_scores) < 2:
        return None

    sorted_candidates = sorted(candidate_scores, key=lambda c: -c.get("accuracy", 0))
    winner_acc = sorted_candidates[0].get("accuracy", 0)

    parts = []
    for c in sorted_candidates:
        acc = c.get("accuracy", 0)
        delta = acc - winner_acc
        desc = c.get("changes_description", "no description")[:80]
        delta_str = f" ({delta:+.1%})" if delta != 0 else " (winner)"
        parts.append(f"{acc:.1%}{delta_str}: {desc}")

    return " | ".join(parts)


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

    missed_by: dict[str, list[str]] = {}  # {query: [candidate descriptions]}
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

    # Format top missed opportunities
    sorted_missed = sorted(missed_by.items(), key=lambda x: -len(x[1]))
    parts = []
    for q, candidates in sorted_missed[:5]:
        parts.append(f"  {q[:60]} — solved by {len(candidates)} other candidate(s)")
    total = len(missed_by)
    return (
        f"{total} missed opportunities (queries other candidates solved but winner missed):\n"
        + "\n".join(parts)
    )


def candidate_summaries(candidates: list[dict]) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, c in enumerate(candidates):
        pp_override = c.get("__pipeline_params_override__")
        summary: dict = {
            "idx": i,
            "changes_description": c.get("changes_description", ""),
        }
        if pp_override:
            summary["pipeline_params_override"] = pp_override
        summaries.append(summary)
    return summaries


# ---------------------------------------------------------------------------
# Warning inventory helpers (moved from critique.py to break circular dep)
# ---------------------------------------------------------------------------


def summarize_warning_inventory(tracker: dict[str, dict]) -> str:
    """Group queries by warning type with per-query hit/miss stats."""
    # Collect queries that have any warnings
    by_warning: dict[str, list[tuple[str, dict]]] = {}
    for query, entry in tracker.items():
        for wtype, _count in entry.get("warnings", {}).items():
            by_warning.setdefault(wtype, []).append((query, entry))

    if not by_warning:
        return ""

    max_rounds = max(
        (e.get("rounds_seen", 0) for e in tracker.values()),
        default=0,
    )
    lines = [f"## RECURRING PIPELINE WARNINGS (across {max_rounds} rounds)"]
    for wtype, entries in sorted(
        by_warning.items(),
        key=lambda x: -len(x[1]),
    ):
        lines.append(f"  {wtype} — {len(entries)} queries affected:")
        # Sort by warning frequency descending
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
    """Return ``(warned_count, top_warning_type)`` from the warning inventory."""
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
    """Build the escalation diagnostics section for L2 prompts.

    Returns an empty string when no escalation context is available.
    When present, the section shows a data-driven stability map of tried
    configs so the LLM can figure out what to change.
    """
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
