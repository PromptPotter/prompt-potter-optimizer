"""PipelineSchema — backend-agnostic description of an LLM pipeline.

Describes nodes, parameters, observation mappings, and metadata so
PromptPotter services can stay generic instead of hardcoding backend-specific constants.
"""

import enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


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
    """Resolved output schema for a pipeline node (field names + descriptions from the backend's registry)."""

    model_config = {"frozen": True}

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict = Field(default_factory=dict)


class NodePromptMeta(BaseModel):
    """Resolved prompt metadata for a pipeline node.

    Carries template variable names, a human description, and the full
    prompt template text from the backend's prompt registry.
    """

    model_config = {"frozen": True}

    family: str = ""
    template_variables: list[str] = Field(default_factory=list)
    description: str = ""


class PipelineNode(BaseModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = {"frozen": True}

    name: str
    wire_type: str = ""  # Raw type from connector (e.g., "generation", "retriever")
    short_circuit: bool = False
    node_type: NodeType = NodeType.NONE
    param_keys: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
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
    """Full description of a backend pipeline.

    Carries enough information for all PromptPotter services to operate
    generically: evaluation, sensitivity scan, observability, and Langfuse push.

    After ``configure_pipeline()`` filters and applies overrides, the schema
    is the single source of truth for pipeline identity at campaign start:
    active nodes (via ``nodes``), prompt node (derivable), and initial
    per-node config (baked into ``PipelineNode.current_config``).

    Nodes form the coordinate system for each dataset.  Indexed lookups
    (``node_position``, ``get_node``, ``node_for_param``) are O(1).
    ``exclude()`` / ``filter_to_steps()`` slice valid sub-pipelines.
    """

    model_config = {"frozen": True}

    name: str = ""
    version: str = ""
    description: str = ""
    nodes: list[PipelineNode] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "_node_map", {n.name: i for i, n in enumerate(self.nodes)})
        object.__setattr__(
            self, "_param_map", {p: n.name for n in self.nodes for p in n.param_keys}
        )
        obs_keys: frozenset[str] = frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )
        object.__setattr__(self, "_observation_keys", obs_keys)

    # -------------------------------------------------------------------
    # Pipeline identity — derived from nodes
    # -------------------------------------------------------------------

    @property
    def active_steps(self) -> tuple[str, ...]:
        """Node names in pipeline order (schema is pre-filtered to active nodes)."""
        return tuple(n.name for n in self.nodes)

    @property
    def observation_keys(self) -> frozenset[str]:
        """Pipeline_data keys written by every node's observation mappings.

        Derived once in ``model_post_init`` — schemas are frozen.  Consumed
        by ``sample_measurement`` when projecting wire-response fields into
        a ``QueryResult``'s ``pipeline_data`` dict.
        """
        return self._observation_keys  # type: ignore[attr-defined]

    def to_pipeline_params(self) -> dict:
        """Build initial pipeline_params dict from this schema.

        Produces the wire-format dict with ``steps`` + per-node config,
        suitable for ``JobSearchPoint`` construction.
        """
        pp: dict = {"steps": list(self.active_steps)}
        for node in self.nodes:
            if node.current_config:
                pp[node.name] = dict(node.current_config)
        return pp

    def with_overrides(self, overrides: dict[str, dict]) -> "PipelineSchema":
        """Return a new schema with overrides applied to node ``current_config``.

        Since PipelineSchema is frozen, reconstructs with updated nodes.
        Only applies overrides for nodes present in the schema.
        """
        updated_nodes: list[PipelineNode] = []
        for node in self.nodes:
            if node.name in overrides:
                merged = {**node.current_config, **overrides[node.name]}
                updated_nodes.append(node.model_copy(update={"current_config": merged}))
            else:
                updated_nodes.append(node)
        return self.model_copy(update={"nodes": updated_nodes})

    # -------------------------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------------------------

    def has_node(self, name: str) -> bool:
        """Whether *name* is a node in this schema (O(1))."""
        return name in self._node_map  # type: ignore[attr-defined]

    def node_position(self, name: str) -> int:
        """Pipeline position of *name*.  Raises ``KeyError`` if unknown."""
        return self._node_map[name]  # type: ignore[attr-defined]

    def get_node(self, name: str) -> PipelineNode | None:
        """Find a node by name (O(1)), or None if not found."""
        idx = self._node_map.get(name)  # type: ignore[attr-defined]
        return self.nodes[idx] if idx is not None else None

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        """Return a copy with only nodes present in *steps*, preserving schema order."""
        active = set(steps)
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name in active]},
        )

    def exclude(self, names: set[str] | None) -> "PipelineSchema":
        """Return a copy without the named nodes.  ``None`` / empty → self."""
        if not names:
            return self
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name not in names]},
        )

    def node_for_param(self, param_name: str) -> str | None:
        """Return the node name that owns *param_name* (O(1)), or None."""
        return self._param_map.get(param_name)  # type: ignore[attr-defined]

    def node_configs(self, pipeline_params: dict[str, Any]) -> list[tuple[str, dict]]:
        """Ordered ``[(node_name, node_config), ...]`` for this schema.

        The canonical SearchPoint identity at the archive layer: hashed
        by ``sp_hash`` and compared element-wise by
        ``DatasetRunStore.find_by_node_configs``.
        """
        result: list[tuple[str, dict]] = []
        for node in self.nodes:
            cfg = pipeline_params.get(node.name, {})
            if not isinstance(cfg, dict):
                cfg = {}
            result.append((node.name, cfg))
        return result

    def sp_hash(self, pipeline_params: dict[str, Any]) -> str:
        """SearchPoint identity hash.  Empty string for empty schemas."""
        configs = self.node_configs(pipeline_params)
        return stable_hash(configs) if configs else ""

    # -------------------------------------------------------------------
    # Derivation methods
    # -------------------------------------------------------------------

    def node_param_keys(self) -> dict[str, set[str]]:
        """Map step name → parameter keys."""
        return {step.name: step.param_keys for step in self.nodes if step.param_keys}

    def obs_extraction_map(self) -> dict[str, list[ObservationMapping]]:
        """Map observation name → extraction rules.

        Used by ``_extract_dataset_from_traces()`` in ``context.py``.
        """
        return {
            step.observation_name: step.observation_mappings
            for step in self.nodes
            if step.observation_name and step.observation_mappings
        }

    def prompt_node_names(self) -> list[str]:
        """Node names whose output is affected by the prompt text.

        A node is prompt-bearing if it has ``prompt_meta`` set.
        Returns empty list when no nodes match — callers must handle this
        (empty means prompt doesn't affect pipeline output).
        """
        return [node.name for node in self.nodes if node.prompt_meta is not None]

    def required_pipeline_key(self) -> str:
        """The pipeline_data key that gates trace validity.

        Returns the first observation mapping's pipeline_key from the first
        LLM node, or ``""`` when no LLM mappings exist.
        """
        for node in self.nodes:
            for mapping in node.observation_mappings:
                if mapping.is_llm:
                    return mapping.pipeline_key
        return ""
