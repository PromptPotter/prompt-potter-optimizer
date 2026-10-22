from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.llm_call import run_optimizer_node
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
from promptpotter.application.optimizers.potter.records import Ladder
from promptpotter.application.optimizers.potter.validators.l1_strict import (
    L1_BLOCKS_IN_POOL,
    L1_SHOTS_IN_DEMO_POOL,
)
from promptpotter.domain.opt_search_point import EvidenceGrounding, Locus, loci_of
from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_MALFORMED,
    PARSE_FAILURE_TOOLING,
    PARSE_FAILURE_WRONG_TYPE,
)
from promptpotter.domain.results import Proposal
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning

if TYPE_CHECKING:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
    from promptpotter.domain.wounds import ValidationFailure

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

    proposals: list[Proposal]
    parse_failure: str | None = None
    citable: tuple[str, ...] | None = None
    exploration_budget: str | None = None


async def l1_generate(
    ctx: NodeContext[L1GenerateKnobs],
    *,
    n_variants: int,
    temperature: float,
) -> L1Generation:
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    round_num = ctx.round_num
    model = ctx.model
    opt_sp = ctx.parent
    pipeline_schema = ctx.schema

    bundle = build_bundle(ctx, ctx.slice(Ladder))
    filled = DispatchHub.fill(load_optimizer_prompt("l1_generate"), bundle, node="l1_generate")
    breakdown = filled.breakdown
    budget = bundle.cycle_slice.exploration_budget
    citable = citable_fields(
        node_layout("l1_generate", bundle.l2),
        exploration_budget=budget,
        rendered=filled.rendered,
    )
    prompt_vars: dict[str, str] = {"n_variants": str(n_variants), **filled.injection_vars}

    schema_field_rename = ctx.knobs.schema_field_rename
    # Under `closed` the wire takes an active entry's id where a pooled field's text would go.
    pooled = (
        {name: {b.id: b.text for b in blocks} for name, blocks in bundle.block_window().items()}
        if bundle.block_access == "closed"
        else None
    )
    output_schema = (
        build_l1_response_schema(
            pipeline_schema,
            citable_fields=citable,
            inner_cells=bundle.inner_cells,
            silent_panels=breakdown.silent,
            schema_field_rename=schema_field_rename,
            n_variants=n_variants,
            pooled_fields=None if pooled is None else {f: list(ids) for f, ids in pooled.items()},
        )
        if pipeline_schema
        else None
    )
    parent_point = ctx.parent_point
    assert parent_point is not None
    # The wire speaks the DEMO POOL panel's ids; an individual holds sample keys.
    key_of = {s.id: s.key for s in bundle.demo_pool}
    response_model = build_l1_response_model(
        effective_l1_field_names(),
        parent_prompts={node: p.fields for node, p in opt_sp.prompts.items()},
        parent_params=parent_point.pipeline_params,
        parent_shots={
            node: [bundle.slot_of[k] for k in p.shots] for node, p in opt_sp.prompts.items()
        },
    )
    try:
        generated, _prompt, _repairs = await run_optimizer_node(
            template_name="l1_generate",
            prompt_vars=prompt_vars,
            temperature=temperature,
            response_model=response_model,
            response_schema=output_schema,
            context=ctx.call_context(injections=breakdown),
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

    population: list[Proposal] = []
    for v in variants_list[:n_variants]:
        updates = v.prompt_fields_updates
        failures: list[ValidationFailure] = []
        if pooled is not None:
            if (blocks_outcome := L1_BLOCKS_IN_POOL.check(updates, offered=pooled)) is not None:
                failures += blocks_outcome.evidence["failures"]
            updates = {
                node: {f: pooled[f][i] for f, i in fields.items() if i in pooled.get(f, {})}
                for node, fields in updates.items()
            }
        # Unfiltered: `bench/children.py::carried_writes` fails the child on an undeclared node or field.
        prompt_loci: dict[Locus, Any] = loci_of(updates)
        prompt_loci.update(
            {
                Locus(node, SHOTS_KEY): [key_of[i] for i in ids if i in key_of]
                for node, ids in v.shots.items()
            }
        )
        shots_outcome = L1_SHOTS_IN_DEMO_POOL.check(
            v.shots, demo_ids=key_of.keys(), k_max=ctx.knobs.k_max
        )
        if shots_outcome is not None:
            failures += shots_outcome.evidence["failures"]
        population.append(
            ctx.child(
                [opt_sp],
                overlay=v.pipeline_overlay,
                failures=failures,
                prompt_loci=prompt_loci,
                changes_description=v.changes_description,
                evidence_grounding=_parse_evidence_grounding(v.evidence_grounding),
            )
        )

    return L1Generation(population, None, citable, budget)


__all__ = ["L1Generation", "l1_generate"]
