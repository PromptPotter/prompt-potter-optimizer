"""L1-generate input/output validation + variant invariant detection.

Three concerns, all validation-shaped:

- **Schema construction**: ``build_l1_output_schema`` grafts per-node
  ``param_allowed_values`` into the static l1_generate envelope and
  constrains the prompt-field + task-context slots so the LLM is
  constrained at output-time across all three override slots.
- **Schema compliance**: ``validate_overrides`` + ``L1_SCHEMA_COMPLIANCE``
  catch ``pipeline_params_override`` values that violate the schema
  after the fact (LLMs sometimes ignore parts of the schema). Failures
  route to L2 via ``ValidatorOutcome``.
- **Variant invariants**: ``detect_invariants`` flags ``no_op_variant``
  (no mutation vs parent) and ``duplicate_variant`` (sig-equal across
  population). Returns ``L1YieldStats`` for the round.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.application.optimization.dispatch.schemas import L1GenerateOutput
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "L1_CONFIG_NOT_IN_RUNTIME_FAILURES",
    "L1_SCHEMA_COMPLIANCE",
    "L1YieldStats",
    "build_l1_output_schema",
    "detect_invariants",
    "filter_pipeline_params_override",
    "validate_overrides",
]


def _inline_refs(node: Any, defs: dict[str, dict[str, Any]]) -> Any:
    """Resolve ``$ref`` references in a Pydantic-emitted JSON Schema in place.

    Pydantic's ``model_json_schema()`` factors nested models into a top-level
    ``$defs`` table referenced via ``$ref``. Provider response_format wants a
    self-contained schema (no $ref forward declarations on subtrees); we
    inline so callers can mutate properties directly, and so the wire
    payload validates server-side without resolution. ``title`` metadata is
    stripped along the way — Pydantic auto-emits them, they bloat the
    schema for no LLM-side benefit.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            key = node["$ref"].split("/")[-1]
            return _inline_refs(copy.deepcopy(defs[key]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def build_l1_output_schema(pipeline_schema: PipelineSchema) -> dict[str, Any]:
    """l1_generate response_schema — three constrained slots per variant.

    The base shape comes from :class:`L1GenerateOutput` (the Pydantic SoT
    for l1_generate's response); we then constrain each of the three
    override slots so the LLM cannot conflate them:

    - ``pipeline_params_override``: per backend node we enrich with the
      node's allowed-value enums so the LLM is constrained at
      output-time:

      * if ``param_allowed_values`` carries an enum → ``{"type": "string", "enum": [...]}``
      * else if ``param_types`` declares a type → ``{"type": <json-type>}``
      * else → ``{}`` (unconstrained — dataset overlay gap)

      The type emission is load-bearing: without it, the L1 LLM is free
      to emit ``"0.2"`` for ``temperature`` (string instead of number),
      which propagates through the wire payload to the upstream provider
      and triggers a 4xx that PromptPotter has to skip.

    - ``prompt_fields_override``: properties enumerated as the six
      :data:`PROMPT_STRING_FIELDS`, each ``{"type": "string"}``;
      ``additionalProperties: false``.

    - ``task_context_override``: properties enumerated as
      :data:`TASK_CONTEXT_OVERRIDES`, each ``{"type": "string"}``;
      ``additionalProperties: false``.

    Returns the ``{"name", "schema", "strict"}`` envelope chat() consumes
    via ``response_schema``. ``strict`` stays False — flipping it on is a
    follow-up after multi-provider testing.
    """
    raw_schema = L1GenerateOutput.model_json_schema()
    defs = raw_schema.pop("$defs", {})
    inlined = _inline_refs(raw_schema, defs)

    variant_props = inlined["properties"]["variants"]["items"]["properties"]

    # 1. pipeline_params_override — per-node tunables.
    pp_override = variant_props["pipeline_params_override"]
    pp_override.setdefault("properties", {})
    pp_override["additionalProperties"] = False
    pp_properties = pp_override["properties"]

    for node in pipeline_schema.nodes:
        if not node.param_keys:
            continue
        param_props: dict[str, dict[str, Any]] = {}
        for param in sorted(node.param_keys):
            # Operator-locked axes (model, provider) live in the dataset
            # overlay and are off L1's surface. Keeping them out of the
            # output schema closes the loop the validator otherwise has
            # to reject every round — the LLM cannot emit a key the
            # structured-output schema does not declare.
            if param in PARAM_FORBIDDEN_KEYS:
                continue
            allowed = node.param_allowed_values.get(param)
            declared_type = node.param_types.get(param)
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            elif declared_type:
                param_props[param] = {"type": declared_type}
            else:
                param_props[param] = {}
        if not param_props:
            continue
        pp_properties[node.name] = {
            "type": "object",
            "properties": param_props,
            "additionalProperties": False,
        }

    # 2. prompt_fields_override — top-level prompt-template fields.
    pf_override = variant_props["prompt_fields_override"]
    pf_override["properties"] = {field: {"type": "string"} for field in PROMPT_STRING_FIELDS}
    pf_override["additionalProperties"] = False

    # 3. task_context_override — pipeline-context strings.
    tc_override = variant_props["task_context_override"]
    tc_override["properties"] = {
        field: {"type": "string"} for field in sorted(TASK_CONTEXT_OVERRIDES)
    }
    tc_override["additionalProperties"] = False

    return {"name": "l1_variants", "schema": inlined, "strict": False}


_JSON_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def _matches_declared_type(value: Any, declared: str) -> bool:
    """JSON-Schema-flavoured isinstance: booleans are not numbers."""
    py_types = _JSON_TYPE_TO_PY.get(declared)
    if py_types is None:
        return True  # unknown declared type → no check (forward-compat)
    # JSON Schema: booleans are NOT integers/numbers, even though Python
    # says `isinstance(True, int)`. Treat bool as exclusive to "boolean".
    if isinstance(value, bool):
        return declared == "boolean"
    return isinstance(value, py_types)


def validate_overrides(
    pipeline_params_override: dict[str, dict[str, Any]],
    pipeline_schema: PipelineSchema,
    *,
    forbidden_axes_strict: bool = True,
) -> list[ValidationFailure]:
    """Validate overrides vs available_models + param_allowed_values + param_types; failures drive synthetic-0.

    When ``forbidden_axes_strict`` is on, any touch of
    ``PARAM_FORBIDDEN_KEYS`` (``model``, ``provider``) is rejected outright,
    independent of whether the proposed value would be in ``available_models``
    — these axes are operator-fixed at the dataset overlay and not on L1's
    surface at all. Off-by-flag enables ablation runs that intentionally
    sweep model identity.

    Type mismatch (``"0.2"`` proposed for a ``number``-declared param) is
    rejected with ``reason="type_mismatch"``. This catches the case the
    JSON-schema ``type`` constraint in :func:`build_l1_output_schema` is
    meant to prevent — both layers run because not every provider/SDK
    enforces structured-output schemas with full fidelity.
    """
    failures: list[ValidationFailure] = []
    allowed_models = list(pipeline_schema.available_models)
    for node_name, node_params in pipeline_params_override.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        node_allowed = (node.param_allowed_values if node else None) or {}
        node_types = (node.param_types if node else None) or {}
        for param, value in node_params.items():
            if forbidden_axes_strict and param in PARAM_FORBIDDEN_KEYS:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=[],
                        reason="forbidden_axis",
                    )
                )
                continue
            declared_type = node_types.get(param)
            if (
                declared_type
                and value is not None
                and not _matches_declared_type(value, declared_type)
            ):
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=f"{value!r} ({type(value).__name__})",
                        allowed=[declared_type],
                        reason="type_mismatch",
                    )
                )
                continue
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
    forbidden_axes_strict: bool = True,
    **_: Any,
) -> ValidatorOutcome | None:
    """Wrap validate_overrides → ValidatorOutcome (nurse_target=L2)."""
    if not source_output or not pipeline_schema:
        return None
    failures = validate_overrides(
        source_output, pipeline_schema, forbidden_axes_strict=forbidden_axes_strict
    )
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
    description="Verify L1's pipeline_params_override vs schema's allowed values.",
    nurse_target="l2",
    check=_check_l1_schema_compliance,
)


def _check_l1_config_in_runtime_failures(
    source_output: Any,
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reject candidates that re-propose a config already proven to fail.

    Pure wire-level check — for each (param, value) in the candidate's
    ``pipeline_params_override``, scan ``opt_sp.wounds.runtime_failures``
    for an entry whose ``observed_config`` carries that same (param, value).
    If matched, emit ``ValidationFailure(reason="reproposes_known_failing_config")``.
    No LLM evidence judgment — just "we already proved this fails."

    Sibling-fork inheritance (``Cycle.start`` → ``gather_sibling_runtime_failures``)
    populates ``runtime_failures`` from prior cycles' terminal wounds, so
    this check fires even on round 1 of a fresh fork when sibling evidence
    exists.
    """
    if not source_output or opt_sp is None:
        return None
    failures_list = list(opt_sp.wounds.runtime_failures)
    if not failures_list:
        return None
    out_failures: list[ValidationFailure] = []
    for node_name, node_params in source_output.items():
        if not isinstance(node_params, dict):
            continue
        for param, value in node_params.items():
            for rf in failures_list:
                obs_cfg = rf.observed_config or {}
                if param in obs_cfg and obs_cfg[param] == value:
                    out_failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.{param}",
                            value=str(value),
                            allowed=[],
                            reason="reproposes_known_failing_config",
                        )
                    )
                    break
    if not out_failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_CONFIG_NOT_IN_RUNTIME_FAILURES.id,
        passed=False,
        score=0.0,
        evidence={"failures": out_failures},
        nurse_target="l2",
    )


L1_CONFIG_NOT_IN_RUNTIME_FAILURES: LLMOutputValidator = LLMOutputValidator(
    id="l1_config_not_in_runtime_failures",
    description=(
        "Reject L1 candidates whose pipeline_params_override matches an "
        "(axis, value) tuple already recorded as failing in "
        "opt_sp.wounds.runtime_failures. Mechanical wire-level check; no "
        "LLM citation required. Heals via Wound 1 (L2 absorbs)."
    ),
    nurse_target="l2",
    check=_check_l1_config_in_runtime_failures,
)


def filter_pipeline_params_override(
    pipeline_params_override: dict[str, dict[str, Any]],
    pipeline_schema: PipelineSchema | None,
) -> dict[str, dict[str, Any]]:
    """Drop entries for node names not in the active pipeline schema.

    The JSON schema built by :func:`build_l1_output_schema` already
    constrains node-name keys to the active schema's nodes, so this is
    belt-and-braces — provider strict mode is off by default and a
    weakly-conformant LLM can still emit a hallucinated node. Without
    this filter the variant lands at the parse stage as a no-op (the
    hallucinated node isn't a real wire target).
    """
    if not pipeline_schema:
        return dict(pipeline_params_override)
    filtered: dict[str, dict[str, Any]] = {}
    for node_name, params in pipeline_params_override.items():
        if pipeline_schema.has_node(node_name):
            filtered[node_name] = params
        else:
            logger.warning("l1_generate: dropping hallucinated node %r", node_name)
    return filtered


@dataclass(frozen=True)
class L1YieldStats:
    """Round-level L1 generation quality.

    Field names mirror ``RoundDiagnostics.l1_yield``/``l1_n_no_op``/``l1_n_duplicate``
    so callers can spread via ``dataclasses.asdict`` rather than translate.
    """

    l1_yield: float  # n_valid / n_proposed (1.0 when no proposals)
    l1_n_no_op: int
    l1_n_duplicate: int


_INVARIANT_REASONS = frozenset({"no_op_variant", "duplicate_variant"})


def detect_invariants(
    proposals: list[CandidateProposal], parent_osp: OptSearchPoint
) -> L1YieldStats:
    """Attach no_op_variant / duplicate_variant failures; return yield stats.

    Failures route through score_population's synthetic-0 path (Path 1) so
    invariant variants don't burn LLM calls. Idempotent — pre-existing
    invariant failures are dropped first so resume-from-disk doesn't dup.
    """
    for cp in proposals:
        cp.osp.wounds.validation_failures = [
            vf for vf in cp.osp.wounds.validation_failures if vf.reason not in _INVARIANT_REASONS
        ]
    seen: dict[tuple[Any, ...], int] = {}
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
            sorted(
                (n, tuple(sorted(p.items())))
                for n, p in (cp.pipeline_params_override or {}).items()
                if p
            )
        )
        sig = (pf_delta, tc_delta, no_canon)
        if not any(sig):
            cp.osp.wounds.validation_failures = [
                *cp.osp.wounds.validation_failures,
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
            cp.osp.wounds.validation_failures = [
                *cp.osp.wounds.validation_failures,
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
    return L1YieldStats(l1_yield=yield_, l1_n_no_op=n_no_op, l1_n_duplicate=n_duplicate)
