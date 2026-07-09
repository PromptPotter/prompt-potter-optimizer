"""L1 generation — LLM meta-prompt call producing N candidate variants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.hub import DispatchHub, build_bundle
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    load_optimizer_prompt,
    optimizer_model,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import (
    L1GenerateOutput,
    VariantEvidenceGrounding,
    build_l1_response_model,
)
from promptpotter.application.optimization.validators.l1_strict import (
    build_l1_output_schema,
    effective_l1_field_names,
)
from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.opt_search_point import EvidenceGrounding
from promptpotter.domain.results import (
    L1_PARSE_FAILURE_MALFORMED,
    L1_PARSE_FAILURE_TOOLING,
    CandidateProposal,
    candidate_label,
)
from promptpotter.infrastructure.llm.json_parse import MetaPromptParseError
from promptpotter.infrastructure.llm.models import emit_round_warning
from promptpotter.infrastructure.tracing import CandidateCreated
from promptpotter.shared import truncate
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
        summary["task_context"] = cp.osp.memory.task_context.to_dict()
        summaries.append(summary)
    return summaries


async def l1_generate(
    cycle: Cycle,
    *,
    n_variants: int,
    creativity: float,
    obs: ObservabilityBridge | None = None,
    round_num: int = 0,
) -> tuple[list[CandidateProposal], str | None]:
    """Generate candidate variants via LLM meta-prompt; context read from cycle.

    Returns ``(candidates, parse_failure)``. ``parse_failure`` names the reason when the
    meta-prompt produced unparseable output — the round then carries zero candidates and
    the reason travels with it (charged to the round, never to a candidate), rather than
    vanishing into a bare ``[]``.
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    model = optimizer_model("l1_generate")  # for warning/diagnostic surfaces only
    opt_sp = cycle.opt_sp
    pipeline_schema = cycle.session.pipeline_schema
    tracing_campaign_id = cycle.session.state.tracing_campaign_id

    bundle = build_bundle(cycle)
    # L2-authored layout rides the OSP; `fill` also resolves the `instruction`-slot
    # injections (`l1_supplemental_rules` / `l1_situational_examples`) into `injection_vars`.
    template, injection_vars = DispatchHub.fill(
        load_optimizer_prompt("l1_generate"), opt_sp.memory.l1_layout, bundle
    )
    prompt_vars: dict[str, str] = {"n_variants": str(n_variants), **injection_vars}

    schema_field_rename = cycle.config.optimization.schema_field_rename
    output_schema = (
        build_l1_output_schema(
            pipeline_schema,
            forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
            schema_field_rename=schema_field_rename,
        )
        if pipeline_schema
        else None
    )
    # The wire schema advertises renamed keys; the response model aliases them back. Both read
    # the SAME `effective_l1_field_names` — a disagreement would fail every parse, every round.
    response_model = build_l1_response_model(effective_l1_field_names())
    try:
        generated, meta_prompt, _ = await run_optimizer_node(
            template_name="l1_generate",
            prompt_vars=prompt_vars,
            temperature=creativity,
            response_model=response_model,
            response_schema=output_schema,
            context=LLMCallContext(
                ledger=cycle.session.state.ledger,
                round_num=round_num,
                cache=cycle.session.store.optimizer_calls,
            ),
            template=template,
        )
    except MetaPromptParseError as parse_err:
        # Schema-noncompliant after one repair retry. Split provider-degraded (empty) vs
        # structurally wrong — both wound the same channel but `reason` steers L2's heal direction.
        raw = (parse_err.raw or "").strip()
        is_empty = len(raw) < 20
        reason = L1_PARSE_FAILURE_TOOLING if is_empty else L1_PARSE_FAILURE_MALFORMED
        logger.error(
            "L1 R%d: %s — zero candidates this round (raw=%d chars) [%s]",
            round_num,
            "provider returned empty/truncated content"
            if is_empty
            else "meta-prompt parse failure after retry",
            len(raw),
            parse_err.diagnosis(),
        )
        opt_sp.memory.wounds.validation_failures.append(
            ValidationFailure(
                axis="l1_generate.output",
                value=truncate(parse_err.raw, 300),
                allowed=[],
                reason=reason,
            )
        )
        emit_round_warning(
            kind="l1_zero_candidates",
            severity="error",
            message=(
                "Optimizer produced 0 candidates this round — "
                + (
                    "the optimizer LLM returned empty/truncated output"
                    if is_empty
                    else "the optimizer LLM's response failed schema validation after a repair retry"
                )
                + f" (model {model})."
            ),
            detail={
                "reason": reason,
                "raw_chars": len(raw),
                "model": model,
                # The provider's own account. `finish_reason: length` + a large
                # `reasoning_tokens` means the meta-prompt is too big for the token
                # budget (fix the prompt); `stop` with ~2 completion tokens means the
                # provider degraded (retry/route elsewhere). Same symptom, opposite fix,
                # so both land on disk rather than only in the log.
                "finish_reason": parse_err.finish_reason,
                "completion_tokens": parse_err.completion_tokens,
                "reasoning_tokens": parse_err.reasoning_tokens,
                "reasoning_chars": parse_err.reasoning_chars,
            },
        )
        return [], reason
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
        opt_sp.memory.wounds.validation_failures.append(
            ValidationFailure(
                axis="l1_generate.output",
                value=truncate(str(generated), 300),
                allowed=[],
                reason="meta_prompt_unexpected_type",
            )
        )
        emit_round_warning(
            kind="l1_zero_candidates",
            severity="error",
            message=(
                "Optimizer produced 0 candidates this round — the optimizer LLM's "
                f"response decoded as {type(generated).__name__} instead of the expected "
                f"schema (model {model})."
            ),
            detail={"reason": "meta_prompt_unexpected_type", "model": model},
        )
        return [], "meta_prompt_unexpected_type"

    variants_list = generated.variants

    population: list[CandidateProposal] = []
    for v in variants_list[:n_variants]:
        # Three slots, three readers — schema split (B1) prevents conflation. A node name
        # absent from the active schema (hallucinated) is NOT pre-filtered here: it flows to
        # the one validation producer (``validate_overrides`` via ``parse_population``), which
        # records it as a non-fatal ``hallucinated_node`` wound (routed to l1_wounds +
        # validation_failure_rate), and ``merge_pipeline_params`` strips it from the wire.
        prompt_changes = dict(v.prompt_fields_override)
        tc_changes = dict(v.task_context_override)
        pipeline_params_override = v.pipeline_params_override
        # Override validation is deferred to parse_population — one producer of truth.
        evidence = _parse_evidence_grounding(v.evidence_grounding)
        child = opt_sp.mutate(
            changes_description=v.changes_description,
            source="l1_generate",
            evidence_grounding=evidence,
            **prompt_changes,
        )
        if tc_changes:
            child.memory.task_context = child.memory.task_context.merge(tc_changes)
        population.append(
            CandidateProposal(
                osp=child,
                pipeline_params_override=pipeline_params_override,
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

    return population, None


__all__ = ["candidate_summaries", "l1_generate"]
