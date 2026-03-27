"""Shared prompt formatting helpers for the optimizer pipeline.

Pure string formatting — no I/O, no LLM calls. Used by both
l1_optimizer (L1 generate) and layer_transitions (L2/L3).
"""

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


def format_failure_lines(
    results: list[dict],
    max_lines: int = 15,
    truncate: int = DISPLAY_TRUNCATE,
) -> list[str]:
    """Format failure examples as Q/Pred/GT lines.

    Filters non-hit, non-error results and formats each as a single line.
    Returns a list of formatted strings (no trailing newline).
    """
    failures = [r for r in results if not r["hit"] and not r.get("error")]
    lines = []
    for r in failures[:max_lines]:
        lines.append(
            f"  Q: {r['query'][:truncate]}  "
            f"Pred: {r.get('predicted', '?')[:truncate]}  "
            f"GT: {r['ground_truth'][:truncate]}"
        )
    return lines


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


def format_scan_analytics(scan_context: dict | None) -> str:
    """Format scan analytics section (empty when no scan data)."""
    if not scan_context:
        return "(no scan data available)"
    return (
        f"### Variant leaderboard (ranked by accuracy)\n"
        f"{scan_context['leaderboard_text']}\n\n"
        f"### Axis sensitivity (most impactful parameters)\n"
        f"{scan_context['sensitivity_text']}\n\n"
        f"### Query difficulty\n"
        f"{scan_context['difficulty_text']}\n\n"
        f"### Tested values per axis\n"
        f"{scan_context['tested_values']}"
    )


def format_focus_note(escalation_journal: list[dict] | None) -> str:
    """Format pipeline degradation note from escalation journal."""
    if not escalation_journal:
        return ""
    latest = escalation_journal[-1]
    rate = latest.get("degraded_rate", 0)
    problem_step = latest.get("problem_step", "unknown")
    lines = [
        f"PIPELINE ISSUE: {rate:.0%} of queries degrade at the {problem_step} step.",
        "Address pipeline instability in your candidates.",
    ]
    if len(escalation_journal) > 1:
        lines.append(
            f"Previous {len(escalation_journal)} attempts have not resolved the issue.",
        )
    wtypes = latest.get("warning_types", {})
    if wtypes:
        lines.append(f"Warning breakdown: {wtypes}")
    return "\n".join(lines)


def format_context_sections(ctx: ContextData) -> str:
    """Build all optional context sections as a single string.

    Each non-empty section is a titled block. Returned string is empty
    when no context is available.
    """
    task_context = ctx.task_context
    critique_text = ctx.critique_text
    l2_directive = ctx.l2_directive
    thinking_styles = ctx.thinking_styles
    plan = ctx.plan
    warning_inventory = ctx.warning_inventory
    escalation_journal = ctx.escalation_journal
    is_probe_round = ctx.is_probe_round

    sections: list[str] = []

    # Task context
    if task_context:
        tc_lines = "\n".join(f"  {k}: {v}" for k, v in task_context.items() if v)
        if tc_lines:
            sections.append(
                f"TASK CONTEXT (domain understanding — use to guide your changes):\n{tc_lines}"
            )

    # Probe round — enriched with warning inventory and escalation history
    if is_probe_round:
        probe_lines = [
            "PROBE ROUND: These queries have recurring pipeline warnings. "
            "Generate candidates that specifically address pipeline "
            "robustness for the affected steps.",
        ]
        if warning_inventory:
            inv_text = summarize_warning_inventory(warning_inventory)
            if inv_text:
                probe_lines.append("")
                probe_lines.append(inv_text)
            step_counts: dict[str, int] = {}
            for entry in warning_inventory.values():
                for wtype in entry.get("warnings", {}):
                    step = wtype.split(":")[0] if ":" in wtype else wtype
                    step_counts[step] = step_counts.get(step, 0) + 1
            if step_counts:
                dominant_step = max(step_counts, key=step_counts.get)  # type: ignore[arg-type]
                probe_lines.append(
                    f"\nDominant problem step: {dominant_step} "
                    f"({step_counts[dominant_step]} warning occurrences)"
                )
                if escalation_journal:
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

    # L2 directive
    if l2_directive:
        sections.append(
            "L2 DIRECTIVE (from context refinement — additional "
            f"guidance for this round):\n{l2_directive}"
        )

    # Critique
    if critique_text:
        sections.append(
            "CRITIQUE (from previous evaluation — use this to guide "
            f"your changes):\n{critique_text}"
        )

    # Thinking styles
    if thinking_styles:
        styles = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(thinking_styles))
        sections.append(
            f"THINKING STYLES (consider these approaches when generating variants):\n{styles}"
        )

    # Strategic plan
    if plan:
        sections.append(f"STRATEGIC GUIDANCE (from optimization plan):\n{plan}")

    return "\n\n".join(sections)
