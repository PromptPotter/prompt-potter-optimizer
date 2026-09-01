"""L1 population shaping — between generation (``CandidateProposal``) and scoring (``ScoredCandidate``)."""

from __future__ import annotations

import copy
import logging
from typing import Any

from promptpotter.application.optimization.validators.l1_strict import (
    L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    L1_INNER_STEER_IS_LEGAL,
    L1_PROMPT_BLOCKS_IN_LIBRARY,
    L1_PROMPT_FIELD_NOT_GUTTED,
    L1_PROMPT_PLACEHOLDERS_INTACT,
    L1_SCHEMA_COMPLIANCE,
)
from promptpotter.application.pipeline_resolve import apply_node_overlay
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import (
    CandidateProposal,
    DegradationContext,
    EliminationContext,
    ScoredCandidate,
    is_floor_pinned,
)
from promptpotter.domain.ruler import ThetaCaveat

logger = logging.getLogger(__name__)

__all__ = [
    "INVALID_SCORES",
    "build_score_report",
    "fatal_validation_failures",
    "merge_pipeline_params",
    "parse_population",
    "pobb_decision_data",
]


def merge_pipeline_params(
    base: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    schema: PipelineSchema | None,
) -> dict[str, Any] | None:
    """The ONE candidate-override merge: overlay onto a DEEP COPY, then drop overrides for inactive nodes. Shared by the
    live L1 path and the ``verify`` / ``ab`` replays, so a re-derived candidate hashes the config the loop did."""
    if not overrides:
        return base
    merged = apply_node_overlay(copy.deepcopy(base or {}), overrides, schema)
    if schema:
        # DECLARED, not the running chain: this guard strips an edit to a node that does not
        # EXIST — a hallucinated name — and a node reached only by escalating exists.
        _declared = {n.name for n in schema.config_nodes}
        for k, _cfg in list(node_config_items(merged)):
            if k not in _declared:
                logger.warning("Dropping LLM override for undeclared node %r", k)
                del merged[k]
    return merged


def parse_population(
    proposals: list[CandidateProposal],
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema | None,
    *,
    prompt_block_catalogue: str = "guidance",
) -> tuple[list[OptSearchPoint], list[dict[str, Any] | None]]:
    """Project proposals into searchpoints. ``model`` / ``provider`` mutations are ALWAYS rejected — operator-owned axes,
    never on L1's surface. An off-library prompt-field value is rejected only under ``restrict``."""
    opt_sp_list: list[OptSearchPoint] = []
    merged: list[dict[str, Any] | None] = []
    for cp in proposals:
        pipeline_params_override = cp.pipeline_params_override
        opt_sp = cp.opt_sp
        merged_pp = merge_pipeline_params(pipeline_params, pipeline_params_override, schema)
        if schema:
            failures: list[ValidationFailure] = []
            block_outcome = L1_PROMPT_BLOCKS_IN_LIBRARY.run(
                cp.prompt_fields_override,
                prompt_block_catalogue=prompt_block_catalogue,
            )
            if block_outcome is not None:
                failures.extend(block_outcome.evidence["failures"])
            if pipeline_params_override:
                outcome = L1_SCHEMA_COMPLIANCE.run(
                    pipeline_params_override,
                    pipeline_schema=schema,
                )
                if outcome is not None:
                    failures.extend(outcome.evidence["failures"])
                # Re-propose check: rejects (param, value) already in
                # opt_sp.memory.wounds.runtime_failures; runs even when schema-compliance passes.
                rf_outcome = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run(
                    pipeline_params_override,
                    opt_sp=opt_sp,
                )
                if rf_outcome is not None:
                    failures.extend(rf_outcome.evidence["failures"])
                # The DELTA, like the two above and unlike the placeholder check below: these
                # convict a candidate for what it PROPOSED, and a child inheriting an ancestor's
                # prose proposed nothing. The gutting check takes the parent's params because the
                # length it judges is a COMPARISON — the delta alone cannot say what it replaced.
                for outcome in (
                    L1_INNER_STEER_IS_LEGAL.run(pipeline_params_override),
                    L1_PROMPT_FIELD_NOT_GUTTED.run(
                        pipeline_params_override,
                        pipeline_params=pipeline_params,
                    ),
                ):
                    if outcome is not None:
                        failures.extend(outcome.evidence["failures"])
            # Mandatory placeholders intact — the evolved TARGET prompt (runs even when the
            # mutation is prompt-fields-only, the exact case that drops {{combined_text}})
            # AND, on an L4 campaign, the MERGED inner optimizer prompts (a child of a broken
            # parent inherits a severed port without re-proposing it).
            ph_outcome = L1_PROMPT_PLACEHOLDERS_INTACT.run(
                merged_pp or {},
                opt_sp=opt_sp,
                pipeline_schema=schema,
            )
            if ph_outcome is not None:
                failures.extend(ph_outcome.evidence["failures"])
            if failures:
                opt_sp.memory.wounds.validation_failures = failures
                for vf in failures:
                    logger.warning(
                        "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
                        opt_sp.lineage.id[:8],
                        vf.axis,
                        vf.value,
                        vf.allowed,
                        vf.reason,
                    )
        opt_sp_list.append(opt_sp)
        merged.append(merged_pp)
    return opt_sp_list, merged


def build_score_report(
    opt_sp: OptSearchPoint,
    pipeline_params_override: dict[str, Any] | None,
    score_summary: dict[str, Any],
    query_results: list[Any],
    dataset: list[Any],
    *,
    label: str,
    sp_hash: str,
    resolved_pipeline_params: dict[str, Any] | None = None,
    aborted: bool = False,
    elimination_stopped: bool = False,
    elimination_context: EliminationContext | None = None,
    degradation_context: DegradationContext | None = None,
    invalid: bool = False,
    new_runtime_failure: RuntimeFailure | None = None,
    l1_diversity: float = 1.0,
) -> ScoredCandidate:
    """Typed candidate score report. The CI is CARRIED from the gateway's own fold
    (`search_point_scorer::_composite`), never re-derived here — one writer, one band, and the
    same band the live row already showed. ``sp_hash`` is the scored searchpoint's own
    ``sp_hash(session.pipeline_schema)`` — the call ``build_dataset_run_data`` makes to key the
    rows — so the report and the archive name one identity; ``""`` where nothing was measured."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.scoring.evaluators import materialize_row_derivable

    evaluators = {**(score_summary.get("evaluators") or {}), "l1_diversity": l1_diversity}
    # Refresh the row-derivable subset from the rows, as the read-side mask does (`mask/load.py`):
    # on a RECONSTRUCT-from-disk path — resume repair, an origin replayed off its round file — the
    # snapshot carries whatever evaluator vocabulary was current when it was written, so a formula
    # naming a term added since halts on a name its own rows can answer. No row-derivable evaluator
    # namespaces by node, so a bare name cannot shadow a `{node}_{name}` one. An EMPTY snapshot is
    # an invalid / force-zeroed candidate and stays empty rather than acquiring a real accuracy.
    if evaluators.get("accuracy") is not None and query_results:
        evaluators.update(materialize_row_derivable(query_results))
    return ScoredCandidate(
        mean_fitness_ci_lo=score_summary.get("mean_fitness_ci_lo"),
        mean_fitness_ci_hi=score_summary.get("mean_fitness_ci_hi"),
        # Decided HERE, from the rows, rather than beside the θ it qualifies: this is the one
        # `ScoredCandidate` construction site, so stamping it at the election would miss round 0,
        # which holds no election fit — and an ORIGIN at 0.0 on every cell is the instance that
        # matters most, since every later round's lift is measured against it.
        theta_caveat=ThetaCaveat.FLOOR_PINNED if is_floor_pinned(query_results) else None,
        candidate_id=opt_sp.lineage.id,
        label=label,
        changes_description=opt_sp.lineage.changes_description or "",
        pipeline_params_override=pipeline_params_override,
        resolved_pipeline_params=resolved_pipeline_params,
        sp_hash=sp_hash,
        prompt_fields=opt_sp.prompt_field_dict(),
        accuracy=score_summary["accuracy"],
        composite_fitness=score_summary["composite_fitness"],
        total=score_summary["total"],
        evaluators=evaluators,
        escalation_aborted=aborted,
        elimination_stopped=elimination_stopped,
        scored_samples=len(query_results),
        expected_samples=len(dataset),
        cached_samples=sum(1 for r in query_results if r.get("cached")),
        partial_reason=str(score_summary.get("partial_reason", "")),
        invalid=invalid,
        validation_failures=list(opt_sp.memory.wounds.validation_failures),
        runtime_failures=[new_runtime_failure] if new_runtime_failure else [],
        elimination_context=elimination_context or {},
        degradation_context=degradation_context or {},
    )


def pobb_decision_data(
    candidate_score: dict[str, Any],
    *,
    candidate_sample_ids: list[str] | None = None,
    prior_histories: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Archival data for PoBB decisions — the per-prior per-sample GRADED responses ARE the snapshot at decision time,
    so replay re-fits θ from exactly these without crawling prior rounds."""
    return {
        "p_best": float(candidate_score.get("p_best", 0.0)),
        "leader_id": str(candidate_score.get("leader_id", "")),
        "paired_breakdown": dict(candidate_score.get("paired_breakdown") or {}),
        "candidate_sample_ids": list(candidate_sample_ids or []),
        "prior_histories": dict(prior_histories or {}),
    }


INVALID_SCORES: dict[str, Any] = {
    "accuracy": 0.0,
    "composite_fitness": 0.0,
    "total": 0,
    "errors": 0,
    "invalid": True,
}


def fatal_validation_failures(opt_sp: OptSearchPoint) -> list[ValidationFailure]:
    """The failures that cost a candidate its measurement, as opposed to riding along as signal.

    ``hallucinated_node`` is the one non-fatal reason — the phantom edit is stripped and the real
    edits still ran. One definition, because the scorer and the yield count must agree on which
    candidates measured."""
    return [
        vf for vf in opt_sp.memory.wounds.validation_failures if vf.reason != "hallucinated_node"
    ]
