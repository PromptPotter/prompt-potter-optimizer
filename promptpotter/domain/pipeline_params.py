"""The target pipeline's params, typed: parsed once off the laid document, dumped back to it whole."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, GetJsonSchemaHandler, model_serializer, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from promptpotter.domain.strict_model import FrozenModel

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from promptpotter.domain.pipeline_schema import ParamSource, PipelineSchema

logger = logging.getLogger(__name__)

# Where the laid document names the chain beside its nodes: the wire's spelling, and the dump's.
STEPS_KEY = "steps"


class PipelineParams(FrozenModel):
    """The chain's ``steps`` (``None`` where none is named) beside each node's laid config, dumped
    as that document: the wire sends it and every key hashes it; empty is the one "carries none".
    Built off it (`model_validate`) or by `with_steps` / `with_nodes`, never by field name."""

    steps: list[str] | None = None
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)

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
        steps = {} if self.steps is None else {STEPS_KEY: list(self.steps)}
        return {**steps, **{node: dict(cfg) for node, cfg in self.nodes.items()}}

    def __bool__(self) -> bool:
        return self.steps is not None or bool(self.nodes)

    def node(self, name: str) -> dict[str, Any]:
        return self.nodes.get(name) or {}

    def with_steps(self, steps: list[str] | None) -> Self:
        return self.model_copy(update={"steps": None if steps is None else list(steps)})

    def with_nodes(self, nodes: Mapping[str, Mapping[str, Any]]) -> Self:
        return self.model_copy(update={"nodes": {n: dict(c) for n, c in nodes.items()}})

    def overlaid(self, overlay: Mapping[str, Mapping[str, Any]], schema: PipelineSchema) -> Self:
        """*overlay* laid over its nodes, a node *schema* does not declare dropped."""
        if not overlay:
            return self
        declared = {n.name for n in schema.declared_nodes}
        nodes = apply_node_overlay(self.nodes, overlay, schema)
        for node in nodes.keys() - declared:
            logger.warning("Dropping LLM override for undeclared node %r", node)
        return self.with_nodes({n: c for n, c in nodes.items() if n in declared})


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
