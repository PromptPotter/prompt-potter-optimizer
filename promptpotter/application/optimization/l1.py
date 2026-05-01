"""L1 phase — generation → measurement → scoring → round execution.

Four labeled sections, one per L1 sub-phase:

1. **Generation** (``l1_generate``, ``build_l1_output_schema``,
   ``validate_overrides``) — produce candidate variants via LLM
   meta-prompt; validate node-overrides against the pipeline schema.
2. **Measurement** (``L1YieldStats``, ``detect_invariants``,
   ``parse_population``, ``score_population``) — post-process proposals
   (no-op + duplicate detection), parse + merge pipeline params, and
   dispatch each candidate over the three scoring exit paths
   (validation-skip / cache-hit / scored). See
   ``docs/developer/self-healing-internals.md``.
3. **Scoring orchestration** (``L1ScoringResult``, ``l1_score``) — score
   candidates and select the round winner; record the ``round_winner``
   decision for divergence replay.
4. **Round execution** (``execute_round``) — top-level L1 round driver
   that ties all four sub-phases together (generate → score → critique
   → persist).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.optimization.cycle import Cycle, Decision, record_decision
from promptpotter.application.optimization.elimination import PoBBCheck
from promptpotter.application.optimization.pipeline import (
    candidate_summaries,
    compile_l1_surface,
    format_l1_critique_for_prompt,
    load_optimizer_prompt,
    run_l1_critique,
    run_optimizer_node,
)
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    compute_composite_score,
    count_degraded_queries,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.analysis import (
    EscalationSignal,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import (
    CandidateProposal,
    CandidateScore,
    RoundBaseline,
    RoundResult,
)
from promptpotter.domain.run_records import DecisionKind
from promptpotter.domain.scoring import QueryResult
from promptpotter.domain.validators import LLMOutputValidator, StopRule, ValidatorOutcome

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.llm import LLMClientBase
from promptpotter.infrastructure.tracing import (
    CandidateCreated,
    CandidateScored,
    L1CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
    observed_node,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.runner import RunListener
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "L1_SCHEMA_COMPLIANCE",
    "L1ScoringResult",
    "L1YieldStats",
    "build_l1_output_schema",
    "detect_invariants",
    "execute_round",
    "l1_generate",
    "l1_score",
    "parse_population",
    "score_population",
    "validate_overrides",
]


# ---------------------------------------------------------------------------
# Section 1 — Generation
# ---------------------------------------------------------------------------


def _classify_axis(axis_key: str) -> str:
    """Classify a pipeline_params override key as 'prompt_field', 'task_context', or 'pipeline_param'."""
    if axis_key in PROMPT_STRING_FIELDS:
        return "prompt_field"
    if axis_key in TASK_CONTEXT_OVERRIDES:
        return "task_context"
    return "pipeline_param"


def build_l1_output_schema(pipeline_schema: PipelineSchema) -> dict:
    """Construct the JSON schema L1 must conform to.

    Starts from the static envelope sheltered in the optimizer manifest
    (``resolved_schemas['l1_generate/1']``) — variants array shape +
    prompt-string + task-context override fields — and grafts per-target
    node properties (with ``param_allowed_values`` tightened to JSON-Schema
    ``enum``) onto ``pipeline_params_override.properties``.
    """
    from copy import deepcopy

    from promptpotter.application.optimization.pipeline import get_optimizer_schema

    base_node = get_optimizer_schema().get_node("l1_generate")
    if base_node is None or base_node.output_schema is None:
        raise RuntimeError(
            "optimizer manifest missing l1_generate output_schema envelope "
            "(resolved_schemas['l1_generate/1'])"
        )
    schema = deepcopy(base_node.output_schema.json_schema)
    override_properties = schema["schema"]["properties"]["variants"]["items"]["properties"][
        "pipeline_params_override"
    ]["properties"]

    for node in pipeline_schema.nodes:
        if not node.param_keys:
            continue
        param_props: dict[str, dict] = {}
        for param in sorted(node.param_keys):
            allowed = node.param_allowed_values.get(param)
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            else:
                param_props[param] = {}
        override_properties[node.name] = {
            "type": "object",
            "properties": param_props,
            "additionalProperties": False,
        }

    return schema


def validate_overrides(
    node_overrides: dict[str, dict],
    pipeline_schema: PipelineSchema,
) -> list[ValidationFailure]:
    """Validate overrides vs available_models + param_allowed_values; failures drive synthetic-0."""
    failures: list[ValidationFailure] = []
    allowed_models = list(pipeline_schema.available_models)
    for node_name, node_params in node_overrides.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        node_allowed = (node.param_allowed_values if node else None) or {}
        for param, value in node_params.items():
            if param == "model" and allowed_models:
                if value is not None and value not in allowed_models:
                    failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.model",
                            value=str(value),
                            allowed=allowed_models,
                            reason="not_in_available_models",
                        )
                    )
            elif (allowed := node_allowed.get(param)) and value not in allowed:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=list(allowed),
                        reason="not_in_param_allowed_values",
                    )
                )
    return failures


def _check_l1_schema_compliance(
    source_output: Any,
    *,
    pipeline_schema: PipelineSchema,
    **_: Any,
) -> ValidatorOutcome | None:
    """L1-output validator wrapping :func:`validate_overrides`.

    Source output is the candidate's ``node_overrides`` dict (parse-time
    projection of L1's ``pipeline_params_override``). Produces a single
    ValidatorOutcome carrying every detected ``ValidationFailure`` as
    evidence. Nurse target: L2 — L2 reads the failures next round and
    writes a directive that names the disallowed value.
    """
    if not source_output or not pipeline_schema:
        return None
    failures = validate_overrides(source_output, pipeline_schema)
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_SCHEMA_COMPLIANCE.id,
        passed=False,
        score=0.0,
        evidence={"failures": failures},
        nurse_target="l2",
    )


L1_SCHEMA_COMPLIANCE: LLMOutputValidator = LLMOutputValidator(
    id="l1_schema_compliance",
    description=(
        "Verify L1's pipeline_params_override against the pipeline schema's "
        "available_models and param_allowed_values. Failures attach to the "
        "candidate as ValidationFailures and drive the synthetic-0 path."
    ),
    nurse_target="l2",
    check=_check_l1_schema_compliance,
)


async def l1_generate(
    cycle: Cycle,
    *,
    n_variants: int,
    creativity: float,
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
    obs_campaign_id = cycle.session.state.obs_campaign_id

    surface = compile_l1_surface(cycle, round_num=round_num, n_variants=n_variants)
    compile_vars = surface.to_compile_vars()

    template = load_optimizer_prompt("l1_generate")
    if opt_sp.l1_template_override:
        template = template.model_copy(update={"problem_description": opt_sp.l1_template_override})

    output_schema = build_l1_output_schema(pipeline_schema) if pipeline_schema else None
    generated, meta_prompt = await run_optimizer_node(
        template_name="l1_generate",
        compile_vars=compile_vars,
        llm_client=llm_client,
        model=model,
        temperature=creativity,
        json_schema=output_schema,
        recorder=cycle.session.state.round_recorder,
        template=template,
    )
    section_sizes = sorted(
        (
            (name, len(value.rstrip()))
            for name, value in compile_vars.items()
            if value and value.strip()
        ),
        key=lambda x: -x[1],
    )
    logger.info(
        "L1 R%d meta-prompt: %d chars | %s",
        round_num,
        len(meta_prompt),
        " | ".join(f"{n}={s}" for n, s in section_sizes[:6]),
    )

    variants_list = generated.get("variants", []) if isinstance(generated, dict) else generated

    population: list[CandidateProposal] = []
    for v in variants_list[:n_variants]:
        pp_override = v.get("pipeline_params_override") or {}
        pp_override.pop("steps", None)  # LLM must not override pipeline composition
        prompt_changes: dict = {}
        tc_changes: dict = {}
        node_overrides: dict = {}
        for k, pv in pp_override.items():
            kind = _classify_axis(k)
            if kind == "prompt_field":
                prompt_changes[k] = pv
            elif kind == "task_context":
                tc_changes[k] = pv
            else:
                node_overrides[k] = pv

        # Symmetric safety net: un-nest prompt / task_context fields the LLM
        # emitted under a node name (e.g. {"llm_only": {"answer_format": ...}}).
        # Without this, the prompt mutation never reaches the OSP and the diff
        # table mislabels the candidate as a clone of its parent.
        for node_name in list(node_overrides.keys()):
            nested = node_overrides[node_name]
            if not isinstance(nested, dict):
                continue
            for sub_k in list(nested.keys()):
                sub_kind = _classify_axis(sub_k)
                if sub_kind == "prompt_field":
                    logger.warning(
                        "l1_generate: un-nesting prompt field %r from node %r",
                        sub_k,
                        node_name,
                    )
                    prompt_changes[sub_k] = nested.pop(sub_k)
                elif sub_kind == "task_context":
                    logger.warning(
                        "l1_generate: un-nesting task_context field %r from node %r",
                        sub_k,
                        node_name,
                    )
                    tc_changes[sub_k] = nested.pop(sub_k)
            if not nested:
                del node_overrides[node_name]

        # Safety net: auto-nest flat params the LLM may still emit
        if pipeline_schema:
            _flat_keys = [k for k, v in node_overrides.items() if not isinstance(v, dict)]
            for fk in _flat_keys:
                owner = pipeline_schema.node_for_param(fk)
                if owner:
                    logger.warning("l1_generate: auto-nesting flat param %r → %s", fk, owner)
                    node_overrides.setdefault(owner, {})[fk] = node_overrides.pop(fk)

            # Filter out hallucinated nodes that don't exist in the pipeline
            _bad_nodes = [k for k in node_overrides if not pipeline_schema.has_node(k)]
            for bk in _bad_nodes:
                logger.warning("l1_generate: dropping hallucinated node %r", bk)
                del node_overrides[bk]

        # Override validation is deferred to parse_population — one producer of truth.
        child = opt_sp.mutate(
            changes_description=v.get("changes_description", ""),
            source="l1_generate",
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        population.append(CandidateProposal(osp=child, node_overrides=node_overrides))

        if obs:
            with graceful("CandidateCreated emit failed"):
                obs.emit_write_point(
                    CandidateCreated,
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=len(population) - 1,
                    candidate_id=child.lineage.id,
                )

    return population


# ---------------------------------------------------------------------------
# Section 2 — Measurement (post-process, parse, score)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class L1YieldStats:
    """Round-level L1 generation quality, computed from per-candidate signatures."""

    yield_: float  # n_valid / n_proposed (1.0 when no proposals)
    n_no_op: int
    n_duplicate: int


_INVARIANT_REASONS = frozenset({"no_op_variant", "duplicate_variant"})


def detect_invariants(
    proposals: list[CandidateProposal], parent_osp: OptSearchPoint
) -> L1YieldStats:
    """Attach ``ValidationFailure`` to no-op / duplicate variants; return round-level stats.

    A candidate is a *no-op* when its mutation signature vs ``parent_osp`` is
    empty (every prompt field, task-context entry, and node-override matches
    parent). A *duplicate* signature equals one already seen earlier in the
    batch. Failures attach to ``cp.osp.validation_failures`` so the existing
    synthetic-0 path (``score_population`` Path 1) skips scoring with zero
    LLM cost; L2 ingests them next round and the resulting ``l2_directive``
    teaches L1 to diversify.

    Idempotent: pre-existing ``no_op_variant`` / ``duplicate_variant``
    entries are dropped before the pass so resume-from-disk doesn't
    accumulate duplicates.
    """
    for cp in proposals:
        cp.osp.validation_failures = [
            vf for vf in cp.osp.validation_failures if vf.reason not in _INVARIANT_REASONS
        ]
    seen: dict[tuple, int] = {}
    n_no_op = 0
    n_duplicate = 0
    parent_tc = parent_osp.task_context.to_dict()
    for i, cp in enumerate(proposals):
        child = cp.osp
        pf_delta = tuple(
            (f, getattr(child, f))
            for f in PROMPT_STRING_FIELDS
            if getattr(child, f) != getattr(parent_osp, f)
        )
        child_tc = child.task_context.to_dict()
        tc_delta = tuple(sorted((k, v) for k, v in child_tc.items() if v != parent_tc.get(k)))
        no_canon = tuple(
            sorted((n, tuple(sorted(p.items()))) for n, p in (cp.node_overrides or {}).items() if p)
        )
        sig = (pf_delta, tc_delta, no_canon)
        if not any(sig):
            cp.osp.validation_failures = [
                *cp.osp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason="no_op_variant",
                ),
            ]
            n_no_op += 1
            continue
        if sig in seen:
            twin = seen[sig]
            cp.osp.validation_failures = [
                *cp.osp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{twin + 1}",
                    allowed=["unique mutation"],
                    reason="duplicate_variant",
                ),
            ]
            n_duplicate += 1
            continue
        seen[sig] = i
    n = len(proposals)
    yield_ = (n - n_no_op - n_duplicate) / n if n else 1.0
    return L1YieldStats(yield_=yield_, n_no_op=n_no_op, n_duplicate=n_duplicate)


def _merge_pipeline_params(
    base: dict | None,
    overrides: dict | None,
    schema: PipelineSchema | None,
) -> dict | None:
    """Deep-merge ``overrides`` into ``base``; drop overrides for nodes outside active steps."""
    if not overrides:
        return base
    merged: dict = copy.deepcopy(base or {})
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
    pipeline_params: dict | None,
    schema: PipelineSchema | None,
) -> tuple[list[OptSearchPoint], list[dict | None]]:
    """Project proposals → OptSearchPoints + merged pp; attaches validation failures."""
    osp_list: list[OptSearchPoint] = []
    merged: list[dict | None] = []
    for cp in proposals:
        override = cp.node_overrides or None
        osp = cp.osp
        if schema and override:
            outcome = L1_SCHEMA_COMPLIANCE.run(override, pipeline_schema=schema)
            if outcome is not None:
                failures: list[ValidationFailure] = outcome.evidence["failures"]
                osp.validation_failures = failures
                for vf in failures:
                    logger.warning(
                        "candidate %s: validation failure on %s — proposed %r not in allowed %r",
                        osp.lineage.id[:8],
                        vf.axis,
                        vf.value,
                        vf.allowed,
                    )
        osp_list.append(osp)
        merged.append(_merge_pipeline_params(pipeline_params, override, schema))
    return osp_list, merged


def _build_score_report(
    osp: OptSearchPoint,
    override: dict | None,
    scores: dict,
    results: list,
    dataset: list,
    *,
    aborted: bool = False,
    elimination_stopped: bool = False,
    elimination_context: dict | None = None,
    resumed_from_cache: bool = False,
    invalid: bool = False,
    new_runtime_failure: RuntimeFailure | None = None,
    l1_diversity: float = 1.0,
) -> CandidateScore:
    """Build the typed candidate score report — stable shape, defaults always present."""
    evaluators = {**(scores.get("evaluators") or {}), "l1_diversity": l1_diversity}
    return CandidateScore(
        candidate_id=osp.lineage.id,
        changes_description=osp.lineage.changes_description or "",
        pipeline_params_override=override,
        accuracy=scores["accuracy"],
        composite=scores.get("composite", scores["accuracy"]),
        hits=scores["hits"],
        total=scores["total"],
        evaluators=evaluators,
        escalation_aborted=aborted,
        elimination_stopped=elimination_stopped,
        scored_queries=len(results),
        expected_queries=len(dataset),
        invalid=invalid,
        resumed_from_cache=resumed_from_cache,
        validation_failures=[vf.to_dict() for vf in osp.validation_failures],
        runtime_failures=[new_runtime_failure.to_dict()] if new_runtime_failure else [],
        elimination_context=dict(elimination_context) if elimination_context else {},
    )


_INVALID_SCORES: dict[str, Any] = {
    "accuracy": 0.0,
    "composite": 0.0,
    "hits": 0,
    "total": 0,
    "errors": 0,
    "invalid": True,
}


def _handle_scored_candidate(
    osp_c: OptSearchPoint,
    override: dict | None,
    results: list[QueryResult],
    scores: dict,
    signal: EscalationSignal | None,
    merged_pp_i: dict | None,
    dataset: list,
    elim_check: Any,
    round_num: int,
    *,
    l1_diversity: float = 1.0,
) -> tuple[CandidateScore, EscalationSignal | None]:
    """Build report for a scored candidate; attach RuntimeFailure on elimination."""
    elimination_stopped = signal is not None and signal.is_elimination
    leader_locked = signal is not None and signal.is_leader_lock
    scoring_error_abort = signal is not None and signal.check_name == "scoring_error_abort"
    aborted = (
        bool(signal) and not leader_locked and (scoring_error_abort or len(results) < len(dataset))
    )

    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not aborted:
        elim_check.register_completed(
            [r.get("score", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )

    new_rf: RuntimeFailure | None = None
    rf_kind = (
        "degradation_check"
        if elimination_stopped and signal is not None and signal.check_name == "degradation"
        else "scoring_error_abort"
        if scoring_error_abort
        else None
    )
    if rf_kind and signal is not None:
        cr = signal.check_result
        dc = int(cr.get("degraded_count", 0))
        te = int(cr.get("total_scored", len(results)))
        if rf_kind == "degradation_check":
            dominant = cr.get("dominant_warning", "unknown:unknown")
            node_cfg = (merged_pp_i or {}).get(dominant.split(":", 1)[0], {})
            rate = float(cr.get("degraded_rate", 0.0))
        else:
            dominant = str(cr.get("dominant_warning") or "scoring_error")
            node_cfg = merged_pp_i or {}
            rate = (dc / te) if te else 0.0
        new_rf = RuntimeFailure(
            source=rf_kind,
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=rate,
            degraded_count=dc,
            total_scored=te,
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=osp_c.lineage.changes_description or "",
        )
        osp_c.runtime_failures = [*osp_c.runtime_failures, new_rf]

    elim_ctx: dict | None = None
    if (
        (elimination_stopped or leader_locked)
        and signal is not None
        and signal.check_name == "elimination"
    ):
        cr = signal.check_result
        elim_ctx = {
            "p_best": float(cr.get("p_best", 0.0)),
            "epsilon": float(cr.get("epsilon", 0.05)),
            "leader_id": str(cr.get("leader_id", "")),
            "queries_scored": int(cr.get("queries_scored", len(results))),
            "total_queries": int(cr.get("total_queries", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
            "leader_locked": leader_locked,
        }

    report = _build_score_report(
        osp_c,
        override,
        scores,
        results,
        dataset,
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        elimination_context=elim_ctx,
        new_runtime_failure=new_rf,
        l1_diversity=l1_diversity,
    )
    residual = None if (elimination_stopped or leader_locked or not signal) else signal
    return report, residual


def _record_elimination_cut(
    signal: EscalationSignal,
    osp_c: OptSearchPoint,
    elim_check: Any,
    priors_at_test: list[str],
    candidate_scores: list[CandidateScore],
    report: CandidateScore,
    decisions: list[Decision] | None,
    round_num: int,
    n_results: int,
) -> None:
    """Decorate report + append elimination_cut decision for divergence replay."""
    cr = signal.check_result
    leader_id = str(cr.get("leader_id", ""))
    if leader_id and leader_id in priors_at_test:
        prior_label = next(
            (f"C{i + 1}" for i, r in enumerate(candidate_scores) if r.candidate_id == leader_id),
            None,
        )
        if prior_label and report.elimination_context:
            report.elimination_context["leader_label"] = prior_label

    if decisions is not None:
        record_decision(
            decisions,
            DecisionKind.ELIMINATION_CUT,
            {
                "candidate_id": osp_c.lineage.id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": int(cr.get("queries_scored", n_results)),
                "epsilon": float(elim_check.epsilon),
                "n_min": int(elim_check.n_min),
                "round_num": round_num,
            },
            True,
            data={
                "p_best": float(cr.get("p_best", 0.0)),
                "leader_id": leader_id,
                "p_best_snapshot": dict(cr.get("p_best_snapshot") or {}),
            },
        )


def _record_leader_lock_in(
    signal: EscalationSignal,
    osp_c: OptSearchPoint,
    elim_check: Any,
    priors_at_test: list[str],
    decisions: list[Decision] | None,
    round_num: int,
    n_results: int,
) -> None:
    """Append leader_lock_in decision for divergence replay."""
    if decisions is None:
        return
    cr = signal.check_result
    record_decision(
        decisions,
        DecisionKind.LEADER_LOCK_IN,
        {
            "candidate_id": osp_c.lineage.id,
            "prior_candidate_ids": priors_at_test,
            "queries_scored": int(cr.get("queries_scored", n_results)),
            "lock_in": float(elim_check.lock_in),
            "lock_in_n_min": int(elim_check.lock_in_n_min),
            "round_num": round_num,
        },
        True,
        data={
            "p_best": float(cr.get("p_best", 0.0)),
            "leader_id": str(cr.get("leader_id", "")),
            "p_best_snapshot": dict(cr.get("p_best_snapshot") or {}),
        },
    )


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    merged_pp: list[dict | None],
    proposals: list[CandidateProposal],
    dataset: list,
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunListener,
    elimination_n_min: int,
    pobb_epsilon: float,
    pobb_lock_in: float = 0.95,
    pobb_lock_in_n_min: int = 8,
    pobb_mc_samples: int = 1000,
    round_num: int = 0,
    decisions: list[Decision] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryResult]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored)."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)
    _ = pobb_mc_samples  # reserved for future plumbing into posterior_best_probabilities

    all_candidate_results: dict[str, list[QueryResult]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None
    elim_check = PoBBCheck(
        n_min=elimination_n_min,
        epsilon=pobb_epsilon,
        lock_in=pobb_lock_in,
        lock_in_n_min=pobb_lock_in_n_min,
        n_queries=len(dataset),
    )

    def _fire(idx: int, report: CandidateScore) -> None:
        candidate_scores.append(report)
        callbacks.on_candidate_scored(idx, n, report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=report.to_dict(),
                )

    for idx, osp_c in enumerate(population):
        override = proposals[idx].node_overrides or None
        callbacks.on_candidate_started(idx, n, osp_c.lineage.changes_description or "", override)
        # Bind the PoBBCheck to this candidate so its per-query snapshot
        # lands on the live telemetry stream tagged with the right id.
        _candidate_idx = idx

        def _emit_p_best(snap, _ci=_candidate_idx) -> None:
            callbacks.on_p_best_update(round_num, _ci, n, snap)

        elim_check.set_current(osp_c.lineage.id, on_snapshot=_emit_p_best)

        # Path 1 — validation-skip synthetic-0.
        if osp_c.validation_failures:
            all_candidate_results[osp_c.lineage.id] = []
            _fire(
                idx,
                _build_score_report(
                    osp_c,
                    override,
                    _INVALID_SCORES,
                    [],
                    dataset,
                    invalid=True,
                    l1_diversity=l1_diversity,
                ),
            )
            continue

        def _on_result(r, qi, qt, _ci=idx):
            callbacks.on_sample_scored(_ci, n, qi, qt, r)

        def _on_start(qtxt, qi, qt, _ci=idx):
            callbacks.on_sample_started(_ci, n, qi, qt, qtxt)

        results, scores, was_cached, signal = await score_search_point(
            osp_c.to_job_search_point(
                base_pipeline_params=merged_pp[idx], schema=session.pipeline_schema
            ),
            dataset,
            session,
            label=f"candidate_{idx}",
            on_result=_on_result,
            on_start=_on_start,
            degradation_checks=[*(degradation_checks or []), elim_check],
            candidate_idx=idx,
            n_total_candidates=n,
            axes=cycle.axes,
            l1_diversity=l1_diversity,
        )
        all_candidate_results[osp_c.lineage.id] = results

        # Path 2 — full-run cache replay.
        if was_cached:
            elim_check.register_completed(
                [r.get("score", 0.0) for r in results], candidate_id=osp_c.lineage.id
            )
            _fire(
                idx,
                _build_score_report(
                    osp_c,
                    override,
                    scores,
                    results,
                    dataset,
                    resumed_from_cache=True,
                    l1_diversity=l1_diversity,
                ),
            )
            continue

        # Path 3 — scored. Snapshot priors BEFORE helper registers this candidate.
        priors_at_test = list(elim_check.prior_ids)
        report, residual = _handle_scored_candidate(
            osp_c,
            override,
            results,
            scores,
            signal,
            merged_pp[idx],
            dataset,
            elim_check,
            round_num,
            l1_diversity=l1_diversity,
        )
        if signal is not None and signal.is_elimination and signal.check_name == elim_check.name:
            _record_elimination_cut(
                signal,
                osp_c,
                elim_check,
                priors_at_test,
                candidate_scores,
                report,
                decisions,
                round_num,
                len(results),
            )
        leader_locked = (
            signal is not None and signal.is_leader_lock and signal.check_name == elim_check.name
        )
        if leader_locked and signal is not None:
            _record_leader_lock_in(
                signal,
                osp_c,
                elim_check,
                priors_at_test,
                decisions,
                round_num,
                len(results),
            )
        _fire(idx, report)

        if leader_locked:
            break  # round leader confirmed — skip remaining candidates
        if residual is not None:
            escalation_signal = residual
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


# ---------------------------------------------------------------------------
# Section 3 — Scoring orchestration (winner selection)
# ---------------------------------------------------------------------------


class L1ScoringResult(BaseModel):
    """Structured return value from l1_score() — frozen."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    label: str
    winner_osp: OptSearchPoint
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None
    winner_accuracy: float
    winner_composite: float
    hits: int
    total: int
    improved: bool
    p_value: float | None = None
    candidates_scored: int
    candidate_scores: list[CandidateScore]
    winner_results: list[QueryResult]
    all_candidate_results: dict[str, list[QueryResult]] = Field(default_factory=dict)
    escalation_signal: EscalationSignal | None = None
    degraded_queries: int = 0
    deprecated: int = 0
    winner_evaluators: dict[str, float] = Field(default_factory=dict)
    decisions: list[Decision] = Field(default_factory=list)
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list,
    baseline: RoundBaseline,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunListener,
    degradation_checks: list[StopRule] | None = None,
    elimination_n_min: int,
    pobb_epsilon: float,
    pobb_lock_in: float = 0.95,
    pobb_lock_in_n_min: int = 8,
    pobb_mc_samples: int = 1000,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> L1ScoringResult:
    """Score candidates and select the round winner (compares fitness, not composite)."""
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, merged_pp = parse_population(candidates, pipeline_params, schema)
    decisions: list[Decision] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        candidates,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        elimination_n_min=elimination_n_min,
        pobb_epsilon=pobb_epsilon,
        pobb_lock_in=pobb_lock_in,
        pobb_lock_in_n_min=pobb_lock_in_n_min,
        pobb_mc_samples=pobb_mc_samples,
        round_num=round_num,
        decisions=decisions,
        l1_diversity=yield_stats.yield_,
    )

    aborted_ids = {
        cs.candidate_id
        for cs in candidate_scores
        if cs.escalation_aborted and not cs.elimination_stopped
    }
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    best_acc = baseline.accuracy
    best_comp = baseline.composite
    best_osp: OptSearchPoint = baseline.osp
    best_results: list = list(baseline.results)
    best_label = baseline.label
    best_scores: dict[str, float] = dict(baseline.evaluators)
    winner_idx: int | None = None
    for idx, ind in enumerate(scored):
        s = compute_composite_score(
            all_candidate_results[ind.lineage.id],
            schema,
            opt_sp=ind,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=yield_stats.yield_,
        )
        if s["accuracy"] > best_acc:
            best_acc = s["accuracy"]
            best_comp = s["composite"]
            best_osp = ind
            best_results = list(all_candidate_results[ind.lineage.id])
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(s.get("evaluators") or {})
            winner_idx = idx

    record_decision(
        decisions,
        DecisionKind.ROUND_WINNER,
        {
            "candidate_ids": [ind.lineage.id for ind in scored],
            "round_num": round_num,
        },
        scored[winner_idx].lineage.id if winner_idx is not None and scored else "",
        data={"current_best_accuracy_at_record": baseline.accuracy},
    )

    base = _compute_accuracy(best_results)
    improved = best_acc > baseline.accuracy + improvement_threshold
    p_value: float | None = None
    if improved and base["total"] > 0:
        from promptpotter.shared.statistics import proportion_test

        bl_hits = round(baseline.accuracy * base["total"])
        p_value = proportion_test(base["hits"], base["total"], bl_hits, base["total"])
    return L1ScoringResult(
        label=best_label,
        winner_osp=best_osp,
        winner_prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        winner_pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        winner_accuracy=best_acc,
        winner_composite=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=improved,
        p_value=p_value,
        candidates_scored=len(scored),
        candidate_scores=candidate_scores,
        winner_results=best_results,
        all_candidate_results=all_candidate_results,
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(best_results),
        deprecated=base["deprecated"],
        winner_evaluators=best_scores,
        decisions=decisions,
        l1_yield=yield_stats.yield_,
        l1_n_no_op=yield_stats.n_no_op,
        l1_n_duplicate=yield_stats.n_duplicate,
    )


# ---------------------------------------------------------------------------
# Section 4 — Round execution (top-level L1 round driver)
# ---------------------------------------------------------------------------


async def _generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> tuple[list[CandidateProposal], L1YieldStats]:
    """Load persisted candidates or generate fresh ones via LLM; detect no-op + duplicate variants."""
    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up the round budget.
    opt = config.optimization
    model = config.optimizer_llm.model
    opt_params = cycle.opt_sp.optimizer_params
    _n_variants = min(opt_params.get("n_variants", opt.n_variants), opt.n_variants * 3)
    _creativity = opt_params.get("creativity", opt.creativity)
    prompt_preview = cycle.opt_sp.render()[:120]

    assert cycle.current_sp is not None
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=cycle.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=model or "(default)",
        has_l1_critique=bool(cycle.opt_sp.l1_critique_text),
        pipeline_params=cycle.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
    )

    if session.state.cycle_id:
        persisted_raw = session.store.campaigns.load_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
        )
        if persisted_raw is not None:
            persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
            logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
            yield_stats = detect_invariants(persisted, cycle.opt_sp)
            # llm_call never fires on this branch — synthesize l1_generate
            # so dashboard.json + round_NNNN.json don't miss the node.
            if _rr := session.state.round_recorder:
                _rr.set_node(
                    "l1_generate",
                    {
                        "input": {"source": "loaded_from_disk", "round": round_num},
                        "output": {"candidates": candidate_summaries(persisted)},
                    },
                )
            emit_phase(
                on_phase,
                CampaignPhase.L1_GENERATE,
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted),
                l1_yield=yield_stats.yield_,
                l1_n_no_op=yield_stats.n_no_op,
                l1_n_duplicate=yield_stats.n_duplicate,
            )
            return persisted, yield_stats

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=session.state.obs_campaign_id,
        round_num=round_num,
    ):
        candidates = await l1_generate(
            cycle,
            n_variants=_n_variants,
            creativity=_creativity,
            llm_client=client,
            model=model,
            obs=obs,
            round_num=round_num,
        )

    yield_stats = detect_invariants(candidates, cycle.opt_sp)

    if session.state.cycle_id:
        session.store.campaigns.save_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
            [cp.model_dump() for cp in candidates],
        )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates),
        l1_yield=yield_stats.yield_,
        l1_n_no_op=yield_stats.n_no_op,
        l1_n_duplicate=yield_stats.n_duplicate,
    )

    return candidates, yield_stats


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_dataset: list[Sample],
    callbacks: RunListener,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique → persist to memory.

    ``skip_critique=True`` skips the round-end ``run_l1_critique`` LLM call.
    Sweep mode passes this so the cheap-trial fork stays one full live LLM
    call (round 2 generate-only); the round-1 critique would otherwise dwarf
    that call and is the same across all forks since round 1 is identical.
    """
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.obs_campaign_id, round_num=round_num))

    candidates, yield_stats = await _generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_eval_queries=len(scoring_dataset), obs=obs
    )

    assert cycle.current_sp is not None
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(scoring_dataset),
        current_best_accuracy=cycle.current_accuracy,
        improvement_threshold=opt.improvement_threshold,
        current_pipeline_params=cycle.current_sp.pipeline_params,
    )
    baseline = cycle.baseline_for_round(scoring_dataset, round_num)
    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        obs_type="span",
        campaign_id=session.state.obs_campaign_id,
        round_num=round_num,
    ):
        scoring_result = await l1_score(
            cycle,
            candidates,
            scoring_dataset,
            baseline,
            pipeline_params=cycle.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            elimination_n_min=opt.elimination_n_min,
            pobb_epsilon=opt.pobb_epsilon,
            pobb_lock_in=opt.pobb_lock_in,
            pobb_lock_in_n_min=opt.pobb_lock_in_n_min,
            pobb_mc_samples=opt.pobb_mc_samples,
            round_num=round_num,
            yield_stats=yield_stats,
        )
        if obs and scoring_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=str(scoring_result.winner_prompt_fields.get("id") or ""),
                    winner_accuracy=scoring_result.winner_accuracy,
                    improved=scoring_result.improved,
                )
    candidate_scores_dicts = [cs.to_dict() for cs in scoring_result.candidate_scores]
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=scoring_result.label,
        winner_accuracy=scoring_result.winner_accuracy,
        winner_composite=scoring_result.winner_composite,
        winner_evaluators=dict(scoring_result.winner_evaluators),
        improved=scoring_result.improved,
        candidate_scores=candidate_scores_dicts,
    )

    critique_text = ""
    if scoring_result.winner_results and not skip_critique:
        crit_llm = _llm_client.get_llm_client(config.optimizer_llm.provider)
        # Wrap the critique LLM call: a truncated/malformed response (e.g.
        # finish_reason=length producing invalid JSON) raised JSONDecodeError
        # all the way to asyncio.run and crashed the whole campaign.
        # Critique is round-over-round feedback — surviving its loss costs at
        # most one round of degraded L1-generate context, far better than a
        # hard exit.
        with graceful("L1 critique failed; continuing without round-over-round feedback"):
            critique_result = await run_l1_critique(
                cycle,
                scoring_result,
                session.pipeline_schema,
                crit_llm,
                round_num=round_num,
                model=config.optimizer_llm.model,
                recorder=session.state.round_recorder,
            )
            critique_text = format_l1_critique_for_prompt(critique_result)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.state.obs_campaign_id,
                round_num=round_num,
                l1_critique_text=critique_text,
            )

    cycle.apply_round_outcome(scoring_result, critique_text)

    round_result = RoundResult(
        round=round_num,
        label=scoring_result.label,
        accuracy=scoring_result.winner_accuracy,
        composite=scoring_result.winner_composite,
        hits=scoring_result.hits,
        total=scoring_result.total,
        improved=scoring_result.improved,
        p_value=scoring_result.p_value,
        baseline_accuracy=baseline.accuracy,
        prompt_fields=scoring_result.winner_prompt_fields,
        pipeline_params=scoring_result.winner_pipeline_params,
        results=scoring_result.winner_results,
        all_candidate_results=dict(scoring_result.all_candidate_results),
        candidates_scored=scoring_result.candidates_scored,
        candidate_scores=candidate_scores_dicts,
        decisions=[d.to_dict() for d in scoring_result.decisions],
        degraded_queries=scoring_result.degraded_queries,
        deprecated=scoring_result.deprecated,
        escalation_signal=scoring_result.escalation_signal,
        evaluators=scoring_result.winner_evaluators,
        l1_yield=scoring_result.l1_yield,
        l1_n_no_op=scoring_result.l1_n_no_op,
        l1_n_duplicate=scoring_result.l1_n_duplicate,
    )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    accuracy=scoring_result.winner_accuracy,
                    hits=scoring_result.hits,
                    total=scoring_result.total,
                    improved=scoring_result.improved,
                    winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                    candidate_scores=candidate_scores_dicts,
                    model=config.optimizer_llm.model or "",
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(scoring_result.winner_evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            w_osp = scoring_result.winner_osp
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=w_osp.lineage.id,
                    rendered_prompt=w_osp.render(),
                    layer1_fields={f: getattr(w_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=w_osp.lineage.parent_id,
                )
            )

    return round_result
