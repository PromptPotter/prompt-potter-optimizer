"""The target pipeline's params, typed: parsed once off the laid document, dumped back to it whole."""

from __future__ import annotations

import logging
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

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from promptpotter.domain.pipeline_schema import ParamSource, PipelineSchema

logger = logging.getLogger(__name__)

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


@shapes_optimizer_prompt
class NodeCall(FrozenModel):
    """A node's engine call keys typed as laid (strict: a wrong type refuses, a number stays the int
    or float it was hashed as), each field its key's one spelling; other keys are its backend's."""

    model_config = ConfigDict(strict=True, extra="allow")
    # The keys as laid, an ordered set: the dump keeps their order (pydantic's lists the declared
    # fields first), equality reads them as a set, so one call laid in two orders is one call.
    _laid: dict[str, None] = PrivateAttr(default_factory=dict)

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
        call = handler(laid)
        if isinstance(laid, Mapping):
            call._laid = dict.fromkeys(laid)
        return call

    def laid_keys(self) -> dict[str, Any]:
        """Each key as laid to its value, in its laid order, the backend's beside the call keys: what
        the wire sends."""
        dumped = self.model_dump(exclude_unset=True)
        return {**{key: dumped[key] for key in self._laid if key in dumped}, **dumped}


class PipelineParams(FrozenModel):
    """The chain's ``steps`` (``None`` where none is named) beside each node's laid config, dumped
    as that document: the wire sends it and every key hashes it; empty is the one "carries none".
    Built only by reading that document, so whatever it dumps reads back; no node takes a field's
    name (`pipeline_schema.py::PipelineSchema` refuses it)."""

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
        return {node: cfg.laid_keys() for node, cfg in self.nodes.items()}

    def __bool__(self) -> bool:
        return self.steps is not None or bool(self.nodes)

    def node(self, name: str) -> NodeCall:
        return self.nodes.get(name) or NodeCall()

    def with_steps(self, steps: list[str] | None) -> Self:
        laid = {} if steps is None else {STEPS_KEY: list(steps)}
        return self.model_validate({**laid, **self.nodes})

    def with_nodes(self, nodes: Mapping[str, NodeCall | Mapping[str, object]]) -> Self:
        """*nodes* laid as the chain's configs: a key of the wrong type refuses, located."""
        typed = {
            n: PayloadInvalidError.validating(NodeCall, c, under=(n,)) for n, c in nodes.items()
        }
        return self.model_validate({**self._laid_steps, **typed})

    def overlaid(self, overlay: Mapping[str, Mapping[str, Any]], schema: PipelineSchema) -> Self:
        """*overlay* laid over its nodes, a node *schema* does not declare dropped."""
        if not overlay:
            return self
        declared = {n.name for n in schema.declared_nodes}
        nodes = apply_node_overlay(self.laid_nodes, overlay, schema)
        for node in nodes.keys() - declared:
            logger.warning("Dropping LLM override for undeclared node %r", node)
        return self.with_nodes({n: c for n, c in nodes.items() if n in declared})


# A node never takes the name of a carrier field: the laid document would read it as that field.
RESERVED_NODE_NAMES = frozenset(PipelineParams.model_fields)


def apply_node_overlay(
    base: Mapping[str, Mapping[str, Any]],
    overlay: Mapping[str, Mapping[str, Any]],
    schema: PipelineSchema | None,
    *,
    source: ParamSource | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, dict[str, Any]]:
    merged = {node: dict(cfg) for node, cfg in base.items()}
    for node, cfg in overlay.items():
        if (existing := merged.get(node)) is None:
            merged[node] = dict(cfg)
        else:
            node_obj = schema.get_node(node) if schema else None
            param_types = node_obj.param_types if node_obj else {}
            node_cfg = {**existing, **cfg}
            # A route names ONE model's hosts: a new model without one takes its own default.
            if "route_order" not in cfg and cfg.get("model", existing.get("model")) != existing.get(
                "model"
            ):
                node_cfg.pop("route_order", None)
            for param, incoming in cfg.items():
                prior = existing.get(param)
                if (
                    param_types.get(param) == "object"
                    and isinstance(prior, dict)
                    and isinstance(incoming, dict)
                ):
                    node_cfg[param] = {**prior, **incoming}
            merged[node] = node_cfg
        if provenance is not None and source is not None:
            provenance.setdefault(node, {}).update(dict.fromkeys(cfg, source))
    return merged
