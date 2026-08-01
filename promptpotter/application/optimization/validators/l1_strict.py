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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from promptpotter.application.config import missing_template_vars
from promptpotter.application.optimization.dispatch.llm_call import prompts as _opt_prompts
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    resolve_node_override,
)
from promptpotter.application.optimization.dispatch.schemas import L1GenerateOutput, L1Variant
from promptpotter.config.prompt_blocks import prompt_blocks
from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES
from promptpotter.domain.escalation_signals import INVARIANT_REASONS, ValidationFailure
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, NODE_LAYOUTS
from promptpotter.domain.opt_search_point import (
    IDEA_MATCH_REJECT,
    TEMPLATE_TOKEN_RE,
    OptSearchPoint,
    PromptTemplate,
    candidate_delta,
    candidate_idea,
    node_config_items,
    same_idea,
)
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
    "INVARIANT_REASONS",
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
    (`runner/inner/cycle.py`), never from the outer's, so the outer would emit a rename that
    nothing applied — and score it as a legitimate mutation.

    Empty on every normal, non-L4 cycle (no override bound). A proposed rename is dropped when
    its target collides with a field that is not itself being renamed away
    (`{changes_description: prompt_fields_override}` would make the response ambiguous).
    """
    proposed = resolve_node_override("l1_generate").schema_field_names
    if not proposed:
        return {}
    survivors = set(L1Variant.model_fields) - set(proposed)
    return {f: w for f, w in proposed.items() if f in L1Variant.model_fields and w not in survivors}


def _rename_variant_schema(variant: dict[str, Any], field_names: dict[str, str]) -> None:
    """Rewrite `properties` keys and the `required` list in place; order is preserved."""
    props = variant["properties"]
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
    citable_fields: Sequence[str],
    schema_field_rename: bool = False,
) -> dict[str, Any]:
    """l1_generate response_schema — three constrained slots per variant.

    ``citable_fields`` is this round's ``evidence_grounding.field`` enum — the evidence
    panels the live layout renders (``registry.citable_fields``). Grafted here rather than
    frozen on the model: what L1 may cite is what L1 was shown, and a layout edit changes it.

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

    Returns the BARE JSON Schema. ``chat()``'s ``response_schema`` *is* the wire
    schema (``llm/base.py``); the client owns the ``{name, schema, strict}`` provider
    envelope. Returning an envelope here nests the real schema one level down, where
    the provider reads a top-level object with no ``type`` and no ``properties`` —
    every constraint below inert.
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

    # The emittable per-node param surface is `node_param_keys()` — the ONE source
    # the catalogue + validator share. It omits model/provider entirely (the LLM
    # cannot emit a key the schema doesn't declare, so the lock needs no per-round
    # rejection).
    # No `model` branch here, deliberately. `node_param_keys()` has already stripped
    # model/provider via PARAM_FORBIDDEN_KEYS, so `keys` cannot contain them and a
    # `if param == "model": <emit available_models enum>` arm is unreachable. One stood
    # here anyway, which is worse than useless: it read as though the campaign's model
    # catalogue were an emittable axis, contradicting the lock the line above states.
    # The lock is structural — the LLM cannot emit a key the schema never declares.
    npk = pipeline_schema.node_param_keys()
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

    # 4. evidence_grounding.field — the panels THIS round's prompt renders. The optional
    # slot inlines as `anyOf: [<object>, null]`, so find the object arm.
    for arm in variant_props["evidence_grounding"].get("anyOf", []):
        eg_field = arm.get("properties", {}).get("field")
        if eg_field is not None:
            eg_field["enum"] = list(citable_fields)

    # 5. Rename LAST. `build_l1_response_model` aliases the same map back so no downstream
    # reader observes the wire name. (The `description` lever no longer touches THIS schema:
    # it rewrites each TARGET node's own `output_schema` at the wire seam
    # `OptSearchPoint.to_job_search_point`, keyed by that node's fields — the core case.)
    field_names = effective_l1_field_names()
    if field_names:
        _rename_variant_schema(inlined["properties"]["variants"]["items"], field_names)

    return cast("dict[str, Any]", inlined)


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
) -> list[ValidationFailure]:
    """Validate overrides vs available_models + param_allowed_values + param_types; failures drive synthetic-0.

    Any touch of ``PARAM_FORBIDDEN_KEYS`` (``model``, ``provider``) is rejected
    outright, independent of whether the proposed value would be in
    ``available_models`` — these axes are operator-owned (dataset overlay or a
    cap-gated fork edit) and never on L1's surface at all.

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
    emittable = pipeline_schema.node_param_keys()
    for node_name, node_params in pipeline_params_override.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        if node is None:
            # L1 named a node absent from the active schema. The wire schema declares the
            # node-name properties + ``additionalProperties: false``, but the client sends
            # ``strict=False``, so the provider is never FORCED to honour them — this is the
            # deterministic backstop. ``merge_pipeline_params`` drops nodes outside
            # ``active_steps``, so recording it ROUTES the signal without changing what runs,
            # and it is NON-FATAL (the reason-aware synthetic-0 gate in
            # ``l1/score/candidate.py`` lets the candidate's real edits score): ``l1_wounds``
            # + the ``validation_failure_rate`` evaluator. The node-name twin of
            # ``validate_l1_layout``'s unknown-placeholder wound.
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
            if param in SCHEMA_OWNED_FIELDS or param in PARAM_FORBIDDEN_KEYS:
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
    **_: Any,
) -> ValidatorOutcome | None:
    """Wrap validate_overrides → ValidatorOutcome (L1 retunes its own override)."""
    if not source_output or not pipeline_schema:
        return None
    failures = validate_overrides(source_output, pipeline_schema)
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

    Pure wire-level check — no LLM evidence judgment, just "we already proved this fails."

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


def _meta_template_failures(pipeline_params: dict[str, Any]) -> list[ValidationFailure]:
    """INLINE injection ports dropped from an inner meta-prompt's prose (the L4 surface).

    A ``{{token}}`` embedded mid-sentence in a meta-prompt field (``{{n_variants}}`` in
    ``l1_generate.task_intent``/``instruction``, ``{{citable_fields}}`` in
    ``l1_generate.answer_format``) is a channel port, not prose — an L4 rewrite that deletes
    it severs the channel while the schema keeps accepting proposals, and no measurement can
    see what went missing. This guard is PERMANENT, not transitional: these ports are inline
    format variables inside the sentence that uses them, so they can never move to the layout
    channel (whose mandatory guard is ``validate_l1_layout`` — the capability directives ride
    there, appended blocks with no inline position to hold). Do not delete this on a "layout
    covers it" argument; layout covers only block-shaped signals. Checks the MERGED params,
    not the round's delta: a child of a broken incumbent inherits the token-less prose without
    re-proposing it, and the program it would run is just as severed.
    """
    failures: list[ValidationFailure] = []
    for node_name, cfg in node_config_items(pipeline_params):
        if node_name not in NODE_LAYOUTS:
            continue
        prose = {
            k: v for k, v in cfg.items() if k in PromptTemplate.model_fields and isinstance(v, str)
        }
        if not prose:
            continue
        base = _opt_prompts.base_optimizer_template(node_name)
        declared = sorted(set(TEMPLATE_TOKEN_RE.findall(base.render())))
        if not declared:
            continue
        merged = base.model_copy(update=prose)
        missing = missing_template_vars(merged.render(), declared)
        if missing:
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.prompt",
                    value="dropped:" + ",".join(missing),
                    allowed=declared,
                    reason=DROPPED_MANDATORY_PLACEHOLDER,
                )
            )
    return failures


def _check_l1_prompt_placeholders_intact(
    source_output: Any,
    *,
    opt_sp: OptSearchPoint | None = None,
    pipeline_schema: PipelineSchema | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Reject a candidate whose program drops a mandatory injection placeholder.

    Two surfaces, one rule — a mutation may degrade what flows through a channel (measurable;
    the proxy goes negative), never delete the channel itself (unmeasurable):

    - **Target prompt**: the evolved prompt lands on exactly ONE node —
      ``prompt_node_names()[0]``, the node ``OptSearchPoint.to_job_search_point`` injects
      ``opt_sp.render()`` into; the other prompt-bearing nodes keep their fixed starting prompt.
      If a mutation drops a declared ``{{var}}`` (e.g. ``{{combined_text}}`` carrying
      web_search evidence into entity_profiling), the backend injects nothing there and the
      evidence-free program would otherwise score as a valid winner. Mint guards this at setup
      (``configure_and_apply_pipeline``); this is its in-loop twin, same ``missing_template_vars``.
    - **Inner meta-prompts** (L4): ``source_output`` is the candidate's MERGED
      ``pipeline_params``; :func:`_meta_template_failures` guards the prose-embedded ports the
      same way. Same reason, same fatal synthetic-0 path, same patience-0 L2 fire.
    """
    if opt_sp is None or pipeline_schema is None:
        return None
    failures: list[ValidationFailure] = []
    prompt_nodes = pipeline_schema.prompt_node_names()
    if prompt_nodes:
        node = pipeline_schema.get_node(prompt_nodes[0])
        if node is not None and node.prompt_info is not None:
            declared = node.prompt_info.template_variables
            missing = missing_template_vars(opt_sp.render(), declared) if declared else []
            if missing:
                failures.append(
                    ValidationFailure(
                        axis=f"{prompt_nodes[0]}.prompt",
                        value="dropped:" + ",".join(missing),
                        allowed=list(declared),
                        reason=DROPPED_MANDATORY_PLACEHOLDER,
                    )
                )
    if isinstance(source_output, dict):
        failures.extend(_meta_template_failures(source_output))
    if not failures:
        return None
    return ValidatorOutcome(
        validator_id=L1_PROMPT_PLACEHOLDERS_INTACT.id,
        evidence={"failures": failures},
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
    # Cross-ROUND collapses (the other two are round-local): re-proposals of an idea a prior
    # round already measured and lost. Defaulted so the many construction sites that predate
    # the gate stay valid — the count is only ever non-zero where prior rounds are passed.
    l1_n_repeat: int = 0
    # Set when the meta-prompt made L1's own output unparseable — the round then holds zero
    # candidates. `detect_invariants` never sets it (it only sees proposals that exist); it is
    # stamped from `l1_generate`'s return in `generate_or_load_candidates`.
    l1_parse_failure: str | None = None


# The reasons `detect_invariants` emits — a synthetic-0 candidate that never burned an LLM
# call. The SET now lives in `domain/escalation_signals.py`; this module writes the reasons,
# `RoundResult` reads them back to derive its collapse counts, and `presentation/views/display.py`
# filters on them when ranking. Re-exported here because three call sites already import it
# from this module and it is still the validator's own vocabulary.


def lost_ideas(prior_rounds: Sequence[Any]) -> list[tuple[int, frozenset[str]]]:
    """``(round, idea fingerprint)`` for every prior candidate that was MEASURED and LOST.

    The evidence base for ``repeat_variant``. Two filters, and both are load-bearing:

    * **Measured** (``total > 0``). A candidate that scored no samples carries
      ``accuracy == 0.0`` only because the field is a non-optional float — it is the absence
      of evidence, not a defeat. Rejecting a live proposal because an unmeasured one "already
      failed" would be the loop punishing an idea nobody ever ran. (Probe rounds manufactured
      exactly these wholesale before the lever was removed.)
    * **Lost.** An idea that BEAT its matched origin is not a dead end — refining a winner is
      the search working. Only ideas measured against a matched origin and found no better
      become grounds for rejection.
    """
    out: list[tuple[int, frozenset[str]]] = []
    for i, rr in enumerate(prior_rounds):
        parent = rr.prompt_fields
        parent_pp = prior_rounds[i - 1].pipeline_params if i > 0 else None
        for cand in rr.candidate_scores:
            if not cand.total or cand.matched_origin_accuracy is None:
                continue
            if cand.accuracy > cand.matched_origin_accuracy:
                continue
            if fp := candidate_idea(
                cand.prompt_fields, parent, cand.pipeline_params_override, parent_pp
            ):
                out.append((rr.round, fp))
    return out


def detect_invariants(
    proposals: list[CandidateProposal],
    parent_opt_sp: OptSearchPoint,
    parent_pipeline_params: dict[str, Any] | None,
    prior_rounds: Sequence[Any] = (),
) -> L1YieldStats:
    """Attach no_op_variant / duplicate_variant / repeat_variant failures; return yield stats.

    Failures route through score_population's synthetic-0 path (Path 1) so
    invariant variants don't burn LLM calls. Idempotent — pre-existing
    invariant failures are dropped first so resume-from-disk doesn't dup.

    **``repeat_variant`` is the cross-ROUND arm** (the other two are round-local). A candidate
    is rejected when it re-proposes an idea a prior round already measured and lost — matched
    on the content words it ADDED to its parent (:func:`candidate_idea`), so it still fires when
    the idea has been rewritten into a different field — the only form a re-proposal actually
    takes — without convicting a candidate for re-emitting the field body its edit had to carry.

    Rejecting is destructive — the variant simply never exists, and a wrong rejection leaves no
    trace — so four things bound it. (1) The evidence is filtered to measured losses only
    (:func:`lost_ideas`). (2) The threshold is :data:`IDEA_MATCH_REJECT`, stricter than the
    panel's marking threshold. (3) **A repeat is never allowed to empty the round**: if
    rejecting them would leave no live proposal, they are all restored — a round that retries a
    known-dead idea is a wasted round, but a round with zero candidates is a wasted round that
    also loses the loop's turn. (4) Every rejection names the round it repeats, so it surfaces
    on the wound channel and in ``review.md`` rather than vanishing into a yield number.

    All three components of the signature are DELTAS against the parent — *parent_pipeline_params*
    is the parent's resolved, folded config (``JobSearchPoint.pipeline_params``). ``None`` is the
    honest "parent declared no node config" case (:attr:`JobSearchPoint.pipeline_params`), where
    every override is by definition a real delta.
    """
    parent_pp = parent_pipeline_params or {}
    for cp in proposals:
        cp.opt_sp.memory.wounds.validation_failures = [
            vf
            for vf in cp.opt_sp.memory.wounds.validation_failures
            if vf.reason not in INVARIANT_REASONS
        ]
    seen: dict[tuple[Any, ...], int] = {}
    n_no_op = 0
    n_duplicate = 0
    tried = lost_ideas(prior_rounds)
    # Repeats are collected, not applied inline: whether they may be rejected at all depends on
    # how many proposals SURVIVE the other two gates, which is only known after the loop.
    repeats: list[tuple[CandidateProposal, int]] = []
    n_live = 0
    parent_tc = parent_opt_sp.memory.task_context.to_dict()
    for i, cp in enumerate(proposals):
        child = cp.opt_sp
        pf, pp = candidate_delta(
            {f: getattr(child, f) for f in PROMPT_STRING_FIELDS},
            {f: getattr(parent_opt_sp, f) for f in PROMPT_STRING_FIELDS},
            cp.pipeline_params_override,
            parent_pp,
        )
        pf_delta = tuple(pf.items())
        child_tc = child.memory.task_context.to_dict()
        tc_delta = tuple(sorted((k, v) for k, v in child_tc.items() if v != parent_tc.get(k)))
        # json canon (not tuple-of-items) so a nested value — e.g. a slice-6 `layout`
        # dict riding alongside the prose edits — stays hashable for the `seen` sig.
        pp_delta = tuple(sorted((n, p, json.dumps(v, sort_keys=True)) for (n, p), v in pp.items()))
        sig = (pf_delta, tc_delta, pp_delta)
        if not any(sig):
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
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
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
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
        n_live += 1
        # The idea is the words the candidate ADDED to its parent — never the field names, and
        # never the changed field's whole value: both collapse the test into "touched the same
        # field" (see `candidate_idea`).
        fp = candidate_idea(
            {f: getattr(child, f) for f in PROMPT_STRING_FIELDS},
            {f: getattr(parent_opt_sp, f) for f in PROMPT_STRING_FIELDS},
            cp.pipeline_params_override,
            parent_pp,
        )
        echo = next(
            (rnd for rnd, prev in tried if same_idea(fp, prev, threshold=IDEA_MATCH_REJECT)),
            None,
        )
        if echo is not None:
            repeats.append((cp, echo))

    # Safety valve — a repeat may cost the round a candidate, never the whole round. `n_live`
    # counts proposals that cleared no-op + duplicate; if every one of them is also a repeat,
    # none is rejected. The loop then re-tests a known-dead idea for one round, which the
    # ALREADY TRIED panel still marks — strictly better than handing PoBB an empty population
    # and burning the turn on nothing.
    n_repeat = 0
    if len(repeats) < n_live:
        for cp, echo in repeats:
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"re-proposes the idea measured and lost in round {echo}",
                    allowed=["an idea this cycle has not already lost with"],
                    reason="repeat_variant",
                ),
            ]
            n_repeat += 1
    elif repeats:
        logger.debug(
            "repeat_variant: %d/%d proposals re-propose a lost idea — none rejected "
            "(rejecting all would leave the round with no candidates)",
            len(repeats),
            n_live,
        )
    n = len(proposals)
    yield_ = (n - n_no_op - n_duplicate - n_repeat) / n if n else 1.0
    return L1YieldStats(
        l1_yield=yield_,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_duplicate,
        l1_n_repeat=n_repeat,
    )
