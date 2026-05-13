"""L1-generate input/output validation + variant invariant detection.

Three concerns, all validation-shaped:

- **Schema construction**: ``build_l1_output_schema`` grafts per-node
  ``param_allowed_values`` into the static l1_generate envelope so the
  LLM is constrained at output-time.
- **Schema compliance**: ``validate_overrides`` + ``L1_SCHEMA_COMPLIANCE``
  catch overrides that violate the schema after the fact (LLMs ignore
  parts of the schema). Failures route to L2 via ``ValidatorOutcome``.
- **Variant invariants**: ``detect_invariants`` flags ``no_op_variant``
  (no mutation vs parent) and ``duplicate_variant`` (sig-equal across
  population). Returns ``L1YieldStats`` for the round.

``_normalize_pp_override`` lives here because the cleanup it performs
(un-nest prompt fields, drop hallucinated nodes) is a pre-validation
step on the same payload.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.application.optimization.l1_behavior_checks import PARAM_FORBIDDEN_KEYS
from promptpotter.application.optimization.optimizer_schemas import L1GenerateOutput
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.analysis import ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "L1_SCHEMA_COMPLIANCE",
    "L1YieldStats",
    "build_l1_output_schema",
    "detect_invariants",
    "validate_overrides",
]


def _inline_refs(node: Any, defs: dict[str, dict]) -> Any:
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


def build_l1_output_schema(pipeline_schema: PipelineSchema) -> dict:
    """l1_generate response_schema with per-node ``param_allowed_values`` grafted in.

    The base shape comes from :class:`L1GenerateOutput` (the Pydantic SoT
    for l1_generate's response); per backend node we then enrich
    ``pipeline_params_override`` with the node's allowed-value enums so
    the LLM is constrained at output-time:

    - if ``param_allowed_values`` carries an enum → ``{"type": "string", "enum": [...]}``
    - else if ``param_types`` declares a type → ``{"type": <json-type>}``
    - else → ``{}`` (unconstrained — dataset overlay gap)

    The type emission is load-bearing: without it, the L1 LLM is free to
    emit ``"0.2"`` for ``temperature`` (string instead of number), which
    propagates through the wire payload to the upstream provider and
    triggers a 4xx that PromptPotter has to skip.

    Returns the ``{"name", "schema", "strict"}`` envelope chat() consumes
    via ``response_schema``.
    """
    raw_schema = L1GenerateOutput.model_json_schema()
    defs = raw_schema.pop("$defs", {})
    inlined = _inline_refs(raw_schema, defs)

    override = inlined["properties"]["variants"]["items"]["properties"]["pipeline_params_override"]
    override.setdefault("properties", {})
    override["additionalProperties"] = False
    override_properties = override["properties"]

    for node in pipeline_schema.nodes:
        if not node.param_keys:
            continue
        param_props: dict[str, dict] = {}
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
        override_properties[node.name] = {
            "type": "object",
            "properties": param_props,
            "additionalProperties": False,
        }

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
    pipeline_params_override: dict[str, dict],
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


def _normalize_pp_override(
    pp_override: dict, pipeline_schema: PipelineSchema | None
) -> tuple[dict, dict, dict]:
    """Split LLM pp_override → (prompt, task_context, pipeline_params_override); un-nest, auto-nest, drop unknown nodes."""
    pp_override.pop("steps", None)  # LLM must not override pipeline composition
    prompt_changes: dict = {}
    tc_changes: dict = {}
    pipeline_params_override: dict = {}
    for k, pv in pp_override.items():
        if k in PROMPT_STRING_FIELDS:
            prompt_changes[k] = pv
        elif k in TASK_CONTEXT_OVERRIDES:
            tc_changes[k] = pv
        else:
            pipeline_params_override[k] = pv

    # Un-nest prompt/task_context fields LLM emitted under a node name
    # (e.g. {"llm_only": {"answer_format": ...}}).
    for node_name in list(pipeline_params_override.keys()):
        nested = pipeline_params_override[node_name]
        if not isinstance(nested, dict):
            continue
        for sub_k in list(nested.keys()):
            if sub_k in PROMPT_STRING_FIELDS:
                logger.warning("l1_generate: un-nesting prompt field %r from %r", sub_k, node_name)
                prompt_changes[sub_k] = nested.pop(sub_k)
            elif sub_k in TASK_CONTEXT_OVERRIDES:
                logger.warning("l1_generate: un-nesting task_context %r from %r", sub_k, node_name)
                tc_changes[sub_k] = nested.pop(sub_k)
        if not nested:
            del pipeline_params_override[node_name]

    if pipeline_schema:
        # Split dotted-path keys (e.g. {"llm_only.model": "X"} → {"llm_only": {"model": "X"}}).
        # Weaker LLMs emit this shape; without splitting they fall through to the
        # hallucinated-node drop below and the variant lands as a no-op.
        for dk in [k for k in list(pipeline_params_override) if "." in k]:
            node, _, param = dk.partition(".")
            if pipeline_schema.has_node(node) and param:
                logger.warning("l1_generate: splitting dotted key %r → %s.%s", dk, node, param)
                pipeline_params_override.setdefault(node, {})[param] = pipeline_params_override.pop(
                    dk
                )
        # Auto-nest flat params + drop hallucinated nodes.
        for fk in [k for k, val in pipeline_params_override.items() if not isinstance(val, dict)]:
            owner = pipeline_schema.node_for_param(fk)
            if owner:
                logger.warning("l1_generate: auto-nesting flat param %r → %s", fk, owner)
                pipeline_params_override.setdefault(owner, {})[fk] = pipeline_params_override.pop(
                    fk
                )
        for bk in [k for k in pipeline_params_override if not pipeline_schema.has_node(k)]:
            logger.warning("l1_generate: dropping hallucinated node %r", bk)
            del pipeline_params_override[bk]

    return prompt_changes, tc_changes, pipeline_params_override


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
            sorted(
                (n, tuple(sorted(p.items())))
                for n, p in (cp.pipeline_params_override or {}).items()
                if p
            )
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
    return L1YieldStats(l1_yield=yield_, l1_n_no_op=n_no_op, l1_n_duplicate=n_duplicate)
