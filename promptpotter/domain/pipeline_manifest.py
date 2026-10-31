from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, TypeAdapter

from promptpotter.domain.opt_search_point import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.strict_model import FrozenModel


class ManifestNode(FrozenModel):
    type: NodeKind
    description: str | None = None
    # What a proposer's arms are, in plain words, as a setup surface names them; prose, so no treatment reads it.
    makes: str | None = None
    # The backend-shaped menu of the node's searchable params (`pipeline_parsing.py`).
    optimizer: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineManifest(FrozenModel):
    """A `pipeline.yaml` typed at load — an optimizer's, or the bench's check-in."""

    name: str
    version: str
    paper: str | None = None
    available_models: list[str]
    nodes: dict[str, ManifestNode]
    pipelines: dict[str, list[str]]
    # `node.knob` refs a setup surface leads with, in order; presentation, so no treatment reads it.
    main: list[str]
    # Whether `initial_population` is a round of its own or heads round 1 (`RoundPlan.walk`).
    initial_population_walks: Literal["alone", "before_default"] | None = None
    resolved_prompts: dict[str, OptimizerPromptTemplate] = Field(default_factory=dict)

    def laid_over(self, overlay: Mapping[str, Mapping[str, Any]]) -> PipelineManifest:
        nodes = {
            name: node.model_copy(update={"config": {**node.config, **overlay[name]}})
            if name in overlay
            else node
            for name, node in self.nodes.items()
        }
        return self.model_copy(update={"nodes": nodes})


class ManifestSwap(FrozenModel):
    # Takes the swapped node's place in every walk, as manifest `of` declares and configures it.
    node: str
    of: str


class ManifestSwitch(FrozenModel):
    """A switch: the whole manifest ``extends`` names, each node ``swap`` names replaced by another manifest's node of its type."""

    name: str
    version: str
    paper: str | None = None
    extends: str
    swap: dict[str, ManifestSwap]
    # Its own, never what it extends: a swapped node takes its knobs with it.
    main: list[str]


AUTHORED_MANIFEST: TypeAdapter[PipelineManifest | ManifestSwitch] = TypeAdapter(
    PipelineManifest | ManifestSwitch
)
CHECKIN_MANIFEST = TypeAdapter(PipelineManifest)
