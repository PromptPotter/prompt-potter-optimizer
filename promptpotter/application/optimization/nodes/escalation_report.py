"""Escalation diagnostics formatter for L2 prompts.

Pure string formatting — no I/O, no LLM calls. Builds the stability
report section consumed by ``refine_strategy``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = ["format_escalation_report"]


def format_escalation_report(
    escalation_check_result: dict | None,
    escalation_journal: list[dict] | None,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Build the escalation diagnostics section for L2 prompts.

    Returns an empty string when no escalation context is available,
    keeping the normal L2 prompt unchanged. When present, the section
    shows a data-driven stability map of tried configs so the LLM can
    figure out what to change.
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
