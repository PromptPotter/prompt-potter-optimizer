"""L1-generate input/output validation + variant invariant detection.

Three concerns, all validation-shaped:

- **Schema construction**: ``build_l1_response_schema`` grafts per-node
  ``param_allowed_values`` into the static l1_generate envelope and
  constrains the prompt-field + task-context slots so the LLM is
  constrained at output-time across all three override slots.
- **Schema compliance**: ``validate_overrides`` + ``L1_SCHEMA_COMPLIANCE``
  catch ``pipeline_params_override`` values that violate the schema
  after the fact (LLMs sometimes ignore parts of the schema). The
  ``ValidatorOutcome`` lands on L1's own ``l1_wounds`` — L1 re-proposes.
- **Variant invariants**: ``detect_invariants`` flags ``no_op_variant``
  (no mutation vs parent) and ``duplicate_variant`` (sig-equal across
  population). Returns ``L1YieldStats`` for the round.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.application.config import missing_template_vars
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    resolve_node_schema_field_names,
)
from promptpotter.application.optimization.dispatch.schemas import L1GenerateOutput, L1Variant
from promptpotter.config.prompt_blocks import prompt_blocks
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, NODE_LAYOUTS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import (
    NESTED_PARAM_TYPES,
    SCHEMA_DESCRIPTIONS_PARAM,
    SCHEMA_OWNED_FIELDS,
    SCHEMA_RENAME_PARAM,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "DROPPED_MANDATORY_PLACEHOLDER",
    "L1_CONFIG_NOT_IN_RUNTIME_FAILURES",
    "L1_PROMPT_BLOCKS_IN_LIBRARY",
    "L1_PROMPT_PLACEHOLDERS_INTACT",
    "L1_SCHEMA_COMPLIANCE",
    "L1YieldStats",
    "build_l1_response_schema",
    "detect_invariants",
    "effective_l1_field_names",
    "validate_overrides",
]

# A dropped mandatory backend placeholder is structural, not a tunable miss: the round
# loop reads this reason off the candidate reports to fire L2 immediately (patience 0),
# rather than burning l1_patience rounds re-dropping it. Single-sourced so the producer
# (the validator below) and the consumer (`runner/round.py`) never drift.
DROPPED_MANDATORY_PLACEHOLDER = "dropped_mandatory_placeholder"


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


def effective_l1_field_names() -> dict[str, str]:
    """The `{field: wire_name}` rename in force for this call — the ONE source both the emitted
    JSON Schema and the response model derive from. Two surfaces, one function: a schema that
    renames a field the response model does not alias fails every parse, every round.

    **Unconditional, and it must be.** `schema_field_rename` governs whether an OUTER campaign's
    L1 may *propose* a rename — it gates the graft, below. The INNER cycle honours whatever
    mutation it is handed, exactly as it does for prose, `layout`, and
    `output_schema_descriptions`. Gating this on the inner cycle's own config would open a silent
    no-op channel: an inner campaign loads its config from the inner dataset's `campaign.json`
    (`runner/inner_recursion.py`), never from the outer's, so the outer would emit a rename that
    nothing applied — and score it as a legitimate mutation.

    Empty on every normal, non-L4 cycle (no override bound). A proposed rename is dropped when
    its target collides with a field that is not itself being renamed away
    (`{changes_description: variant_name}` would make the response ambiguous).
    """
    proposed = resolve_node_schema_field_names("l1_generate")
    if not proposed:
        return {}
    survivors = set(L1Variant.model_fields) - set(proposed)
    return {f: w for f, w in proposed.items() if f in L1Variant.model_fields and w not in survivors}


def _rename_variant_schema(variant: dict[str, Any], field_names: dict[str, str]) -> None:
    """Rewrite `properties` keys and the `required` list in place; order is preserved."""
    props = variant.get("properties") or {}
    variant["properties"] = {field_names.get(k, k): v for k, v in props.items()}
    required = variant.get("required")
    if isinstance(required, list):
        variant["required"] = [field_names.get(k, k) for k in required]


_SCHEMA_DESCRIPTIONS_INSTRUCTION = (
    "Rewrite the JSON-Schema `description` of a field on this node's OWN output "
    "schema. This prose sits adjacent to the slot it governs, inside the field-"
    "filling loop, so it steers the model harder per token than the instruction "
    "does. Keys are the node's existing field names and are FIXED — you describe a "
    "field, you never rename or add one. Describe only where the current prose "
    "underspecifies what the field should hold."
)

_SCHEMA_RENAME_INSTRUCTION = (
    "Rename a field on the inner optimizer's own output schema. The model holds "
    "strong priors about what belongs under a given key, so the name steers "
    "before a single token of the value is written. Keys are the existing field "
    "names; values are the new wire names. Rename only when the current name "
    "misdescribes what the field should hold — a rename the model then fails to "
    "honour makes the round unparseable and scores it maximally dirty."
)


def _nested_param_property(node: PipelineNode, param: str) -> dict[str, Any] | None:
    """The emitted sub-schema for a NESTED (`object`-declared) optimizer param.

    Three levers, each keyed by a CLOSED set so the optimizer can edit but never invent:

    - `output_schema_descriptions` — the always-on `description` lever
      (`docs/concepts/structured-output.md`). Keyed by **this node's own output-schema
      fields** — synthesized onto every `output_schema`-bearing node, target or optimizer,
      at parse time. This is the core case: `l1_generate` describing `llm_only`'s
      `{reasoning, answer}` is the same code path as describing the inner optimizer's own
      variant fields. `None` when the node ships no schema.
    - `layout` (L4's information-flow lever): per-slot lists of injection names; value space
      is the node's own `NODE_LAYOUTS` vocabulary. `None` when the node is not one of this
      optimizer's own (no layout spec).
    - `output_schema_field_names` (L4 rename, gated): the strongest lever, keyed by the inner
      `l1_generate`'s own variant fields; offered only where a node declares it.
    """
    if param == SCHEMA_DESCRIPTIONS_PARAM:
        out_schema = node.output_schema
        if out_schema is None or not out_schema.fields:
            return None
        return {
            "type": "object",
            "description": _SCHEMA_DESCRIPTIONS_INSTRUCTION,
            "properties": {f: {"type": "string"} for f in out_schema.fields},
            "additionalProperties": False,
        }
    if param == "layout":
        spec = NODE_LAYOUTS.get(node.name)
        if spec is None:
            return None
        allowed = sorted(spec.possible)
        return {
            "type": "object",
            "properties": {
                slot: {"type": "array", "items": {"type": "string", "enum": allowed}}
                for slot in L1_LAYOUT_SLOTS
            },
            "additionalProperties": False,
        }
    if param == SCHEMA_RENAME_PARAM and NODE_LAYOUTS.get(node.name) is not None:
        return {
            "type": "object",
            "description": _SCHEMA_RENAME_INSTRUCTION,
            "properties": {f: {"type": "string"} for f in L1Variant.model_fields},
            "additionalProperties": False,
        }
    return None


def build_l1_response_schema(
    pipeline_schema: PipelineSchema,
    *,
    forbidden_axes_strict: bool = True,
    schema_field_rename: bool = False,
) -> dict[str, Any]:
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

    # The emittable per-node param surface is `node_param_keys(forbidden_strict)` —
    # the ONE source the catalogue + validator share. When locked it omits the
    # model axis entirely (the LLM cannot emit a key the schema doesn't declare,
    # so the lock needs no per-round rejection); when unlocked it synthesizes a
    # `model` axis whose value space is `available_models`.
    npk = pipeline_schema.node_param_keys(forbidden_strict=forbidden_axes_strict)
    available_models = list(pipeline_schema.available_models)
    for node_name, keys in npk.items():
        node = pipeline_schema.get_node(node_name)
        if node is None:
            continue
        # Scalars first, then the nested params, each alphabetical. Field ORDER is what
        # this schema teaches (`docs/concepts/structured-output.md`), so the two groups
        # are emitted in a fixed sequence rather than one interleaved sort — the meta-
        # levers read after the surface they act on.
        nested = {p for p in keys if node.param_types.get(p) in NESTED_PARAM_TYPES}
        param_props: dict[str, dict[str, Any]] = {}
        for param in sorted(keys - nested):
            if param == "model" and available_models:
                param_props[param] = {"type": "string", "enum": available_models}
                continue
            allowed = node.param_allowed_values.get(param)
            declared_type = node.param_types.get(param)
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            elif declared_type:
                param_props[param] = {"type": declared_type}
            else:
                param_props[param] = {}
        # The field-NAME lever is the strongest and the only one that can break a parser,
        # so the campaign must unlock it: dropped from the emitted schema when locked, and
        # the LLM cannot emit a key the schema omits. Structural, never policed per round.
        # Unlocked, the rename is a presentation transform — `build_l1_response_model`
        # aliases the wire key back onto the real field, so no downstream reader observes it.
        if not schema_field_rename:
            nested.discard(SCHEMA_RENAME_PARAM)
        for param in sorted(nested):
            prop = _nested_param_property(node, param)
            if prop is not None:
                param_props[param] = prop
        if not param_props:
            continue
        pp_properties[node_name] = {
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

    # 4. Rename LAST. `build_l1_response_model` aliases the same map back so no downstream
    # reader observes the wire name. (The `description` lever no longer touches THIS schema:
    # it rewrites each TARGET node's own `output_schema` at the wire seam
    # `OptSearchPoint.to_job_search_point`, keyed by that node's fields — the core case.)
    field_names = effective_l1_field_names()
    if field_names:
        _rename_variant_schema(inlined["properties"]["variants"]["items"], field_names)

    return {"name": "l1_variants", "schema": inlined, "strict": False}


_JSON_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _matches_declared_type(value: Any, declared: str) -> bool:
    """JSON-Schema-flavoured isinstance: booleans are not numbers."""
    py_types = _JSON_TYPE_TO_PY.get(declared)
    if py_types is None:
        return True  # type we don't model → unconstrained here (schema gates structure)
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
    JSON-schema ``type`` constraint in :func:`build_l1_response_schema` is
    meant to prevent — both layers run because not every provider/SDK
    enforces structured-output schemas with full fidelity.

    ``SCHEMA_OWNED_FIELDS`` (``output_schema`` + schema registry identity) are
    rejected UNCONDITIONALLY (no ablation unlock): the output schema is the
    pipeline's structural wire contract, and a mutated one breaks the backend.
    ``node_param_keys`` already strips them from the emittable surface, so this is
    the deterministic backstop for a provider that leaks the key past its schema —
    the structural twin of the model/provider lock.

    A param the node never advertised is rejected as ``reason="unknown_param"``. The
    emitted schema declares exactly ``node_param_keys``; anything else arrived past a
    weakly-conformant provider's ``additionalProperties: false`` and would merge into
    ``pipeline_params`` unchecked — unlike a hallucinated NODE, which
    ``merge_pipeline_params`` drops downstream. This is the node-name check's per-param
    twin, and it is what makes ``node_param_keys`` the single emittable surface its own
    docstring claims: the catalogue and the schema derived from it; the validator did not.
    """
    failures: list[ValidationFailure] = []
    allowed_models = list(pipeline_schema.available_models)
    emittable = pipeline_schema.node_param_keys(forbidden_strict=forbidden_axes_strict)
    for node_name, node_params in pipeline_params_override.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        if node is None:
            # Hallucinated node: L1 named a node absent from the active schema. The
            # JSON-schema node-name enum is advisory (``build_l1_response_schema`` emits
            # ``strict=False``, so a weakly-conformant provider slips a phantom key past
            # ``additionalProperties: false``). The phantom edit is stripped from the wire
            # downstream (``merge_pipeline_params`` drops nodes outside ``active_steps``),
            # so recording it here ROUTES the signal without changing what runs — and it is
            # NON-FATAL (the reason-aware synthetic-0 gate in ``l1/score/candidate.py`` lets
            # the candidate's real edits score). It flows through the existing channel:
            # ``l1_wounds`` (next-round self-correction) + the ``validation_failure_rate``
            # evaluator (L4 meta-analyses hallucination-rate as a meta-prompt quality axis).
            # This is the node-name twin of ``validate_l1_layout``'s unknown-placeholder wound.
            failures.append(
                ValidationFailure(
                    axis=node_name,
                    value=node_name,
                    allowed=sorted(pipeline_schema.active_steps),
                    reason="hallucinated_node",
                )
            )
            continue
        node_allowed = node.param_allowed_values
        node_types = node.param_types
        node_emittable = emittable.get(node_name, set())
        for param, value in node_params.items():
            if param in SCHEMA_OWNED_FIELDS or (
                forbidden_axes_strict and param in PARAM_FORBIDDEN_KEYS
            ):
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=[],
                        reason="forbidden_axis",
                    )
                )
                continue
            # After the locked axes, so a leaked `model` keeps its specific reason.
            if param not in node_emittable:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=sorted(node_emittable),
                        reason="unknown_param",
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
    """Wrap validate_overrides → ValidatorOutcome (L1 retunes its own override)."""
    if not source_output or not pipeline_schema:
        return None
    failures = validate_overrides(
        source_output, pipeline_schema, forbidden_axes_strict=forbidden_axes_strict
    )
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_SCHEMA_COMPLIANCE.id,
        evidence={"failures": failures},
    )


L1_SCHEMA_COMPLIANCE: LLMOutputValidator = LLMOutputValidator(
    id="l1_schema_compliance",
    check=_check_l1_schema_compliance,
)


def _check_l1_prompt_blocks_in_library(
    source_output: Any,
    *,
    prompt_block_catalogue: str = "guidance",
    **_: Any,
) -> ValidatorOutcome | None:
    """Under ``restrict``, a prompt-field value L1 PROPOSES must come from the block library.

    Reads the round's ``prompt_fields_override`` — the delta, not the resulting OSP. The
    parent's fields are the dataset's authored origin and are not in the library by
    construction; checking the merged result would reject every candidate on round 1 for a
    field nobody touched. Only ``guidance`` (the default) and ``off`` render nothing here —
    they leave the value space open, so there is nothing to violate.

    A field with no library entries (``problem_description`` — task-specific by nature) is
    unrestricted: ``restrict`` narrows a declared value space, it does not close an
    undeclared one.
    """
    if prompt_block_catalogue != "restrict" or not source_output:
        return None
    library = prompt_blocks()
    failures = [
        ValidationFailure(
            axis=field,
            value=str(value),
            allowed=list(blocks),
            reason="not_in_prompt_block_library",
        )
        for field, value in source_output.items()
        if (blocks := library.get(field))
        and str(value).strip()
        and str(value).strip() not in blocks
    ]
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_BLOCKS_IN_LIBRARY.id,
        evidence={"failures": failures},
    )


L1_PROMPT_BLOCKS_IN_LIBRARY: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_blocks_in_library",
    check=_check_l1_prompt_blocks_in_library,
)


def _check_l1_config_in_runtime_failures(
    source_output: Any,
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reject candidates that re-propose a config already proven to fail.

    Pure wire-level check — for each (param, value) in the candidate's
    ``pipeline_params_override``, scan ``opt_sp.memory.wounds.runtime_failures``
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
    failures_list = list(opt_sp.memory.wounds.runtime_failures)
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
        evidence={"failures": out_failures},
    )


L1_CONFIG_NOT_IN_RUNTIME_FAILURES: LLMOutputValidator = LLMOutputValidator(
    id="l1_config_not_in_runtime_failures",
    check=_check_l1_config_in_runtime_failures,
)


def _check_l1_prompt_placeholders_intact(
    source_output: Any,
    *,
    opt_sp: OptSearchPoint | None = None,
    pipeline_schema: PipelineSchema | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reject an L1 candidate whose evolved prompt drops a mandatory backend placeholder.

    The optimizer's evolved prompt lands on exactly ONE node — ``prompt_node_names()[0]``,
    the node ``OptSearchPoint.to_job_search_point`` injects ``osp.render()`` into. The other
    prompt-bearing nodes keep their fixed starting prompt and are never touched by L1, so this
    checks that one node only. If a mutation drops one of its declared ``{{vars}}`` (e.g.
    ``{{combined_text}}`` / ``{{format_string}}`` carrying web_search evidence into
    entity_profiling), the backend injects nothing there and the evidence-free program would
    otherwise score as a valid winner. Mint guards this at setup
    (``configure_and_apply_pipeline``); this is its in-loop twin, same ``missing_template_vars``.
    """
    if opt_sp is None or pipeline_schema is None:
        return None
    prompt_nodes = pipeline_schema.prompt_node_names()
    if not prompt_nodes:
        return None
    node = pipeline_schema.get_node(prompt_nodes[0])
    if node is None or node.prompt_info is None:
        return None
    declared = node.prompt_info.template_variables
    missing = missing_template_vars(opt_sp.render(), declared) if declared else []
    if not missing:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_PLACEHOLDERS_INTACT.id,
        evidence={
            "failures": [
                ValidationFailure(
                    axis=f"{prompt_nodes[0]}.prompt",
                    value="dropped:" + ",".join(missing),
                    allowed=list(declared),
                    reason=DROPPED_MANDATORY_PLACEHOLDER,
                )
            ]
        },
    )


L1_PROMPT_PLACEHOLDERS_INTACT: LLMOutputValidator = LLMOutputValidator(
    id="l1_prompt_placeholders_intact",
    check=_check_l1_prompt_placeholders_intact,
)


@dataclass(frozen=True)
class L1YieldStats:
    """Round-level L1 generation quality.

    Field names mirror ``RoundDiagnostics.l1_yield``/``l1_n_no_op``/``l1_n_duplicate``
    so callers can spread via ``dataclasses.asdict`` rather than translate.
    """

    l1_yield: float  # n_valid / n_proposed (1.0 when no proposals)
    l1_n_no_op: int
    l1_n_duplicate: int
    # Set when the meta-prompt made L1's own output unparseable — the round then holds zero
    # candidates. `detect_invariants` never sets it (it only sees proposals that exist); it is
    # stamped from `l1_generate`'s return in `generate_or_load_candidates`.
    l1_parse_failure: str | None = None


_INVARIANT_REASONS = frozenset({"no_op_variant", "duplicate_variant"})


def _parent_value(parent_cfg: dict[str, Any], param: str, proposed: Any) -> Any:
    """The parent's CURRENT value for *param*, shaped like the override that proposes it.

    Every param but one reads straight off the parent's node config. ``output_schema_descriptions``
    is virtual: the prose it edits lives folded inside ``output_schema.properties[f].description``
    (:func:`fold_schema_descriptions`), so the parent never carries the key and a naive lookup
    returns ``None`` — making an exact re-proposal of the existing prose read as a mutation. It is
    reconstructed here, keyed by the fields the override actually names.
    """
    if param == SCHEMA_DESCRIPTIONS_PARAM and isinstance(proposed, dict):
        props = (parent_cfg.get("output_schema") or {}).get("properties") or {}
        return {f: (props.get(f) or {}).get("description", "") for f in proposed}
    return parent_cfg.get(param)


def detect_invariants(
    proposals: list[CandidateProposal],
    parent_osp: OptSearchPoint,
    parent_pipeline_params: dict[str, Any] | None,
) -> L1YieldStats:
    """Attach no_op_variant / duplicate_variant failures; return yield stats.

    Failures route through score_population's synthetic-0 path (Path 1) so
    invariant variants don't burn LLM calls. Idempotent — pre-existing
    invariant failures are dropped first so resume-from-disk doesn't dup.

    All three components of the signature are DELTAS against the parent — *parent_pipeline_params*
    is the parent's resolved, folded config (``JobSearchPoint.pipeline_params``). The param
    component used to be the raw override, so a variant re-proposing a value the parent already
    held — ``temperature: 0.0`` onto a parent at ``0.0``, or the parent's own ``answer``
    description echoed back verbatim — counted as a mutation, cleared the no-op gate, and burned
    a full scoring pass on a searchpoint identical to its parent. ``None`` is the honest
    "parent declared no node config" case (:attr:`JobSearchPoint.pipeline_params`), where every
    override is by definition a real delta.
    """
    parent_pp = parent_pipeline_params or {}
    for cp in proposals:
        cp.osp.memory.wounds.validation_failures = [
            vf
            for vf in cp.osp.memory.wounds.validation_failures
            if vf.reason not in _INVARIANT_REASONS
        ]
    seen: dict[tuple[Any, ...], int] = {}
    n_no_op = 0
    n_duplicate = 0
    parent_tc = parent_osp.memory.task_context.to_dict()
    for i, cp in enumerate(proposals):
        child = cp.osp
        pf_delta = tuple(
            (f, getattr(child, f))
            for f in PROMPT_STRING_FIELDS
            if getattr(child, f) != getattr(parent_osp, f)
        )
        child_tc = child.memory.task_context.to_dict()
        tc_delta = tuple(sorted((k, v) for k, v in child_tc.items() if v != parent_tc.get(k)))
        # json canon (not tuple-of-items) so a nested value — e.g. a slice-6 `layout`
        # dict riding alongside the prose edits — stays hashable for the `seen` sig.
        # Per-PARAM, and only where the proposal actually moves the parent's value: an
        # override that restates what the parent already holds is not a mutation.
        pp_delta = tuple(
            sorted(
                (n, p, json.dumps(v, sort_keys=True))
                for n, cfg in (cp.pipeline_params_override or {}).items()
                for p, v in (cfg or {}).items()
                if v != _parent_value(parent_pp.get(n) or {}, p, v)
            )
        )
        sig = (pf_delta, tc_delta, pp_delta)
        if not any(sig):
            cp.osp.memory.wounds.validation_failures = [
                *cp.osp.memory.wounds.validation_failures,
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
            cp.osp.memory.wounds.validation_failures = [
                *cp.osp.memory.wounds.validation_failures,
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
