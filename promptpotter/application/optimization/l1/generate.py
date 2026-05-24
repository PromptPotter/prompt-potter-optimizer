"""L1 generation — LLM meta-prompt call producing N candidate variants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.hub import DispatchHub, build_bundle
from promptpotter.application.optimization.dispatch.llm_call import (
    load_optimizer_prompt,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import (
    L1GenerateOutput,
    VariantEvidenceGrounding,
)
from promptpotter.application.optimization.validators.l1_strict import (
    build_l1_output_schema,
    filter_pipeline_params_override,
)
from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.opt_search_point import EvidenceGrounding
from promptpotter.domain.results import CandidateProposal, candidate_label
from promptpotter.infrastructure.llm import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import MetaPromptParseError
from promptpotter.infrastructure.tracing import CandidateCreated
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

import logging

logger = logging.getLogger(__name__)


def _parse_evidence_grounding(raw: VariantEvidenceGrounding | None) -> EvidenceGrounding | None:
    """Permissive parse — `field` is plain `str` (provider may ignore the JSON-Schema enum) and
    `raw` may be None (missing citation). Missing groundings surface downstream as
    `evidence_grounding_present` behavior-check failures.
    """
    if raw is None:
        logger.warning(
            "l1_generate: variant emitted without evidence_grounding — "
            "routed to evidence_grounding_present wound channel"
        )
        return None
    return EvidenceGrounding(field=raw.field, citation=raw.citation.strip())


def candidate_summaries(proposals: list[CandidateProposal], round_num: int) -> list[dict[str, Any]]:
    """Per-candidate summary dicts for phase events. `label` set once here — no display-side `idx+1`."""
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = cp.osp.prompt_fields()
        summary: dict[str, Any] = {
            "idx": i,
            "label": candidate_label(round_num, i),
            "changes_description": cp.osp.lineage.changes_description or "",
        }
        if cp.pipeline_params_override:
            summary["pipeline_params_override"] = cp.pipeline_params_override
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        # Third L1 mutation slot — lets SP-diff render task_context-only candidates as a mutation,
        # not a bare [clone].
        summary["task_context"] = cp.osp.task_context.to_dict()
        summaries.append(summary)
    return summaries


L1_CREATIVITY: float = 0.7
"""LLM sampling temperature for L1 candidate generation."""


async def l1_generate(
    cycle: Cycle,
    *,
    n_variants: int,
    creativity: float = L1_CREATIVITY,
    llm_client: LLMClientBase,
    model: str | None = None,
    obs: ObservabilityBridge | None = None,
    round_num: int = 0,
) -> list[CandidateProposal]:
    """Generate candidate variants via LLM meta-prompt; context read from cycle."""
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    opt_sp = cycle.opt_sp
    pipeline_schema = cycle.session.pipeline_schema
    tracing_campaign_id = cycle.session.state.tracing_campaign_id

    bundle = build_bundle(cycle)
    template = DispatchHub.fill_l1(load_optimizer_prompt("l1_generate"), opt_sp.l1_layout, bundle)
    # `instruction` slot isn't in L1_LAYOUT_SLOTS so fill_l1 skips it — we substitute its two
    # injection placeholders via compile_prompt kwargs here.
    prompt_vars: dict[str, str] = {
        "n_variants": str(n_variants),
        "l1_supplemental_rules": DispatchHub.render("l1_supplemental_rules", bundle),
        "l1_situational_examples": DispatchHub.render("l1_situational_examples", bundle),
    }

    output_schema = build_l1_output_schema(pipeline_schema) if pipeline_schema else None
    try:
        generated, meta_prompt = await run_optimizer_node(
            template_name="l1_generate",
            prompt_vars=prompt_vars,
            llm_client=llm_client,
            model=model,
            temperature=creativity,
            response_schema=output_schema,
            ledger=cycle.session.state.ledger,
            round_num=round_num,
            template=template,
            optimizer_call_cache=cycle.session.store.optimizer_calls,
        )
    except MetaPromptParseError as parse_err:
        # Schema-noncompliant after one repair retry. Split provider-degraded (empty) vs
        # structurally wrong — both wound the same channel but `reason` steers L2's heal direction.
        raw = (parse_err.raw or "").strip()
        is_empty = len(raw) < 20
        reason = "l1_provider_empty_response" if is_empty else "meta_prompt_parse_failure"
        logger.error(
            "L1 R%d: %s — zero candidates this round (raw=%d chars)",
            round_num,
            "provider returned empty/truncated content"
            if is_empty
            else "meta-prompt parse failure after retry",
            len(raw),
        )
        opt_sp.wounds.validation_failures.append(
            ValidationFailure(
                axis="l1_generate.output",
                value=_truncate_raw(parse_err.raw, 300),
                allowed=[],
                reason=reason,
            )
        )
        return []
    slot_sizes = sorted(
        (
            (slot, len(slot_text.rstrip()))
            for slot in ("persona", "task_intent", "problem_description", "thinking_style")
            if (slot_text := getattr(template, slot)) and slot_text.strip()
        ),
        key=lambda x: -x[1],
    )
    logger.info(
        "L1 R%d meta-prompt: %d chars | %s",
        round_num,
        len(meta_prompt),
        " | ".join(f"{n}={s}" for n, s in slot_sizes),
    )

    # The repair-retry path can leak a raw str/dict/list past validation when JSON parses but
    # doesn't bind. Route unexpected types to the wound channel so the round completes cleanly
    # (zero candidates → L2 heals next round) instead of crashing on `.variants`.
    if not isinstance(generated, L1GenerateOutput):
        logger.error(
            "L1 R%d: l1_generate response decoded as %s instead of L1GenerateOutput — "
            "treating as parse failure, returning zero candidates",
            round_num,
            type(generated).__name__,
        )
        opt_sp.wounds.validation_failures.append(
            ValidationFailure(
                axis="l1_generate.output",
                value=_truncate_raw(str(generated), 300),
                allowed=[],
                reason="meta_prompt_unexpected_type",
            )
        )
        return []

    variants_list = generated.variants

    population: list[CandidateProposal] = []
    for v in variants_list[:n_variants]:
        # Three slots, three readers — schema split (B1) prevents conflation. Runtime filter is
        # belt-and-braces: drops node names absent from the active schema if provider strict is off.
        prompt_changes = dict(v.prompt_fields_override or {})
        tc_changes = dict(v.task_context_override or {})
        pipeline_params_override = filter_pipeline_params_override(
            v.pipeline_params_override or {}, pipeline_schema
        )
        # Override validation is deferred to parse_population — one producer of truth.
        evidence = _parse_evidence_grounding(v.evidence_grounding)
        child = opt_sp.mutate(
            changes_description=v.changes_description,
            source="l1_generate",
            evidence_grounding=evidence,
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        population.append(
            CandidateProposal(
                osp=child,
                pipeline_params_override=pipeline_params_override,
                evidence_grounding=evidence,
            )
        )

        if obs:
            with graceful("CandidateCreated emit failed"):
                obs.emit_write_point(
                    CandidateCreated,
                    campaign_id=tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=len(population) - 1,
                    candidate_id=child.lineage.id,
                )

    return population


def _truncate_raw(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


__all__ = ["L1_CREATIVITY", "candidate_summaries", "l1_generate"]
