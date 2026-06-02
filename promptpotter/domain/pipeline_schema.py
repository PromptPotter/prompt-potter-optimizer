"""PipelineSchema — backend-agnostic LLM-pipeline description."""

import enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS


def stable_hash(value: Any) -> str:
    """Deterministic 16-char hex digest of an arbitrary JSON-able value."""
    blob = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class NodeType(enum.StrEnum):
    """Pipeline node type classification (empty string = untyped)."""

    NONE = ""
    CANDIDATE_SOURCE = "candidate_source"
    RANKER = "ranker"
    ENRICHER = "enricher"
    CACHE = "cache"


class ObservationMapping(BaseModel):
    """Maps one trace observation field to a pipeline_data key."""

    model_config = {"frozen": True}

    pipeline_key: str
    output_field: str | None = None
    is_llm: bool = False


class NodeOutputSchema(BaseModel):
    """Resolved output schema for a pipeline node."""

    model_config = {"frozen": True}

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class NodePromptMeta(BaseModel):
    """Resolved prompt metadata for a pipeline node."""

    model_config = {"frozen": True}

    family: str = ""
    template_variables: list[str] = Field(default_factory=list)
    description: str = ""


class PipelineViewNode(BaseModel):
    """Webapp pipeline-graph node."""

    model_config = {"frozen": True}

    id: str
    label: str
    kind: str = ""  # "io" | "llm" | "tool" | "retriever" | "cache" | "measurement" | "phase"


class PipelineViewEdge(BaseModel):
    """Webapp pipeline-graph edge."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: str = "forward"  # "forward" | "loop" | "directive" | "escalate"
    label: str = ""


class PipelineView(BaseModel):
    """Webapp-facing graph projection — ``datasets/_optimizer/pipeline.json::view`` or derived."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    nodes: list[PipelineViewNode] = Field(default_factory=list)
    edges: list[PipelineViewEdge] = Field(default_factory=list)


class PipelineNode(BaseModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = {"frozen": True}

    name: str
    wire_type: str = ""
    short_circuit: bool = False
    node_type: NodeType = NodeType.NONE
    param_keys: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)
    # JSON-schema type per param — drives structured-output constraint + validate_overrides
    # checks; without it, L1 may emit stringified numbers that break wire payloads.
    param_types: dict[str, str] = Field(default_factory=dict)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    langfuse_type: str = "span"  # "generation" | "tool" | "retriever" | "span"
    output_schema: NodeOutputSchema | None = None
    prompt_meta: NodePromptMeta | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)

    @property
    def output_keys(self) -> list[str]:
        """Pipeline_data keys this node writes (derived from observation_mappings)."""
        return [m.pipeline_key for m in self.observation_mappings]

    @property
    def is_llm(self) -> bool:
        """Whether this node makes an LLM call (any mapping has ``is_llm``)."""
        return any(m.is_llm for m in self.observation_mappings)


class PipelineSchema(BaseModel):
    """Frozen, backend-agnostic pipeline description; SoT for identity at campaign start."""

    model_config = {"frozen": True}

    name: str = ""
    version: str = ""
    description: str = ""
    nodes: list[PipelineNode] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)
    view: PipelineView | None = None

    _node_map: dict[str, int] = PrivateAttr(default_factory=dict)
    _observation_keys: frozenset[str] = PrivateAttr(default_factory=frozenset)

    def model_post_init(self, __context: Any) -> None:
        self._node_map = {n.name: i for i, n in enumerate(self.nodes)}
        self._observation_keys = frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )

    @property
    def active_steps(self) -> tuple[str, ...]:
        """Node names in pipeline order."""
        return tuple(n.name for n in self.nodes)

    @property
    def observation_keys(self) -> frozenset[str]:
        """``pipeline_data`` keys written by all nodes' observation mappings."""
        return self._observation_keys

    def to_pipeline_params(self) -> dict[str, Any]:
        """``{"steps": [...]}`` sparse scaffold — backend merges per-node config defaults."""
        return {"steps": list(self.active_steps)}

    def optimizer_locks(self, forbidden_strict: bool) -> dict[str, Any]:
        """The optimizer permission surface for a *minted* pipeline — same wire shape
        as ``application/jobs/launcher.py::derive_optimizer_locks`` (the draft-side sibling).

        Read straight off the committed schema: ``pipeline`` is the active step order,
        each node carries its ``config`` floor + the ``param_allowed_values`` the optimizer
        may permute. ``forbidden_strict`` is the campaign-level
        ``optimization.forbidden_axes_strict`` policy (NOT derivable from the pipeline —
        a node may declare ``model`` in ``param_keys`` as a capability while the campaign
        still pins it); the caller reads it from the dataset's ``campaign.json``. The two
        derivations must stay shape-consistent — change them together.
        """
        return {
            "pipeline": list(self.active_steps),
            "forbidden_axes": sorted(PARAM_FORBIDDEN_KEYS) if forbidden_strict else [],
            "nodes": {
                n.name: {
                    "config": dict(n.current_config),
                    "param_allowed_values": {k: list(v) for k, v in n.param_allowed_values.items()},
                }
                for n in self.nodes
            },
        }

    def has_node(self, name: str) -> bool:
        return name in self._node_map

    def get_node(self, name: str) -> PipelineNode | None:
        idx = self._node_map.get(name)
        return self.nodes[idx] if idx is not None else None

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        active = set(steps)
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name in active]},
        )

    def exclude(self, names: set[str] | None) -> "PipelineSchema":
        if not names:
            return self
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name not in names]},
        )

    def node_configs(self, pipeline_params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Canonical SearchPoint identity: ordered ``[(node, config), ...]`` for hashing."""
        result: list[tuple[str, dict[str, Any]]] = []
        for node in self.nodes:
            cfg = pipeline_params.get(node.name, {})
            if not isinstance(cfg, dict):
                cfg = {}
            result.append((node.name, cfg))
        return result

    def sp_hash(self, pipeline_params: dict[str, Any]) -> str:
        configs = self.node_configs(pipeline_params)
        return stable_hash(configs) if configs else ""

    def node_param_keys(self) -> dict[str, set[str]]:
        return {step.name: step.param_keys for step in self.nodes if step.param_keys}

    def obs_extraction_map(self) -> dict[str, list[ObservationMapping]]:
        return {
            step.observation_name: step.observation_mappings
            for step in self.nodes
            if step.observation_name and step.observation_mappings
        }

    def prompt_node_names(self) -> list[str]:
        """Node names whose output is affected by the prompt — ``prompt_meta`` set."""
        return [node.name for node in self.nodes if node.prompt_meta is not None]


__all__ = [
    "NodeOutputSchema",
    "NodePromptMeta",
    "NodeType",
    "ObservationMapping",
    "PipelineNode",
    "PipelineSchema",
    "PipelineView",
    "PipelineViewEdge",
    "PipelineViewNode",
    "stable_hash",
]
