"""L1 population shaping: project proposals → OSP, build score reports.

These helpers run between L1's two phases — generation (``l1_generate``)
produces ``CandidateProposal``s, scoring (``score_population`` /
``l1_score``) consumes ``OptSearchPoint``s and emits ``CandidateScore``s.
This module owns:

- ``parse_population`` — proposals → OSPs + merged pipeline_params,
  attaching schema-compliance failures.
- ``_merge_pipeline_params`` — deep-merge LLM overrides into the active
  ``pipeline_params``, dropping overrides for excluded nodes.
- ``_build_score_report`` — typed ``CandidateScore`` factory with stable
  defaults across the four exit paths in ``score_population``.
- ``_pobb_decision_data`` — shared archival blob for elimination + lock-in
  decisions.
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
    CandidateScore,
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
    """Project proposals → OptSearchPoints + merged pp; attaches validation failures.

    ``forbidden_axes_strict`` (default on) gates the strict-mode rejection
    of ``model``/``provider`` mutations — see
    ``OptimizationConfig.forbidden_axes_strict``.
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
            # Re-propose check: rejects (param, value) tuples already in
            # opt_sp.wounds.runtime_failures (intra-cycle or inherited from
            # sibling forks via Cycle.start). Runs even when schema-compliance
            # passes — different rejection class.
            rf_outcome = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run(
                pipeline_params_override,
                opt_sp=osp,
            )
            if rf_outcome is not None:
                failures.extend(rf_outcome.evidence["failures"])
            if failures:
                osp.wounds.validation_failures = failures
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
    elimination_context: dict[str, Any] | None = None,
    degradation_context: dict[str, Any] | None = None,
    resumed_from_cache: bool = False,
    invalid: bool = False,
    new_runtime_failure: RuntimeFailure | None = None,
    l1_diversity: float = 1.0,
) -> CandidateScore:
    """Build the typed candidate score report — stable shape, defaults always present.

    ``label`` is the persisted display identity (``"C0"`` for origin,
    ``"CN.M"`` for L1 candidates). See ``domain.results.candidate_label``.

    ``elimination_context`` is populated for PoBB-driven cuts (Bayesian
    posterior comparison vs prior candidates); ``degradation_context`` is
    populated for DegradationCheck-driven aborts (fatal classification or
    threshold-rate degradation). The two are mutually exclusive at the
    decode site — the renderer branches on which one is non-empty.
    """
    evaluators = {**(score_summary.get("evaluators") or {}), "l1_diversity": l1_diversity}
    return CandidateScore(
        candidate_id=osp.lineage.id,
        label=label,
        changes_description=osp.lineage.changes_description or "",
        pipeline_params_override=pipeline_params_override,
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
        validation_failures=[vf.to_dict() for vf in osp.wounds.validation_failures],
        runtime_failures=[new_runtime_failure.to_dict()] if new_runtime_failure else [],
        elimination_context=dict(elimination_context) if elimination_context else {},
        degradation_context=dict(degradation_context) if degradation_context else {},
    )


def pobb_decision_data(
    candidate_score: dict[str, Any],
    *,
    candidate_sample_ids: list[str] | None = None,
    prior_histories: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Shared archival data for PoBB elimination + leader-lock decisions.

    ``candidate_sample_ids`` + ``prior_histories`` are the paired-PoBB
    snapshot at decision time: the ordered sample IDs the candidate had
    measured, and each prior's fitness map restricted to those samples.
    Replay reconstructs the paired vectors directly from these without
    needing to crawl prior rounds or backfill the archive.

    ``paired_breakdown`` (per-prior mean_d, se_d, n_paired, p_better) is
    pulled through from ``check_result`` so the round audit + the live
    PoBB stream both carry the per-prior comparison the operator reads
    to triangulate whether a stall is candidate-wide or driven by one
    sticky prior.
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
