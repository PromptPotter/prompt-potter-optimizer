from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from promptpotter.application.bench.node_context import (
    Calls,
    Children,
    Frontier,
    Lineage,
    Ruler,
    Target,
)
from promptpotter.application.optimizers.nodes import (
    CheckResult,
    Proposals,
    ReviewReading,
    ReviewStat,
)
from promptpotter.application.optimizers.potter.behavior_checks import (
    CHECK_REGISTRY,
    ValidatorContext,
    run_all_checks,
    run_all_l2_checks,
)
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    RECOMBINATION_ID_CHARS,
    l1_target_schema,
)
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.injections.layer_state import (
    format_l1_critique_for_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.injections.panels import (
    clustered_sample_ids,
    detectable_rows,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import citable_fields
from promptpotter.application.optimizers.potter.dispatch.l1_wire_schema import (
    build_l1_response_schema,
    effective_l1_field_names,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    CLUSTER_ROWS_MIN,
    VariantEvidenceGrounding,
    build_l1_response_model,
    critique_response_model,
)
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.records import (
    L1Readout,
    L2Memory,
    Ladder,
    Recombination,
)
from promptpotter.application.optimizers.potter.validators import (
    detect_invariants,
    l1_blocks_in_pool,
    l1_citation_in_panel,
    l1_config_not_in_runtime_failures,
    l1_inner_layout_applies,
    l1_inner_placeholders_intact,
    l1_inner_steer_is_legal,
    l1_prompt_field_not_gutted,
    l1_prompt_fields_open,
    l1_prose_within_ceiling,
    l1_shots_in_demo_pool,
)
from promptpotter.application.optimizers.variation.members import (
    Cross,
    crossable,
    most_donated,
)
from promptpotter.domain.arms import Proposal
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.opt_search_point import EvidenceGrounding, Locus, OptSearchPoint, loci_of
from promptpotter.domain.optimizer_state import (
    PARSE_FAILURE_ALL_NO_OP,
    PARSE_FAILURE_MALFORMED,
    PARSE_FAILURE_TOOLING,
    Critique,
    CritiqueReadout,
)
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import OptimizerFact, RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.run_standing import rounds_without_advance
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.wounds import COLLAPSE_WORDS, RuntimeFailure, fatal_validation_failures
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.runner.inner.tasks import InnerCells
    from promptpotter.domain.pipeline_params import PipelineParams
    from promptpotter.domain.wounds import ValidationFailure

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


async def distil_critique(ctx: NodeContext) -> Critique:
    """Of the round *ctx* stands on, whether the round adapts or a later walk re-runs it; the readout a dict so persistence does not drag Pydantic into the domain serialization path."""
    bundle = build_bundle(ctx, ctx.slice(Ladder))
    calls = ctx.read(Calls)
    filled = DispatchHub.fill(calls.prompt(), bundle, node=ctx.node)
    # A cluster an edit could fix whole and still not separate from its parent is noise to target.
    least = max(CLUSTER_ROWS_MIN, detectable_rows(bundle) or 0)
    result = await calls.answer(
        critique_response_model(frozenset(clustered_sample_ids(bundle)), least), filled
    )
    return Critique(readout=cast(CritiqueReadout, result.model_dump()))


def parse_population(
    proposals: list[Proposal],
    parent: OptSearchPoint,
    pipeline_params: PipelineParams,
    schema: PipelineSchema,
    *,
    runtime_failures: Sequence[RuntimeFailure],
    inner_cells: InnerCells | None,
) -> list[Proposal]:
    """L1's rejections only: search space, route keys and backend placeholders are the bench's (``Children.child``); shots are judged on the wire (``l1.py``), where demo ids still exist."""
    parsed: list[Proposal] = []
    for cp in proposals:
        pipeline_overlay = cp.overlay
        opt_sp = cp.individual
        merged_pp = pipeline_params.overlaid(pipeline_overlay, schema)
        # The DELTA, never the child's whole prompt: an inherited field was not proposed.
        prompt_edit = candidate_delta(opt_sp.prompts, parent.prompts, None, None).prompt
        failures = l1_prompt_fields_open(prompt_edit, pipeline_schema=schema)
        if pipeline_overlay:
            # The DELTA, unlike the placeholder check below: these convict what was PROPOSED.
            failures += [
                *l1_config_not_in_runtime_failures(
                    pipeline_overlay,
                    runtime_failures=runtime_failures,
                    pipeline_params=merged_pp,
                ),
                *l1_inner_steer_is_legal(
                    pipeline_overlay,
                    inner_nodes=() if inner_cells is None else inner_cells.owners,
                ),
                *l1_inner_layout_applies(pipeline_overlay),
                *l1_prose_within_ceiling(pipeline_overlay, schema=schema, inner_cells=inner_cells),
                *l1_prompt_field_not_gutted(
                    pipeline_overlay,
                    inner_cells=inner_cells,
                    pipeline_params=pipeline_params,
                ),
            ]
        # The MERGED params: a child of a broken parent inherits a severed port without proposing it.
        failures += l1_inner_placeholders_intact(merged_pp, inner_cells=inner_cells)
        # A replayed proposal already carries them: the bench banks the walk's arms validated.
        failures = [vf for vf in failures if vf not in cp.validation_failures]
        if failures:
            cp = cp.model_copy(update={"validation_failures": [*cp.validation_failures, *failures]})
            for vf in failures:
                logger.warning(
                    "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
                    opt_sp.id[:8],
                    vf.axis,
                    vf.value,
                    vf.allowed,
                    vf.reason,
                )
        parsed.append(cp)
    return parsed


def variants_this_round(n_variants: int, l2: L2Memory) -> int:
    """L2's `n_variants` steer, capped at 3× the knob so L2 cannot blow up the round budget."""
    steered = l2.steered("l1_generate")
    return min(int(steered.get("n_variants", n_variants)), n_variants * 3)


def rejoining(lineage: Lineage) -> list[Proposal]:
    """The population beside the parent — the leader a held round kept — as it was walked, ahead
    of L1's own, so each of those races it too (`election.py::elect_on_theta`)."""
    walked = lineage.rounds[-1].arms
    return [
        Proposal(individual=member, overlay=walked[ArmKey(member.id)].overlay)
        for member in lineage.population
        if member.id != lineage.parent.id
    ]


def l1_readout(ctx: NodeContext, generation: L1Generation) -> L1Readout:
    """Called once every reject posture has read *generation*: the yield is counted here alone."""
    axes = ctx.read(Ruler).axes
    rejected = Counter(
        fatal[0].reason
        for cp in generation.proposals
        if (fatal := fatal_validation_failures(cp.validation_failures))
    )
    proposed = len(generation.proposals)
    return L1Readout(
        l1_yield=(proposed - rejected.total()) / proposed if proposed else None,
        l1_proposed=proposed,
        l1_rejected=dict(rejected),
        l1_parse_failure=generation.parse_failure,
        l1_citable=list(generation.citable),
        l1_exploration_budget=generation.exploration_budget,
        axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
    )


async def propose_l1_population(ctx: NodeContext) -> Proposals:
    parent_point = ctx.read(Frontier).parent_point
    lineage, target = ctx.read(Lineage), ctx.read(Target)
    # Outside L1's generation: no invariant reads them and its yield never counts them.
    kept = rejoining(lineage)
    generation = await _generate(ctx)
    checked = detect_invariants(
        generation.proposals, lineage.parent, parent_point.pipeline_params, lineage.rounds
    )
    # A batch moving none of the parent is missing data, not one proposer wound per variant.
    if checked and all(
        any(vf.reason == "no_op_variant" for vf in cp.validation_failures) for cp in checked
    ):
        logger.error("L1 R%d: every variant is a no-op — zero candidates", ctx.round_num)
        checked = []
        generation = replace(generation, parse_failure=PARSE_FAILURE_ALL_NO_OP)
    parsed = parse_population(
        checked,
        lineage.parent,
        parent_point.pipeline_params,
        l1_target_schema(target.schema),
        runtime_failures=target.runtime_failures(),
        inner_cells=target.inner_cells,
    )
    generation = replace(generation, proposals=parsed)
    ctx.emit(l1_readout(ctx, generation))
    return Proposals(proposals=[*kept, *generation.proposals])


async def _generate(ctx: NodeContext) -> L1Generation:
    l2 = ctx.slice(L2Memory)
    # Unsteered, the call runs at the node's own temperature; `NodeConfig` types a steered one.
    return await l1_generate(
        ctx,
        n_variants=variants_this_round(ctx.knobs(L1GenerateKnobs).n_variants, l2),
        temperature=l2.steered("l1_generate").get("temperature"),
    )


RoundOneVerdict = Literal["healthy", "degraded", "broken", "unknown"]


@dataclass(frozen=True)
class L1Stats:
    """``None`` on a rate is NOT MEASURED, never 0.0 or 1.0: a rate over zero checks is no clean bill."""

    yield_rate: float | None
    top_lift_mean: float | None
    behavior_pass_rate: float | None
    stagnation_max: int
    l2_fires: int
    l2_behavior_pass_rate: float | None
    round_1_verdict: RoundOneVerdict


def compute_l1_stats(
    rounds: list[RoundResult],
    *,
    behavior_results: list[list[CheckResult]],
    l2_behavior_results: list[list[CheckResult]] | None = None,
) -> L1Stats:
    yield_rate = _mean_yield_rate(rounds)
    top_lifts = _top_lifts(rounds)
    behavior_pass_rate = _behavior_pass_rate(behavior_results)
    l2_behavior_pass_rate = _behavior_pass_rate(l2_behavior_results or [])
    # A round's L2 checks are empty exactly where L2 did not fire (`_behavior_per_round`).
    l2_fires = sum(1 for checks in l2_behavior_results or [] if checks)
    round_1_verdict = _compute_round_1_verdict(
        rounds,
        round_1_behavior=behavior_results[0] if behavior_results else [],
    )
    return L1Stats(
        yield_rate=yield_rate,
        top_lift_mean=sum(top_lifts) / len(top_lifts) if top_lifts else None,
        behavior_pass_rate=behavior_pass_rate,
        stagnation_max=max(
            (rounds_without_advance(rounds[: i + 1]) for i in range(len(rounds))), default=0
        ),
        l2_fires=l2_fires,
        l2_behavior_pass_rate=l2_behavior_pass_rate,
        round_1_verdict=round_1_verdict,
    )


def review_reading(
    rounds: list[RoundResult],
    audits: list[RoundAudit | None],
    *,
    context_object: list[str],
) -> ReviewReading:
    l1_checks, l2_checks = _behavior_per_round(rounds, audits, context_object)
    stats = compute_l1_stats(rounds, behavior_results=l1_checks, l2_behavior_results=l2_checks)
    return ReviewReading(
        checks=l1_checks,
        check_ids=tuple(CHECK_REGISTRY),
        verdict=ReviewStat("round-1 conformance", stats.round_1_verdict),
        stats=(
            ReviewStat("yield_rate", stats.yield_rate, ".2f"),
            ReviewStat("top_lift_mean", stats.top_lift_mean, "+.4f"),
            ReviewStat("behavior_pass_rate", stats.behavior_pass_rate, ".2f"),
            ReviewStat("l2_behavior_pass_rate", stats.l2_behavior_pass_rate, ".2f"),
            ReviewStat("stagnation_max", stats.stagnation_max),
            ReviewStat("l2_fires", stats.l2_fires),
        ),
        feedback=[format_l1_critique_for_prompt(r.closed_state.feedback()).strip() for r in rounds],
    )


def yield_facts(round_result: RoundResult) -> list[OptimizerFact]:
    state = round_result.closed_state.output(L1Readout)
    if not state.l1_rejected:
        return []
    live = state.l1_proposed - sum(state.l1_rejected.values())
    bits = ", ".join(
        f"{n} {COLLAPSE_WORDS.get(reason, reason.replace('_', ' '))}"
        for reason, n in state.l1_rejected.items()
    )
    text = f"{live}/{state.l1_proposed} ({bits})"
    return [
        OptimizerFact(
            key="l1_yield", label="L1 yield", text=text, value=state.l1_yield, kind="stat"
        )
    ]


def critique_facts(round_result: RoundResult) -> list[OptimizerFact]:
    if not (critique := format_l1_critique_for_prompt(round_result.closed_state.feedback())):
        return []
    return [OptimizerFact(key="critique", label="Critique", text=critique, value=None, kind="note")]


def _behavior_per_round(
    rounds: list[RoundResult],
    audits: list[RoundAudit | None],
    context_object: list[str],
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[RoundAudit] = []
    # A round's generator reads the critique of the round BEFORE it.
    read_critique: CritiqueReadout | None = None
    for round_data, audit in zip(rounds, audits, strict=True):
        payload = round_data.closed_state.output(L1Readout)
        clusters = tuple(
            frozenset(c["sample_ids"]) for c in (read_critique or {}).get("failure_highlights", [])
        )
        read_critique = round_data.closed_state.feedback()
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        ctx = ValidatorContext(
            clusters=clusters,
            prior_rounds=list(prior_audits),
            citable=None if payload.l1_citable is None else tuple(payload.l1_citable),
            context_object=context_object,
            exploration_budget=payload.l1_exploration_budget,
            peaked_axes=frozenset(payload.axis_memory_peaked),
        )
        l1_out.append(run_all_checks(audit, ctx))
        l2_out.append(run_all_l2_checks(audit, ctx))
        prior_audits.append(audit)
    return l1_out, l2_out


def _compute_round_1_verdict(
    rounds: list[RoundResult],
    *,
    round_1_behavior: list[CheckResult],
) -> RoundOneVerdict:
    """Conformance only: yield and lift are confounded by the dataset's headroom."""
    if not rounds:
        return "unknown"

    parse_failure = rounds[0].closed_state.output(L1Readout).l1_parse_failure
    if parse_failure == PARSE_FAILURE_MALFORMED:
        return "broken"
    # What remains is TOOLING: the verdict is a CHARGE against the prompt, and this round earns none.
    if parse_failure is not None:
        return "unknown"
    # Zero failures out of ZERO checks is not ``healthy``.
    if not round_1_behavior:
        return "unknown"

    failed_total = sum(1 for c in round_1_behavior if not c.passed)
    if failed_total >= 2:
        return "broken"
    if failed_total == 0:
        return "healthy"
    return "degraded"


def _mean_yield_rate(rounds: list[RoundResult]) -> float | None:
    yields = [y for r in rounds if (y := r.closed_state.output(L1Readout).l1_yield) is not None]
    return sum(yields) / len(yields) if yields else None


def _top_lifts(rounds: list[RoundResult]) -> list[float]:
    tops: list[float] = []
    for r in rounds:
        lifts = [
            fit.vs_reference.headline.estimate.value
            for key in r.panel
            if (fit := r.fit(key)) is not None
            and fit.vs_reference is not None
            and fit.vs_reference.headline is not None
        ]
        if lifts:
            tops.append(max(lifts))
    return tops


def _behavior_pass_rate(behavior_results: list[list[CheckResult]]) -> float | None:
    total = sum(len(r) for r in behavior_results)
    if not total:
        return None
    passed = sum(1 for r in behavior_results for c in r if c.passed)
    return passed / total
