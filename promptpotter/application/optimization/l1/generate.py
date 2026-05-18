"""L1 generation — LLM meta-prompt call producing N candidate variants."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    """Build an ``EvidenceGrounding`` from one Pydantic-typed variant entry.

    The parse boundary is permissive — ``field`` is a plain ``str`` (providers
    that ignore the JSON-Schema ``enum`` hint would otherwise crash the round),
    and ``raw`` itself is allowed to be ``None`` for variants the LLM emitted
    without the required panel citation. Missing/invalid groundings surface as
    per-round behavior-check failures downstream — ``evidence_grounding_present``
    flags them in ``review.md`` and ``round_NNNN.json``.
    """
    if raw is None:
        logger.warning(
            "l1_generate: variant emitted without evidence_grounding — "
            "routed to evidence_grounding_present wound channel"
        )
        return None
    return EvidenceGrounding(field=raw.field, citation=raw.citation.strip())


def candidate_summaries(proposals: list[CandidateProposal], round_num: int) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data.

    Each summary carries ``label`` (canonical ``CN.M``), set once here so the
    audit trail's ``l1_score.input.candidates`` and every downstream reader
    share one identity — no display-side ``idx + 1`` arithmetic.
    """
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = cp.osp.prompt_fields()
        summary: dict = {
            "idx": i,
            "label": candidate_label(round_num, i),
            "changes_description": cp.osp.lineage.changes_description or "",
        }
        if cp.pipeline_params_override:
            summary["pipeline_params_override"] = cp.pipeline_params_override
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        # task_context is the third L1 mutation slot. Surface it so the
        # SP-diff table can render task_context-only candidates as a
        # mutation rather than a bare [clone].
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
    # L1's instruction slot carries {{l1_supplemental_rules}} +
    # {{l1_situational_examples}} placeholders (registered injections, but
    # filled here because fill_l1 only walks L1_LAYOUT_SLOTS and instruction
    # isn't one). compile_prompt substitutes via kwargs.
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
        # The meta-prompt LLM returned content that didn't validate against
        # L1GenerateOutput even after one repair-hint retry. Record this on
        # the wound channel L2 already reads (validation_failures), then
        # return zero candidates so the round completes cleanly. Next round
        # L2 sees the failure on its surface and heals the framing.
        logger.error(
            "L1 R%d: meta-prompt parse failure after retry — zero candidates this round",
            round_num,
        )
        opt_sp.wounds.validation_failures.append(
            ValidationFailure(
                axis="l1_generate.output",
                value=_truncate_raw(parse_err.raw, 300),
                allowed=[],
                reason="meta_prompt_parse_failure",
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

    # Defensive type guard — ``extract_parsed_json`` returns ``response.parsed``
    # unconditionally, and the schema-repair retry path can leak a raw
    # ``str`` (or dict, or list) past validation when the second attempt's
    # content parses to JSON but doesn't bind to ``L1GenerateOutput``. Route
    # unexpected types into the same wound channel as a true parse failure
    # so the round completes cleanly (zero candidates, L2 sees the failure
    # next round and heals) instead of crashing on ``.variants``.
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
        # Three slots, three readers. The schema split (B1) guarantees the
        # LLM cannot conflate them — the runtime filter only drops node
        # names not in the active schema (belt-and-braces; the JSON schema
        # already enumerates them, but provider strict mode is off).
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
