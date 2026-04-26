"""Source + render closures for :mod:`inbox_registry`.

Each :class:`InboxField` registered in ``inbox_registry.INBOX`` references one
``_src_*`` (extracts a raw value from a :class:`Cycle` + :class:`InboxTransients`)
and one ``_r_*`` (formats that value into a labeled section string).

This module is purely the closure catalogue — no public API. The registry
declarations and the ``assemble_inbox`` dispatcher live next door so the
manifest stays readable. New inbox signals add a closure pair here and a
declaration in the registry tuple.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes.formatting import (
    format_escalation_report,
    format_runtime_failure_line,
    format_search_memory_block,
    summarize_warning_inventory,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.inbox_registry import (
        InboxTransients,
        Layer,
    )
    from promptpotter.domain.analysis import FailureAnalysis


_L1_SM_LABELS: dict[str, str] = {
    "failure_clusters": "Common failure patterns",
    "dead_queries": "Dead queries (never hit)",
    "top_axes": "High-impact axes",
    "top_values": "Best-performing values",
}
_L2_SM_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_group_insights": "Failure group x axis",
    "persistent_failures": "Persistent failures",
    "volatile_queries": "Volatile queries (oscillating)",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})


# ---------------------------------------------------------------------------
# Source helpers — closures over (Cycle, InboxTransients).
# ---------------------------------------------------------------------------


def _src_memory(attr: str) -> Callable[[Cycle, InboxTransients], Any]:
    def _read(cycle: Cycle, _t: InboxTransients) -> Any:
        return getattr(cycle.opt_sp, attr) or None

    return _read


def _src_task_context(cycle: Cycle, _t: InboxTransients) -> Any:
    tc = cycle.opt_sp.task_context
    return tc if tc else None


def _src_plan(cycle: Cycle, _t: InboxTransients) -> str | None:
    return cycle.opt_sp.plan or None


def _src_pipeline_schema_text(_cycle: Cycle, t: InboxTransients) -> str | None:
    return t.pipeline_schema_text or None


def _src_failure_analysis(cycle: Cycle, _t: InboxTransients) -> FailureAnalysis | None:
    fa = cycle.opt_sp.failure_analysis
    return fa if fa and fa.patterns else None


def _src_escalation_probe(cycle: Cycle, _t: InboxTransients) -> list[dict] | None:
    """Probe-round per-query warning block — fires only when probe AND journal present."""
    if not cycle.probe_next_round:
        return None
    journal = cycle.opt_sp.escalation_journal
    return journal or None


def _src_escalation_alert(cycle: Cycle, _t: InboxTransients) -> list[dict] | None:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    if cycle.probe_next_round:
        return None
    if cycle.opt_sp.l2_directive:
        return None
    journal = cycle.opt_sp.escalation_journal
    return journal or None


def _src_l1_search_memory(cycle: Cycle, _t: InboxTransients) -> dict[str, str] | None:
    if cycle.search_memory is None:
        return None
    return cycle.search_memory.digest_for_l1_generate()


def _src_l2_search_memory(cycle: Cycle, _t: InboxTransients) -> dict[str, str] | None:
    if cycle.search_memory is None:
        return None
    return cycle.search_memory.digest_for_l2()


def _src_escalation_section(cycle: Cycle, t: InboxTransients) -> str | None:
    """L2 escalation report — from escalation_check_result + journal."""
    if not t.escalation_check_result:
        return None
    schema = cycle.session.pipeline_schema if cycle.session is not None else None
    text = format_escalation_report(
        t.escalation_check_result,
        cycle.opt_sp.escalation_journal or None,
        t.pipeline_params,
        pipeline_schema=schema,
    )
    return text or None


def _src_warning_inventory_l2(cycle: Cycle, t: InboxTransients) -> dict | None:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _src_escalation_section(cycle, t):
        return None
    return cycle.opt_sp.warning_inventory or None


def _src_validation_failures(_cycle: Cycle, t: InboxTransients) -> list[dict] | None:
    vfs: list[dict] = []
    for cs in t.candidate_scores or []:
        vfs.extend(cs.get("validation_failures") or [])
    return vfs or None


def _src_runtime_failures(cycle: Cycle, _t: InboxTransients) -> list[dict] | None:
    rfs = [rf.to_dict() for rf in cycle.opt_sp.runtime_failures]
    return rfs or None


# ---------------------------------------------------------------------------
# Render helpers — closures receive (value, cycle, t, layer) and return str.
# ---------------------------------------------------------------------------


def _r_identity(v: Any, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    return str(v) if v else ""


def _r_task_context(v: Any, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    # Drop raw_description (duplicates the dataset task_description.md) and
    # upstream/downstream_context (already spliced into ``rendered_prompt``
    # by ``OptSearchPoint._field_value``). Leaking them here would double-
    # count the same text into the L1 meta-prompt.
    lines = "\n".join(
        f"  {k}: {val}" for k, val in v.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _r_plan(v: str, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    return f"PLAN:\n{v}" if v else ""


def _r_unused(_v: Any, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    """Sentinel for fields whose render is always supplied by ``_LABEL_BY_LAYER``."""
    return ""


def _r_failure_analysis(
    fa: FailureAnalysis, _cycle: Cycle, _t: InboxTransients, _layer: Layer
) -> str:
    if not fa or not fa.patterns:
        return ""
    lines = [f"FAILURE ANALYSIS ({fa.total_failures} failures / {fa.total_results} total):"]
    for i, pat in enumerate(fa.patterns[:2], 1):
        lines.append(f"  {i}. {pat.name} — {pat.query_count} queries ({pat.fraction:.0%})")
        if pat.example_queries:
            ex = pat.example_queries[0][:60]
            lines.append(f'     Example: "{ex}"')
        sig = {
            k: v for k, v in pat.signals.items() if k not in ("error", "degraded", "total_time_ms")
        }
        if sig:
            sig_str = ", ".join(f"{k}={v}" for k, v in list(sig.items())[:2])
            lines.append(f"     Signals: {sig_str}")
    return "\n".join(lines)


def _r_escalation_probe(
    journal: list[dict], cycle: Cycle, _t: InboxTransients, _layer: Layer
) -> str:
    lines = [
        "PROBE ROUND: queries have recurring pipeline warnings. "
        "Generate candidates that address pipeline robustness."
    ]
    warning_inventory = cycle.opt_sp.warning_inventory or None
    if warning_inventory:
        inv = summarize_warning_inventory(warning_inventory)
        if inv:
            lines.extend(["", inv])
        step_counts: Counter[str] = Counter()
        for entry in warning_inventory.values():
            for wtype in entry.get("warnings", {}):
                step_counts[wtype.split(":", 1)[0]] += 1
        if step_counts:
            dom_step, dom_count = step_counts.most_common(1)[0]
            lines.append(f"\nDominant step: {dom_step} ({dom_count} warnings)")
            tried = [ej for ej in journal if ej.get("problem_step") == dom_step][-3:]
            if tried:
                lines.append(f"Previous attempts at {dom_step}:")
                lines.extend(
                    f"  - {ej.get('degraded_rate', 0):.0%} degraded, {ej.get('warning_types', {})}"
                    for ej in tried
                )
    return "\n".join(lines)


def _r_escalation_alert(
    journal: list[dict], _cycle: Cycle, _t: InboxTransients, _layer: Layer
) -> str:
    latest = journal[-1]
    alert = [
        f"PIPELINE ISSUE: {latest.get('degraded_rate', 0):.0%} of queries "
        f"degrade at {latest.get('problem_step', 'unknown')}. "
        "Address pipeline instability."
    ]
    if len(journal) > 1:
        alert.append(f"{len(journal)} prior attempts unresolved.")
    if latest.get("warning_types"):
        alert.append(f"Warnings: {latest['warning_types']}")
    return "\n".join(alert)


def _r_search_memory_l1(
    v: dict[str, str], _cycle: Cycle, _t: InboxTransients, _layer: Layer
) -> str:
    # Uses format_search_memory_block's "HISTORICAL INTELLIGENCE:" default header.
    return format_search_memory_block(v, _L1_SM_LABELS)


def _r_search_memory_l2(
    v: dict[str, str], _cycle: Cycle, _t: InboxTransients, _layer: Layer
) -> str:
    return format_search_memory_block(v, _L2_SM_LABELS)


def _r_escalation_section(v: str, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    # format_escalation_report returns text ending in "\n" already — preserve.
    return v


def _r_warning_inventory_l2(v: dict, _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    text = summarize_warning_inventory(v)
    return (text + "\n") if text else ""


def _r_validation_failures(v: list[dict], _cycle: Cycle, _t: InboxTransients, _layer: Layer) -> str:
    lines = ["L1 VALIDATION FAILURES (prior round produced structurally invalid candidates):"]
    for vf in v:
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
    return "\n".join(lines)


def _r_runtime_failures_l2(v: list[dict], _cycle: Cycle, t: InboxTransients, _layer: Layer) -> str:
    rfs_new = [rf for rf in v if rf.get("first_seen_round", 0) == t.round_num]
    rfs_acc = [rf for rf in v if rf.get("first_seen_round", 0) != t.round_num]
    lines = [
        "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
        "  (candidates ran but produced high warning rates; L2 must adjust "
        "its own strategy — directive, task_context, optimizer_params — to "
        "steer L1 away from the failing config region)",
    ]
    if rfs_new:
        lines.append("")
        lines.append("NEW (this round):")
        for rf in rfs_new:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    if rfs_acc:
        lines.append("")
        lines.append(
            f"ACCUMULATED (surviving from earlier rounds, {len(rfs_acc)} patterns — "
            "L2's prior strategy adjustments did NOT reduce these):"
        )
        for rf in rfs_acc:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
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
    return "\n".join(lines)
