"""
PipelineSchema — backend-agnostic description of an LLM pipeline.

Describes the nodes, parameters, observation mappings, and metadata of
a backend pipeline so that PromptPotter services can work generically
instead of hardcoding backend-specific constants.

Derivation methods:
  node_param_keys()       → node name → param keys
  obs_extraction_map()    → observation name → extraction rules
  langfuse_type_map()     → node name → Langfuse as_type
"""

import enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


def node_cache_key(
    node_name: str,
    node_config: dict[str, Any],
    upstream_hash: str = "",
) -> str:
    """Deterministic 16-char hex key for one node's output.

    Chains upstream: *upstream_hash* is the previous node's cache key.
    Changing any upstream node's config cascades invalidation downstream.
    """
    blob = json.dumps(
        {"node": node_name, "config": node_config, "upstream": upstream_hash},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class NodeRuntime(enum.StrEnum):
    """Where a pipeline node executes."""

    BACKEND = "backend"
    FRONTEND = "frontend"


class NodeType(enum.StrEnum):
    """Pipeline node type classification (empty string = untyped)."""

    NONE = ""
    CANDIDATE_SOURCE = "candidate_source"
    RANKER = "ranker"
    ENRICHER = "enricher"
    CACHE = "cache"


class ObservationMapping(BaseModel):
    """Maps one trace observation field to a pipeline_data key.

    Mirrors the semantics of ``_ObsField`` in ``context.py``:
    - ``pipeline_key``: key written into the pipeline_data dict
    - ``output_field``: sub-key to extract from observation output (None → full dict)
    - ``is_llm``: whether to also extract model info from observation metadata
    """

    model_config = {"frozen": True}

    pipeline_key: str
    output_field: str | None = None
    is_llm: bool = False


class NodeOutputSchema(BaseModel):
    """Resolved output schema for a pipeline node.

    Carries the field names and descriptions from the backend's schema registry
    so that downstream consumers (scan advisor, notebooks) can reason about
    what a node produces without calling the registry directly.
    """

    model_config = {"frozen": True}

    family: str = ""
    version: int | None = None
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
    version: int | None = None
    template_variables: list[str] = Field(default_factory=list)
    description: str = ""


WIRE_TYPE_TAGS: dict[str, str] = {
    "generation": "ai",
    "retriever": "retr",
    "tool": "tool",
    "cache": "cach",
}


class PipelineNode(BaseModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = {"frozen": True}

    name: str
    wire_type: str = ""  # Raw type from connector (e.g., "generation", "retriever")
    display_tag: str = ""  # Optional short display tag override from connector config
    runtime: NodeRuntime = NodeRuntime.BACKEND
    short_circuit: bool = False
    node_type: NodeType = NodeType.NONE
    param_keys: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
    input_keys: list[str] = Field(default_factory=list)
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


class IntermediateMetric(BaseModel):
    """A metric derived from a pipeline node's type."""

    model_config = {"frozen": True}

    name: str
    node_type: str
    pipeline_data_key: str
    description: str = ""
    default_weight: float = 0.0  # 0 = display-only


NODE_TYPE_METRICS: dict[str, list[IntermediateMetric]] = {
    "candidate_source": [
        IntermediateMetric(
            name="source_recall",
            node_type="candidate_source",
            pipeline_data_key="candidate_ranking",
            description="Fraction of queries where ground truth appears in candidate list",
        ),
    ],
    "ranker": [
        IntermediateMetric(
            name="candidate_recall",
            node_type="ranker",
            pipeline_data_key="final_ranking",
            description="Fraction of LLM-ranked queries where ground truth was available",
        ),
    ],
    "cache": [
        IntermediateMetric(
            name="cache_hit_rate",
            node_type="cache",
            pipeline_data_key="step_timings",
            description="Fraction of queries resolved by cache",
        ),
    ],
    "enricher": [],
}


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
    ``prefix_through`` slices a valid pipeline prefix for cache-hit / partial-
    execution workflows.
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

    # -------------------------------------------------------------------
    # Pipeline identity — derived from nodes
    # -------------------------------------------------------------------

    @property
    def active_steps(self) -> tuple[str, ...]:
        """Node names in pipeline order (schema is pre-filtered to active nodes)."""
        return tuple(n.name for n in self.nodes)

    @property
    def prompt_node(self) -> str:
        """First prompt-bearing node, or ``""`` if none."""
        names = self.prompt_node_names()
        return names[0] if names else ""

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

    @property
    def all_param_keys(self) -> set[str]:
        """Union of every node's ``param_keys``."""
        return set(self._param_map)  # type: ignore[attr-defined]

    @property
    def has_prompt_nodes(self) -> bool:
        """Whether any node has ``prompt_meta`` set."""
        return any(n.prompt_meta is not None for n in self.nodes)

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

    def prefix_through(self, node_name: str) -> "PipelineSchema":
        """Schema containing ``nodes[0:position+1]`` — a valid pipeline prefix."""
        idx = self._node_map[node_name]  # type: ignore[attr-defined]
        return self.model_copy(update={"nodes": list(self.nodes[: idx + 1])})

    def node_for_param(self, param_name: str) -> str | None:
        """Return the node name that owns *param_name* (O(1)), or None."""
        return self._param_map.get(param_name)  # type: ignore[attr-defined]

    def prefix_keys(self, pipeline_params: dict[str, Any]) -> list[tuple[str, str]]:
        """Chained cache keys for each node in pipeline order.

        Returns ``[(node_name, cache_key), ...]``.  Each key is a 16-char
        hex digest of ``(node_name, node_config, upstream_key)``, so changing
        any upstream node's config cascades invalidation downstream.

        This is the single addressing scheme for all caching: the
        intermediate cache uses individual entries for per-node output
        lookup; the terminal element serves as ``sp_hash`` for dataset-run
        lookup.
        """
        result: list[tuple[str, str]] = []
        upstream = ""
        for node in self.nodes:
            config = pipeline_params.get(node.name, {})
            if not isinstance(config, dict):
                config = {}
            key = node_cache_key(node.name, config, upstream)
            result.append((node.name, key))
            upstream = key
        return result

    def sp_hash(self, pipeline_params: dict[str, Any]) -> str:
        """SearchPoint identity hash — terminal element of the node chain.

        Equivalent to ``self.prefix_keys(pipeline_params)[-1][1]``.
        Returns ``""`` for empty schemas (no nodes).
        """
        chain = self.prefix_keys(pipeline_params)
        return chain[-1][1] if chain else ""

    def excluded_nodes(self, pipeline_params: dict[str, Any]) -> set[str]:
        """Nodes in this schema but absent from ``pipeline_params["steps"]``.

        Returns empty set when ``steps`` is missing (all nodes assumed active).
        """
        steps = pipeline_params.get("steps")
        if steps is None:
            return set()
        return set(self.active_steps) - set(steps)

    def validate_step_dependencies(self, steps: list[str]) -> list[str]:
        """Return node names in *steps* whose ``input_keys`` aren't satisfied.

        ``input_keys`` are *data* keys (e.g. ``entity_profile``), so we track
        both node names and the data keys each node produces (from
        ``observation_mappings``).
        """
        available: set[str] = set()
        violations: list[str] = []
        for name in steps:
            node = self.get_node(name)
            if node and node.input_keys and any(k not in available for k in node.input_keys):
                violations.append(name)
            available.add(name)
            if node:
                available.update(node.output_keys)
        return violations

    def build_display_tags(self) -> dict[str, str]:
        """Compute display tag map ``{node_name: tag}`` with auto-enumeration.

        Resolution: node.display_tag → WIRE_TYPE_TAGS[wire_type] → name[:4].
        When multiple nodes resolve to the same base tag, append ``_1``, ``_2``, ...
        """
        from collections import Counter

        # Resolve base tag per node
        base_tags: list[tuple[str, str]] = []  # [(name, base_tag), ...]
        for node in self.nodes:
            tag = node.display_tag or WIRE_TYPE_TAGS.get(node.wire_type, "") or node.name[:4]
            base_tags.append((node.name, tag))

        # Count occurrences
        tag_counts = Counter(tag for _, tag in base_tags)

        # Enumerate duplicates
        tag_seq: dict[str, int] = {}
        result: dict[str, str] = {}
        for name, tag in base_tags:
            if tag_counts[tag] > 1:
                tag_seq[tag] = tag_seq.get(tag, 0) + 1
                result[name] = f"{tag}_{tag_seq[tag]}"
            else:
                result[name] = tag
        return result

    def infer_terminating_node(self, step_timings: dict[str, float | None]) -> str | None:
        """Infer which pipeline step produced the final result.

        Walks steps in pipeline order. The last step with a non-None timing
        is the one that produced the result.
        """
        last_executed: str | None = None
        for step in self.nodes:
            if step_timings.get(step.name) is not None:
                last_executed = step.name
        return last_executed

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

    def langfuse_type_map(self) -> dict[str, str]:
        """Map step name → Langfuse ``as_type``.

        Replaces the implicit mapping in ``pipeline_nodes.py``.
        """
        return {step.name: step.langfuse_type for step in self.nodes}

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


def load_pipeline_from_dict(data: dict) -> PipelineSchema:
    """Load a PipelineSchema from a raw dict (e.g., optimizer_pipeline.json).

    Accepts the standard ``{nodes: {...}, pipelines: {...}}`` format used
    by both the target backend and the optimizer pipeline declarations.
    """
    nodes = []
    for name, node_data in data.get("nodes", {}).items():
        opt = node_data.get("optimizer", {})
        pk = set(opt.get("param_keys", []))
        nodes.append(
            PipelineNode(
                name=name,
                current_config=node_data.get("config", {}),
                param_keys=pk,
                input_keys=opt.get("input_keys", []),
            )
        )
    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )
