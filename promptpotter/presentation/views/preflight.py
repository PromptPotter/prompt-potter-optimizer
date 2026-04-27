"""Pre-flight render — feedback cycle configuration + round pipeline summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.campaign.config import compute_preflight_metrics
from promptpotter.application.campaign.data import extract_campaign_baseline
from promptpotter.application.scoring.metrics import count_failures
from promptpotter.presentation.views.display_primitives import BOLD, CYAN, RESET

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = ["render_preflight"]


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

    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  {BOLD}FEEDBACK CYCLE PRE-FLIGHT{RESET}")
    lines.append("=" * 70)
    lines.append(f"  Baseline accuracy      : {baseline_acc:.1%}")
    lines.append(f"  Baseline prompt        : {instr_preview}")
    lines.append("  " + "-" * 66)
    lines.append(f"  Max rounds             : {opt.max_rounds or 'unlimited'}")
    lines.append(f"  Candidates per round   : {opt.n_variants}")
    lines.append(f"  Queries per eval       : {m.queries_label}")
    lines.append(f"  Improvement threshold  : {opt.improvement_threshold:.1%}")
    lines.append(f"  Patience (L1)          : {opt.l1_patience} rounds")
    lines.append(f"  L2 (refine context)    : {m.l2_label}")
    lines.append(f"  L3 (modify plan)       : {m.l3_label}")
    lines.append("  " + "-" * 66)
    lines.append(f"  Candidate model        : {model_name}")
    lines.append(f"  Creativity             : {opt.creativity}")
    lines.append(f"  Pipeline               : {m.pipeline_label}")
    if m.nodes_detail:
        lines.append(f"    Nodes                : {m.nodes_detail}")
    if excluded:
        lines.append(f"    Excluded             : {', '.join(excluded)}")

    lines.append("")
    lines.append(f"  {BOLD}ROUND PIPELINE{RESET} (what happens each round)")
    lines.append("  " + "-" * 66)
    lines.append(f"  {CYAN}1. BASELINE INPUT{RESET}")
    lines.append(f"     Prompt: {instr_preview}")
    lines.append(f"     Accuracy: {baseline_acc:.1%}  |  Prior results: {n_prior}")
    lines.append(f"  {CYAN}2. FAILURE ANALYSIS{RESET}")
    lines.append(
        f"     Up to {opt.max_failures} failures extracted (currently {n_failures} available)"
    )
    lines.append(f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}")
    lines.append("     LLM generates from failures + L1 critique + SearchMemory")
    lines.append(f"  {CYAN}4. LLM CANDIDATE GENERATION{RESET}")
    lines.append(f"     Model: {model_name}  |  Temperature: {opt.creativity}")
    lines.append(f"     Candidates: {opt.n_variants}")
    lines.append("     Output: prompt + pipeline_params_override per candidate")
    lines.append(f"  {CYAN}5. BACKEND EVALUATION{RESET}")
    lines.append(f"     Queries per candidate: {m.queries_label}")
    lines.append(f"     Scoring: {scoring}")
    lines.append(f"  {CYAN}6. WINNER SELECTION{RESET}")
    lines.append(f"     Criterion: best accuracy >= baseline + {opt.improvement_threshold:.1%}")
    lines.append(f"  {CYAN}7. LOOP CONTROL{RESET}")
    lines.append(
        f"     Patience: {opt.l1_patience} stalls → stop  |  L2: {m.l2_label}  |  L3: {m.l3_label}"
    )

    lines.append("  " + "-" * 66)
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
