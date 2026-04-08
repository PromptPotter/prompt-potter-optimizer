"""Shared prompt formatting helpers for the optimizer pipeline.

Pure string formatting — no I/O, no LLM calls. Used by
l1_optimizer (L1 generate) for prompt assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.models.task_context import TaskContext
from promptpotter.services.campaign.critique import summarize_warning_inventory

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis
    from promptpotter.services.search.scan_results import ScanContext

__all__ = [
    "ContextData",
    "L2IntelligenceData",
    "build_l1_search_memory_context",
    "build_l2_search_memory_context",
    "candidate_summaries",
    "format_context_sections",
    "format_l2_intelligence",
]


@dataclass
class ContextData:
    """Data bundle for ``format_context_sections()``."""

    task_context: TaskContext | None = None
    critique_text: str = ""
    l2_directive: str = ""
    thinking_styles: list[str] | None = None
    plan: str = ""
    warning_inventory: dict | None = None
    escalation_journal: list[dict] | None = None
    is_probe_round: bool = False
    scan_context: ScanContext | None = None
    scan_compact: bool = False
    failure_analysis: FailureAnalysis | None = None
    search_memory_context: dict | None = None
    pipeline_schema_text: str = ""


def format_context_sections(ctx: ContextData) -> str:
    """Build all optional context sections as a single string.

    Each non-empty section is a titled block. Returned string is empty
    when no context is available. This is the single intelligence bundle
    for L1 — scan data, escalation, critique, directives, and plan all
    live here.
    """
    sections: list[str] = []

    # Pipeline schema — valid nodes and parameters
    if ctx.pipeline_schema_text:
        sections.append(ctx.pipeline_schema_text)

    # Scan analytics — full on first round, sensitivity-only thereafter
    sc = ctx.scan_context
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
    smc = ctx.search_memory_context
    if smc:
        hi_lines = ["HISTORICAL INTELLIGENCE:"]
        if smc.get("failure_clusters"):
            hi_lines.append(f"  Common failure patterns: {smc['failure_clusters']}")
        if smc.get("dead_queries"):
            hi_lines.append(f"  Dead queries (never hit): {smc['dead_queries']}")
        if smc.get("top_axes"):
            hi_lines.append(f"  High-impact axes: {smc['top_axes']}")
        if smc.get("top_values"):
            hi_lines.append(f"  Best-performing values: {smc['top_values']}")
        if len(hi_lines) > 1:
            sections.append("\n".join(hi_lines))

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
    search_memory_context: dict | None = None


def format_l2_intelligence(ctx: L2IntelligenceData) -> str:
    """Build the intelligence bundle for L2 refine_context.

    Mirrors L1's ``format_context_sections()`` pattern — a single string
    with titled blocks for escalation/warnings, critique, and previous
    directive. Returns empty string when no intelligence is available.
    """
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

    # Historical intelligence from SearchMemory
    smc = ctx.search_memory_context
    if smc:
        hi_lines = ["HISTORICAL INTELLIGENCE:"]
        if smc.get("axis_rankings"):
            hi_lines.append(f"  Axis impact rankings: {smc['axis_rankings']}")
        if smc.get("bottleneck_distribution"):
            hi_lines.append(f"  Bottleneck distribution: {smc['bottleneck_distribution']}")
        if smc.get("failure_group_insights"):
            hi_lines.append(f"  Failure group x axis: {smc['failure_group_insights']}")
        if smc.get("persistent_failures"):
            hi_lines.append(f"  Persistent failures: {smc['persistent_failures']}")
        if len(hi_lines) > 1:
            sections.append("\n".join(hi_lines))

    return "\n\n".join(sections)


def build_l1_search_memory_context(search_memory: Any) -> dict | None:
    """Build SearchMemory context dict for L1 generation prompt."""
    if search_memory is None:
        return None

    ctx: dict[str, str] = {}

    clusters = search_memory.failure_clusters(3)
    if clusters:
        ctx["failure_clusters"] = "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.query_count} queries)" for c in clusters
        )

    dead = search_memory.dead_queries()
    if dead:
        ctx["dead_queries"] = f"{len(dead)} queries never hit"

    rankings = search_memory.axis_rankings()[:3]
    if rankings:
        ctx["top_axes"] = "; ".join(
            f"{a.axis} (effect={a.effect_size:.3f}, {a.classification})" for a in rankings
        )
        # Top values for the highest-impact axis
        top_vals = search_memory.top_k_values(rankings[0].axis, k=3)
        if top_vals:
            ctx["top_values"] = "; ".join(
                f"{v.value_preview} (acc={v.mean_accuracy:.1%})" for v in top_vals
            )

    return ctx if ctx else None


def build_l2_search_memory_context(search_memory: Any) -> dict | None:
    """Build SearchMemory context dict for L2 refine prompt.

    L2 receives the full strategic intelligence: axis rankings, bottleneck
    distribution, failure group × axis correlations, and persistent failures.
    """
    if search_memory is None:
        return None

    ctx: dict[str, str] = {}

    rankings = search_memory.axis_rankings()[:5]
    if rankings:
        ctx["axis_rankings"] = "; ".join(
            f"{a.axis} (effect={a.effect_size:.3f}, {a.classification})" for a in rankings
        )

    bottleneck = search_memory.bottleneck_distribution()
    if bottleneck:
        ctx["bottleneck_distribution"] = "; ".join(
            f"{step}: {frac:.0%}" for step, frac in bottleneck.items()
        )

    # Failure group × axis correlations (which axes help which failure modes)
    if rankings:
        fg_lines = []
        for a in rankings[:3]:
            corr = search_memory.parameter_failure_correlation(a.axis)
            if corr:
                parts = [f"{mode}: {delta:+.0%}" for mode, delta in sorted(
                    corr.items(), key=lambda x: -abs(x[1]),
                )[:3]]
                fg_lines.append(f"{a.axis} → {', '.join(parts)}")
        if fg_lines:
            ctx["failure_group_insights"] = "; ".join(fg_lines)

    # Persistent failures — queries failing across multiple configs
    persistent = search_memory.persistent_failures(min_streak=3)
    if persistent:
        intractable = [q for q in persistent if q.hit_rate == 0]
        chronic = [q for q in persistent if q.hit_rate > 0]
        parts = []
        if intractable:
            parts.append(f"{len(intractable)} intractable (never hit)")
        if chronic:
            parts.append(f"{len(chronic)} chronic failures")
        ctx["persistent_failures"] = "; ".join(parts)

    return ctx if ctx else None


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
