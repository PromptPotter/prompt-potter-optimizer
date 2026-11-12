from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import Calls, Children, Lineage
from promptpotter.application.optimizers.potter.dispatch.bundle import RECOMBINATION_ID_CHARS
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import citable_fields
from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
    build_l1_response_schema,
    effective_l1_field_names,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    VariantEvidenceGrounding,
    build_l1_response_model,
)
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.records import Ladder, Recombination
from promptpotter.application.optimizers.potter.validators.l1_strict import (
    l1_blocks_in_pool,
    l1_citation_in_panel,
    l1_shots_in_demo_pool,
)
from promptpotter.application.optimizers.variation.members import (
    Cross,
    crossable,
    most_donated,
)
from promptpotter.domain.arms import Proposal
from promptpotter.domain.opt_search_point import EvidenceGrounding, Locus, loci_of
from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_MALFORMED,
    PARSE_FAILURE_TOOLING,
)
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.domain.pipeline_schema import PipelineSchema
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
    proposals: list[Proposal]
    parse_failure: str | None
    citable: tuple[str, ...]
    exploration_budget: str


async def l1_generate(
    ctx: NodeContext,
    *,
    n_variants: int,
    temperature: float | None,
) -> L1Generation:
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    round_num = ctx.round_num
    calls, knobs = ctx.read(Calls), ctx.knobs(L1GenerateKnobs)
    opt_sp = ctx.read(Lineage).parent

    bundle = build_bundle(ctx, ctx.slice(Ladder))
    pipeline_schema = bundle.pipeline_schema
    filled = DispatchHub.fill(calls.prompt(), bundle, node=ctx.node)
    budget = bundle.cycle_slice.exploration_budget
    citable = citable_fields(
        node_layout("l1_generate", bundle.l2),
        exploration_budget=budget,
        rendered=filled.rendered,
    )
    schema_field_rename = knobs.schema_field_rename
    # Under `closed` the wire takes an active entry's id where a pooled field's text would go.
    pooled = (
        {name: {b.id: b.text for b in blocks} for name, blocks in bundle.block_window().items()}
        if bundle.block_access == "closed"
        else None
    )
    output_schema = build_l1_response_schema(
        pipeline_schema,
        citable_fields=citable,
        inner_cells=bundle.inner_cells,
        silent_panels=bundle.silent_l1_panels,
        schema_field_rename=schema_field_rename,
        n_variants=n_variants,
        pooled_fields=None if pooled is None else {f: list(ids) for f, ids in pooled.items()},
        recombination=bundle.recombination_offer("l1_generate"),
    )
    # The wire speaks the DEMO POOL panel's ids; an individual holds sample keys.
    key_of = {s.id: s.key for s in bundle.demo_pool}
    hosted = bundle.l2.recombinations if bundle.l2.for_round == bundle.cycle_slice.round_num else []
    try:
        generated = await calls.answer(
            build_l1_response_model(effective_l1_field_names()),
            replace(filled, prompt_vars={"n_variants": str(n_variants), **filled.prompt_vars}),
            temperature=temperature,
            response_schema=output_schema,
        )
    except OptimizerPromptParseError as parse_err:
        # TOOLING is never charged, a truncation always: a prompt that outgrew max_tokens owns its failure.
        # Either way an L4 cell whose every round yields nothing FLOORS (`l4/proxies.py::floor_reason`).
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
        return L1Generation([], reason, citable, budget)

    variants_list = generated.variants

    children = ctx.read(Children)
    population: list[Proposal] = []
    for v in variants_list[:n_variants]:
        updates = v.prompt_fields_updates
        failures: list[ValidationFailure] = []
        if pooled is not None:
            failures += l1_blocks_in_pool(updates, offered=pooled)
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
        failures += l1_shots_in_demo_pool(v.shots, demo_ids=key_of.keys(), k_max=knobs.k_max)
        grounding = _parse_evidence_grounding(v.evidence_grounding)
        failures += l1_citation_in_panel(grounding, rendered=filled.rendered)
        population.append(
            children.child(
                [opt_sp],
                overlay=v.pipeline_overlay,
                failures=failures,
                prompt_loci=prompt_loci,
                changes_description=v.changes_description,
                evidence_grounding=grounding,
            )
        )
    lineage = ctx.read(Lineage)
    if bundle.recombines("l1_generate"):
        own = [
            Recombination.model_validate(spec.model_dump())
            for spec in generated.recombinations[: bundle.recombine]
        ]
        population.extend(_recombined(children, own, lineage, pipeline_schema, host=None))
    # What L2's last fire named for this round, L1 the node that adds it.
    population.extend(_recombined(children, hosted, lineage, pipeline_schema, host="l2_context"))

    return L1Generation(population, None, citable, budget)


def _recombined(
    children: Children,
    specs: Sequence[Recombination],
    lineage: Lineage,
    schema: PipelineSchema,
    *,
    host: str | None,
) -> list[Proposal]:
    """Each spec a child of its main, a population member, and its donor, any other measured
    individual, both by id head, the donor giving the loci it names: from one to `most_donated` of
    those the two differ on. A spec failing any of these is dropped."""
    by_id = {individual.id[:RECOMBINATION_ID_CHARS]: individual for individual in lineage.measured}
    mains = {individual.id for individual in lineage.population}
    out: list[Proposal] = []
    dropped: list[str] = []
    for spec in specs:
        main, donor = by_id.get(spec.main), by_id.get(spec.donor)
        if main is None or donor is None or main.id not in mains:
            dropped.append(spec.changes_description)
            continue
        cross = Cross(main, donor)
        loci = crossable(cross, schema)
        take = {locus: 1 for locus in loci if locus.name in spec.from_donor}
        if not 0 < len(take) <= most_donated(len(loci)):
            dropped.append(spec.changes_description)
            continue
        out.append(
            children.child(
                cross, take=take, changes_description=spec.changes_description, hosted_by=host
            )
        )
    if dropped:
        logger.info("%d recombination(s) dropped: %s", len(dropped), dropped)
    return out
