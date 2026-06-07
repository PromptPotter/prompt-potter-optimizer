"""L1 population shaping: project proposals → OSP + build score reports.

Sits between L1 generation (``CandidateProposal``) and L1 scoring (``ScoredCandidate``).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from promptpotter.application.optimization.validators.l1_strict import (
    L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    L1_SCHEMA_COMPLIANCE,
)
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import (
    CandidateProposal,
    DegradationContext,
    EliminationContext,
    ScoredCandidate,
)

logger = logging.getLogger(__name__)

__all__ = [
    "INVALID_SCORES",
    "build_score_report",
    "merge_pipeline_params",
    "parse_population",
    "pobb_decision_data",
]


def merge_pipeline_params(
    base: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    schema: PipelineSchema | None,
) -> dict[str, Any] | None:
    """Deep-merge ``overrides`` into ``base``; drop overrides for nodes outside active steps."""
    if not overrides:
        return base
    merged: dict[str, Any] = copy.deepcopy(base or {})
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    if schema:
        _active = set(schema.active_steps)
        for k in list(merged):
            if k != "steps" and isinstance(merged[k], dict) and k not in _active:
                logger.warning("Dropping LLM override for excluded node %r", k)
                del merged[k]
    return merged


def parse_population(
    proposals: list[CandidateProposal],
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema | None,
    *,
    forbidden_axes_strict: bool = True,
) -> tuple[list[OptSearchPoint], list[dict[str, Any] | None]]:
    """Project proposals → OptSearchPoints + merged pp; attach validation failures.

    ``forbidden_axes_strict`` (default on) gates strict-mode rejection of
    ``model``/``provider`` mutations (see ``OptimizationConfig.forbidden_axes_strict``).
    """
    osp_list: list[OptSearchPoint] = []
    merged: list[dict[str, Any] | None] = []
    for cp in proposals:
        pipeline_params_override = cp.pipeline_params_override or None
        osp = cp.osp
        if schema and pipeline_params_override:
            outcome = L1_SCHEMA_COMPLIANCE.run(
                pipeline_params_override,
                pipeline_schema=schema,
                forbidden_axes_strict=forbidden_axes_strict,
            )
            failures: list[ValidationFailure] = []
            if outcome is not None:
                failures.extend(outcome.evidence["failures"])
            # Re-propose check: rejects (param, value) already in
            # opt_sp.memory.wounds.runtime_failures; runs even when schema-compliance passes.
            rf_outcome = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run(
                pipeline_params_override,
                opt_sp=osp,
            )
            if rf_outcome is not None:
                failures.extend(rf_outcome.evidence["failures"])
            if failures:
                osp.memory.wounds.validation_failures = failures
                for vf in failures:
                    logger.warning(
                        "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
                        osp.lineage.id[:8],
                        vf.axis,
                        vf.value,
                        vf.allowed,
                        vf.reason,
                    )
        osp_list.append(osp)
        merged.append(merge_pipeline_params(pipeline_params, pipeline_params_override, schema))
    return osp_list, merged


def build_score_report(
    osp: OptSearchPoint,
    pipeline_params_override: dict[str, Any] | None,
    score_summary: dict[str, Any],
    query_results: list[Any],
    dataset: list[Any],
    *,
    label: str,
    aborted: bool = False,
    elimination_stopped: bool = False,
    elimination_context: EliminationContext | None = None,
    degradation_context: DegradationContext | None = None,
    resumed_from_cache: bool = False,
    invalid: bool = False,
    new_runtime_failure: RuntimeFailure | None = None,
    l1_diversity: float = 1.0,
) -> ScoredCandidate:
    """Typed candidate score report — stable shape, defaults always present.

    ``label`` is the persisted display identity (``C0`` origin / ``CN.M`` L1).
    ``elimination_context`` (PoBB cut) and ``degradation_context``
    (DegradationCheck abort) are mutually exclusive — renderer branches on which is non-empty.
    """
    evaluators = {**(score_summary.get("evaluators") or {}), "l1_diversity": l1_diversity}
    return ScoredCandidate(
        candidate_id=osp.lineage.id,
        label=label,
        changes_description=osp.lineage.changes_description or "",
        pipeline_params_override=pipeline_params_override,
        prompt_fields=osp.prompt_field_dict(),
        accuracy=score_summary["accuracy"],
        composite_fitness=score_summary.get("composite_fitness", score_summary["accuracy"]),
        hits=score_summary["hits"],
        total=score_summary["total"],
        evaluators=evaluators,
        escalation_aborted=aborted,
        elimination_stopped=elimination_stopped,
        scored_samples=len(query_results),
        expected_samples=len(dataset),
        invalid=invalid,
        resumed_from_cache=resumed_from_cache,
        validation_failures=list(osp.memory.wounds.validation_failures),
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
    """Archival data for PoBB elimination + leader-lock decisions.

    ``candidate_sample_ids`` + ``prior_histories`` = paired-PoBB snapshot
    at decision time; replay reconstructs paired vectors without crawling
    prior rounds. ``paired_breakdown`` per-prior comparison feeds round
    audit + live PoBB stream so the operator can spot one sticky prior.
    """
    return {
        "p_best": float(candidate_score.get("p_best", 0.0)),
        "leader_id": str(candidate_score.get("leader_id", "")),
        "p_best_snapshot": dict(candidate_score.get("p_best_snapshot") or {}),
        "paired_breakdown": dict(candidate_score.get("paired_breakdown") or {}),
        "candidate_sample_ids": list(candidate_sample_ids or []),
        "prior_histories": dict(prior_histories or {}),
    }


INVALID_SCORES: dict[str, Any] = {
    "accuracy": 0.0,
    "composite_fitness": 0.0,
    "hits": 0,
    "total": 0,
    "errors": 0,
    "invalid": True,
}
