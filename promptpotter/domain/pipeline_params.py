"""The target pipeline's params, typed: parsed once off the laid document, dumped back to it whole."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import (
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PrivateAttr,
    ValidatorFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from promptpotter.domain.strict_model import FrozenModel

# Where the laid document names the chain beside its nodes: the wire's spelling, and the dump's.
STEPS_KEY = "steps"


class _LaidDocument(FrozenModel):
    """Served, stored and hashed as the document it was parsed off: its schema is that object's."""

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return handler(core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema()))


class NodeParams(_LaidDocument):
    """One node's laid config: the engine's keys typed as laid (strict, so a value of the wrong
    type refuses and an int stays the int it was hashed as), and the backend's own params one
    dict. Built off the laid mapping alone (`parse`); its dump is that mapping."""

    model_config = ConfigDict(strict=True)

    provider: str | None = None
    model: str | None = None
    route_order: list[str] | None = None
    temperature: int | float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    top_p: int | float | None = None
    seed: int | None = None
    # The RENDER of its prompt loci, never configuration: persisted, it goes stale.
    prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # The laid key order, so the dump IS the mapping it was parsed off: a rendering reads it in order.
    _order: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="wrap")
    @classmethod
    def _split(cls, laid: object, handler: ValidatorFunctionWrapHandler) -> NodeParams:
        if isinstance(laid, NodeParams):
            return laid
        if not isinstance(laid, Mapping):
            raise ValueError(f"a node's config is a mapping of its params, not {laid!r}")
        engine = {k: v for k, v in laid.items() if k in _ENGINE_KEYS}
        parsed = handler(
            {**engine, "params": {k: v for k, v in laid.items() if k not in _ENGINE_KEYS}}
        )
        assert isinstance(parsed, NodeParams)
        parsed._order = tuple(laid)
        return parsed

    @model_serializer(mode="plain")
    def dump(self) -> dict[str, Any]:
        """The laid mapping, in the order it was laid."""
        engine = {k: getattr(self, k) for k in _ENGINE_KEYS if k in self.model_fields_set}
        laid = {**engine, **self.params}
        return {k: laid[k] for k in (*self._order, *laid) if k in laid}

    def __eq__(self, other: object) -> bool:
        """As the mappings compare: the order a key was laid in is no part of the config."""
        return isinstance(other, NodeParams) and self.dump() == other.dump()

    __hash__ = None  # type: ignore[assignment]

    @classmethod
    def parse(cls, laid: Mapping[str, Any]) -> NodeParams:
        return cls.model_validate(laid)

    def __contains__(self, key: str) -> bool:
        return key in self.model_fields_set if key in _ENGINE_KEYS else key in self.params

    def value(self, key: str, default: object = None) -> Any:
        """A param by its laid name, for a walk over every key; an engine key reads its field."""
        if key in _ENGINE_KEYS:
            return getattr(self, key) if key in self.model_fields_set else default
        return self.params.get(key, default)

    def keys(self) -> list[str]:
        return list(self.dump())

    def laid(self, update: Mapping[str, Any]) -> NodeParams:
        """*update* laid over it key by key, typed as a laid config is."""
        return NodeParams.parse({**self.dump(), **update})

    def without(self, *keys: str) -> NodeParams:
        return NodeParams.parse({k: v for k, v in self.dump().items() if k not in keys})

    def config(self) -> NodeParams:
        """Its configuration: everything less the rendered prompt."""
        return self.without("prompt") if "prompt" in self else self


_ENGINE_KEYS: tuple[str, ...] = tuple(k for k in NodeParams.model_fields if k != "params")


class PipelineParams(_LaidDocument):
    """What a pipeline runs with: the chain's ``steps`` (``None`` where none is named) and each
    node's :class:`NodeParams`. Its dump is the laid document — ``steps`` beside the nodes — which
    the wire sends and every key hashes; empty is the one spelling of "carries none"."""

    steps: list[str] | None = None
    nodes: dict[str, NodeParams] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split(cls, laid: object) -> object:
        if isinstance(laid, PipelineParams):
            return laid
        if laid is None:
            return {}
        if not isinstance(laid, Mapping):
            raise ValueError(f"pipeline params are a mapping by node, not {laid!r}")
        flat = sorted(
            k for k, v in laid.items() if k != STEPS_KEY and not isinstance(v, Mapping | NodeParams)
        )
        if flat:
            raise ValueError(
                f"pipeline_params must be nested-by-node — keys {flat} carry "
                "scalar values. Expected {node: {param: value}}, not a flat map."
            )
        out: dict[str, Any] = {"nodes": {k: v for k, v in laid.items() if k != STEPS_KEY}}
        if STEPS_KEY in laid:
            out["steps"] = laid[STEPS_KEY]
        return out

    @model_serializer(mode="plain")
    def dump(self) -> dict[str, Any]:
        steps = {} if self.steps is None else {STEPS_KEY: list(self.steps)}
        return {**steps, **{node: cfg.dump() for node, cfg in self.nodes.items()}}

    @classmethod
    def parse(cls, laid: Mapping[str, Any] | None) -> PipelineParams:
        return cls.model_validate(laid or {})

    def __bool__(self) -> bool:
        return self.steps is not None or bool(self.nodes)

    def laid_nodes(self) -> dict[str, dict[str, Any]]:
        """Each node's laid config, the chain left out: the shape a node overlay takes."""
        return {node: cfg.dump() for node, cfg in self.nodes.items()}

    def node(self, name: str) -> NodeParams:
        """*name*'s config; an empty one where it sets none."""
        return self.nodes.get(name) or NodeParams()

    def with_steps(self, steps: list[str] | None) -> Self:
        return self.model_copy(update={"steps": None if steps is None else list(steps)})

    def with_nodes(self, nodes: Mapping[str, NodeParams]) -> Self:
        return self.model_copy(update={"nodes": dict(nodes)})

    def with_node(self, name: str, cfg: NodeParams) -> Self:
        return self.with_nodes({**self.nodes, name: cfg})

    def without_prompts(self) -> Self:
        """Each node's configuration, its rendered ``prompt`` dropped."""
        return self.with_nodes({node: cfg.config() for node, cfg in self.nodes.items()})


EMPTY_PARAMS = PipelineParams()
