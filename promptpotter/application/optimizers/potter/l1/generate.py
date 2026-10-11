from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import citable_fields
from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
    build_l1_response_schema,
    effective_l1_field_names,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    load_optimizer_prompt,
    node_layout,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1GenerateOutput,
    VariantEvidenceGrounding,
    build_l1_response_model,
)
from promptpotter.domain.opt_search_point import EvidenceGrounding
from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_MALFORMED,
    PARSE_FAILURE_TOOLING,
    PARSE_FAILURE_WRONG_TYPE,
)
from promptpotter.domain.results import CandidateProposal
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
    from promptpotter.application.optimizers.potter.state import PotterState

import logging

logger = logging.getLogger(__name__)


def _parse_evidence_grounding(raw: VariantEvidenceGrounding | None) -> EvidenceGrounding | None:
    """Permissive on purpose: a missing grounding is a behaviour-check failure, never a parse one."""
    if raw is None:
        logger.warning(
            "l1_generate: variant emitted without evidence_grounding — "
            "routed to evidence_grounding_present wound channel"
        )
        return None
    return EvidenceGrounding(field=raw.field, citation=raw.citation.strip())


@dataclass(frozen=True)
class L1Generation:
    """``citable`` and ``exploration_budget`` are ``None`` where the proposals came back off disk."""

    proposals: list[CandidateProposal]
    parse_failure: str | None = None
    citable: tuple[str, ...] | None = None
    exploration_budget: str | None = None


async def l1_generate(
    ctx: NodeContext[L1GenerateKnobs],
    state: PotterState,
    *,
    n_variants: int,
    temperature: float,
) -> L1Generation:
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    session = state.session
    round_num = ctx.round_num
    model = ctx.optimizer.model("l1_generate")
    opt_sp = ctx.parent
    pipeline_schema = session.pipeline_schema

    bundle = build_bundle(ctx, state)
    filled = DispatchHub.fill(load_optimizer_prompt("l1_generate"), bundle, node="l1_generate")
    breakdown = filled.breakdown
    budget = bundle.cycle_slice.exploration_budget
    citable = citable_fields(
        node_layout("l1_generate", state.memory),
        exploration_budget=budget,
        rendered=filled.rendered,
    )
    prompt_vars: dict[str, str] = {"n_variants": str(n_variants), **filled.injection_vars}

    schema_field_rename = ctx.knobs.schema_field_rename
    output_schema = (
        build_l1_response_schema(
            pipeline_schema,
            citable_fields=citable,
            inner_optimizer=bundle.inner_optimizer,
            silent_panels=breakdown.silent,
            schema_field_rename=schema_field_rename,
            n_variants=n_variants,
        )
        if pipeline_schema
        else None
    )
    parent_point = ctx.parent_point
    assert parent_point is not None
    response_model = build_l1_response_model(
        effective_l1_field_names(),
        parent_prompt=opt_sp.prompt_fields(),
        parent_params=parent_point.pipeline_params,
        parent_shot_ids=opt_sp.shot_ids,
    )
    try:
        generated, _prompt, _repairs = await run_optimizer_node(
            template_name="l1_generate",
            prompt_vars=prompt_vars,
            temperature=temperature,
            response_model=response_model,
            response_schema=output_schema,
            context=LLMCallContext(
                ledger=session.state.ledger,
                round_num=round_num,
                cache=session.store.optimizer_reuse,
                injections=breakdown,
            ),
            template=filled.template,
        )
    except OptimizerPromptParseError as parse_err:
        # TOOLING drops the round from L4 outer scoring; a truncation never is: a prompt that outgrew max_tokens owns its failure.
        is_empty = parse_err.is_empty
        truncated = parse_err.first_finish_reason == "length"
        reason = PARSE_FAILURE_TOOLING if is_empty else PARSE_FAILURE_MALFORMED
        cause = (
            "response truncated at max_tokens — the optimizer prompt asks for more than the "
            "budget carries"
            if truncated
            else "provider returned empty/truncated content"
            if is_empty
            else "optimizer prompt parse failure after retry"
        )
        logger.error(
            "L1 R%d: %s — zero candidates this round (failing attempt=%d chars) [%s]",
            round_num,
            cause,
            parse_err.failing_chars,
            parse_err.diagnosis(),
        )
        emit_round_warning(
            kind="l1_zero_candidates",
            severity="error",
            message=(
                "Optimizer produced 0 candidates this round — "
                + (
                    "the optimizer LLM's response was cut off at max_tokens; shrink the "
                    "optimizer prompt or raise the node's max_tokens"
                    if truncated
                    else "the optimizer LLM returned empty/truncated output"
                    if is_empty
                    else "the optimizer LLM's response failed schema validation after a repair retry"
                )
                + f" (model {model})."
            ),
            detail={"reason": reason, "model": model, **parse_err.warning_detail()},
        )
        return L1Generation([], reason, citable, budget)
    # The repair-retry path can leak a raw str/dict/list when JSON parses but does not bind.
    if not isinstance(generated, L1GenerateOutput):
        logger.error(
            "L1 R%d: l1_generate response decoded as %s instead of L1GenerateOutput — "
            "treating as parse failure, returning zero candidates",
            round_num,
            type(generated).__name__,
        )
        emit_round_warning(
            kind="l1_zero_candidates",
            severity="error",
            message=(
                "Optimizer produced 0 candidates this round — the optimizer LLM's "
                f"response decoded as {type(generated).__name__} instead of the expected "
                f"schema (model {model})."
            ),
            detail={"reason": PARSE_FAILURE_WRONG_TYPE, "model": model},
        )
        return L1Generation([], PARSE_FAILURE_WRONG_TYPE, citable, budget)

    variants_list = generated.variants

    population: list[CandidateProposal] = []
    for v in variants_list[:n_variants]:
        # A hallucinated node name is NOT pre-filtered: ``bench/children.py::overlay_failures`` owns it.
        changes: dict[str, Any] = dict(v.prompt_fields_updates)
        if v.shot_ids is not None:
            changes["shot_ids"] = v.shot_ids
        population.append(
            ctx.child(
                [opt_sp],
                overlay=v.pipeline_overlay,
                changes_description=v.changes_description,
                evidence_grounding=_parse_evidence_grounding(v.evidence_grounding),
                **changes,
            )
        )

    return L1Generation(population, None, citable, budget)


__all__ = ["L1Generation", "l1_generate"]
