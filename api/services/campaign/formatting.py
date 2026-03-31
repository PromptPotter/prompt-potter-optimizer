"""Shared prompt formatting helpers for the optimizer pipeline.

Pure string formatting — no I/O, no LLM calls. Used by
l1_optimizer (L1 generate) for prompt assembly.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.config.settings import DISPLAY_TRUNCATE
from api.services.campaign.critique import summarize_warning_inventory


@dataclass
class ContextData:
    """Data bundle for ``format_context_sections()``."""

    task_context: dict | None = None
    critique_text: str = ""
    l2_directive: str = ""
    thinking_styles: list[str] | None = None
    plan: str = ""
    warning_inventory: dict | None = None
    escalation_journal: list[dict] | None = None
    is_probe_round: bool = False
    scan_context: dict | None = None


def format_failure_examples(
    current_results: list,
    warning_inventory: dict | None,
    is_probe_round: bool,
    max_failures: int = 15,
) -> str:
    """Format failure examples with optional warning annotations."""
    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    lines = []
    for r in failures[:max_failures]:
        line = (
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r['predicted'][:DISPLAY_TRUNCATE]}  |  "
            f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
        )
        if warning_inventory:
            entry = warning_inventory.get(r["query"])
            if entry and entry.get("warnings"):
                top_warn = max(entry["warnings"], key=entry["warnings"].get)
                wcount = entry["warnings"][top_warn]
                seen = entry.get("rounds_seen", 0)
                threshold = 1 if is_probe_round else 2
                if wcount >= threshold:
                    line += f"  [{top_warn} {wcount}/{seen} rounds]"
        lines.append(line)
    return "\n".join(lines)


def format_context_sections(ctx: ContextData) -> str:
    """Build all optional context sections as a single string.

    Each non-empty section is a titled block. Returned string is empty
    when no context is available. This is the single intelligence bundle
    for L1 — scan data, escalation, critique, directives, and plan all
    live here.
    """
    sections: list[str] = []

    # Scan analytics (tested ranges + sensitivity)
    sc = ctx.scan_context
    if sc:
        scan_parts = [f"SCAN ANALYTICS (sensitivity scan results):\n"
                      f"Tested values per axis:\n{sc['tested_values']}"]
        if sc.get("sensitivity_text"):
            scan_parts.append(f"Top sensitivity drivers:\n{sc['sensitivity_text']}")
        sections.append("\n".join(scan_parts))

    # Task context
    if ctx.task_context:
        tc_lines = "\n".join(f"  {k}: {v}" for k, v in ctx.task_context.items() if v)
        if tc_lines:
            sections.append(
                f"TASK CONTEXT (domain understanding — use to guide your changes):\n{tc_lines}"
            )

    # Escalation / probe — unified from escalation_journal
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
        else:
            # Normal round — compact escalation alert
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
        sections.append(
            "L2 DIRECTIVE (from context refinement — additional "
            f"guidance for this round):\n{ctx.l2_directive}"
        )

    # Critique — skip when L2 directive is present (L2 already absorbed critique)
    if ctx.critique_text and not ctx.l2_directive:
        sections.append(
            "CRITIQUE (from previous evaluation — use this to guide "
            f"your changes):\n{ctx.critique_text}"
        )

    # Thinking styles
    if ctx.thinking_styles:
        styles = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(ctx.thinking_styles))
        sections.append(
            f"THINKING STYLES (consider these approaches when generating variants):\n{styles}"
        )

    # Strategic plan
    if ctx.plan:
        sections.append(f"STRATEGIC GUIDANCE (from optimization plan):\n{ctx.plan}")

    return "\n\n".join(sections)


@dataclass
class L2IntelligenceData:
    """Data bundle for ``format_l2_intelligence()``."""

    escalation_section: str = ""
    warning_inventory: dict | None = None
    critique_text: str = ""
    l2_directive: str = ""


def format_l2_intelligence(ctx: L2IntelligenceData) -> str:
    """Build the intelligence bundle for L2 refine_context.

    Mirrors L1's ``format_context_sections()`` pattern — a single string
    with titled blocks for escalation/warnings, critique, and previous
    directive. Returns empty string when no intelligence is available.
    """
    sections: list[str] = []

    # Escalation + per-query warnings (complementary granularities)
    esc = ctx.escalation_section
    if ctx.warning_inventory:
        warning_text = summarize_warning_inventory(ctx.warning_inventory)
        if warning_text:
            esc = esc + "\n\n" + warning_text + "\n" if esc else warning_text + "\n"
    if esc:
        sections.append(esc)

    # Previous critique
    if ctx.critique_text:
        sections.append(
            "PREVIOUS CRITIQUE (build on this analysis, don't repeat it):\n"
            + ctx.critique_text
        )

    # Previous L2 directive
    if ctx.l2_directive:
        sections.append(
            "YOUR PREVIOUS DIRECTIVE (evolve or supersede — do not repeat verbatim):\n"
            + ctx.l2_directive
        )

    return "\n\n".join(sections)
