"""The target pipeline's params, typed: parsed once off the laid document, dumped back to it whole."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Self

from pydantic import (
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    ModelWrapValidatorHandler,
    PrivateAttr,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from promptpotter.domain.layer_fold import Laid, Lay, Layer, Rule, fold_layers
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

# Where the laid document names the chain beside its nodes: the wire's spelling, and the dump's.
STEPS_KEY = "steps"


class _Roster(Enum):
    """Which roster an engine call key joins; a key carries its rosters on its `NodeCall` field."""

    # WHO ANSWERS the call: a steer touching only these inherits its done C0.
    WHO_ANSWERS = "who_answers"
    # Never a search axis: hosts of one model disagree, so an arm moving one measures the plumbing.
    FORBIDDEN = "forbidden"
    # A sampling scope the provider request carries.
    SCOPE = "scope"


class _LaidOrder(tuple[str, ...]):
    """Keys in their laid order: the wire's, never a call's identity, so every order equals every
    other and a call's equality reads its fields alone."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _LaidOrder)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return 0


@shapes_optimizer_prompt
class NodeCall(FrozenModel):
    """A node's engine call keys typed as laid (strict: a wrong type refuses, a number stays the int
    or float it was hashed as), each field its key's one spelling; other keys are its backend's."""

    model_config = ConfigDict(strict=True, extra="allow")
    # The dump keeps the laid order (pydantic's lists the declared fields first); a copy, which
    # validates nothing, keeps its source's, and its updated keys follow.
    _laid: _LaidOrder = PrivateAttr(default_factory=_LaidOrder)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return handler(core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema()))

    provider: Annotated[str | None, _Roster.WHO_ANSWERS, _Roster.FORBIDDEN] = None
    model: Annotated[str | None, _Roster.WHO_ANSWERS] = None
    route_order: Annotated[list[str] | None, _Roster.WHO_ANSWERS, _Roster.FORBIDDEN] = None
    temperature: Annotated[int | float | None, _Roster.SCOPE] = None
    max_tokens: Annotated[int | None, _Roster.SCOPE] = None
    reasoning_effort: Annotated[str | None, _Roster.SCOPE] = None
    top_p: Annotated[int | float | None, _Roster.SCOPE] = None
    seed: int | None = None
    prompt_family: str | None = None
    prompt_version: int | None = None
    schema_family: str | None = None
    schema_version: int | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _keeps_order(cls, laid: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        if not isinstance(laid, Mapping):
            return handler(laid)
        # A null call key is unset: equality and the digest read one shape.
        laid = {k: v for k, v in laid.items() if v is not None or k not in cls.model_fields}
        call = handler(laid)
        call._laid = _LaidOrder(laid)
        return call

    @model_serializer(mode="plain")
    def _dump(self) -> dict[str, object]:
        held = {
            key: getattr(self, key)
            for key in (*type(self).model_fields, *(self.model_extra or {}))
            if key in self.model_fields_set
        }
        return {**{key: held[key] for key in self._laid if key in held}, **held}

    def laid_keys(self) -> dict[str, Any]:
        """Each key as laid to its value, in its laid order, the backend's beside the call keys: what
        the wire sends, and every dump of a call."""
        return self.model_dump()


def laid_calls(calls: Mapping[str, NodeCall]) -> dict[str, dict[str, Any]]:
    return {node: call.laid_keys() for node, call in calls.items()}


def _call_keys_in(roster: _Roster) -> frozenset[str]:
    return frozenset(
        name for name, info in NodeCall.model_fields.items() if roster in info.metadata
    )


WHO_ANSWERS_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(
    _Roster.WHO_ANSWERS
)
PARAM_FORBIDDEN_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(
    _Roster.FORBIDDEN
)
# A forbidden key changes who answers: else a steer of one inherits a C0 measured by another host.
assert PARAM_FORBIDDEN_KEYS <= WHO_ANSWERS_KEYS, PARAM_FORBIDDEN_KEYS - WHO_ANSWERS_KEYS
PARAM_SCOPE_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = _call_keys_in(_Roster.SCOPE)
# Every key an llm node's config may set beside its knobs and prompt fields.
CALL_CONFIG_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    NodeCall.model_fields
)


class PipelineParams(FrozenModel):
    """The chain's ``steps`` (``None`` where none is named) beside each node's laid config, dumped
    as that document: the wire sends it and every key hashes it; empty is the one "carries none".
    Built only by reading that document, so whatever it dumps reads back; no node takes a field's
    name (`RESERVED_NODE_NAMES`)."""

    steps: list[str] | None = None
    nodes: dict[str, NodeCall] = Field(default_factory=dict)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return handler(core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema()))

    @model_validator(mode="before")
    @classmethod
    def _split(cls, laid: object) -> object:
        if not isinstance(laid, Mapping):
            return laid
        # The keyword `PipelineParams(nodes=...)` arrives as a laid key: it refuses, never a node.
        if taken := sorted(laid.keys() & cls.model_fields.keys() - {STEPS_KEY}):
            raise ValueError(f"a node may not be named {taken}: the pipeline params read it")
        nodes = {k: v for k, v in laid.items() if k != STEPS_KEY}
        return {"nodes": nodes, **({"steps": laid[STEPS_KEY]} if STEPS_KEY in laid else {})}

    @model_serializer(mode="plain")
    def dump(self) -> dict[str, Any]:
        return {**self._laid_steps, **self.laid_nodes}

    @property
    def _laid_steps(self) -> dict[str, list[str]]:
        return {} if self.steps is None else {STEPS_KEY: list(self.steps)}

    @property
    def laid_nodes(self) -> dict[str, dict[str, Any]]:
        return laid_calls(self.nodes)

    def __bool__(self) -> bool:
        return self.steps is not None or bool(self.nodes)

    def node(self, name: str) -> NodeCall:
        return self.nodes.get(name) or NodeCall()

    def with_steps(self, steps: list[str] | None) -> Self:
        laid = {} if steps is None else {STEPS_KEY: list(steps)}
        return self.model_validate({**laid, **self.nodes})

    def with_nodes(self, nodes: Mapping[str, NodeCall | Mapping[str, object]]) -> Self:
        return self.model_validate({**self._laid_steps, **nodes})

    def overlaid(self, overlay: Mapping[str, Mapping[str, Any]], schema: PipelineSchema) -> Self:
        """*overlay* laid over its nodes; a node *schema* does not declare refuses."""
        if undeclared := sorted(overlay.keys() - {n.name for n in schema.declared_nodes}):
            raise ValueError(f"an overlay names undeclared nodes {undeclared}")
        return (
            self.with_nodes(lay_nodes(self.laid_nodes, overlay, schema=schema)) if overlay else self
        )


# A node never takes the name of a carrier field: the laid document reads `steps` as the chain, and
# refuses `nodes` (`_split`), so no door builds a node of either name.
RESERVED_NODE_NAMES = frozenset(PipelineParams.model_fields)


def node_rule(schema: PipelineSchema | None) -> Rule:
    """A node document's rule: each node a call, an ``object`` param *schema* declares laid key by key."""

    def rule(at: tuple[str, ...]) -> Laid:
        if len(at) == 1:
            return Laid(Lay.CALL)
        node = schema.get_node(at[0]) if schema is not None and len(at) == 2 else None
        typed = node is not None and node.param_types.get(at[1]) == "object"
        return Laid(Lay.KEYS if typed else Lay.WHOLE)

    return rule


def lay_nodes(
    *layers: Mapping[str, Mapping[str, Any]], schema: PipelineSchema | None = None
) -> dict[str, dict[str, Any]]:
    """*layers* of a node document, each over the last, by `node_rule`."""
    return fold_layers(node_rule(schema), [Layer("node", layer) for layer in layers]).document
