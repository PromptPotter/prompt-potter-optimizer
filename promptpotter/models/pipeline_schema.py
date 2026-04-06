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
from typing import Any

from pydantic import BaseModel, Field


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

    Mirrors the semantics of ``_ObsField`` in ``eval_dataset.py``:
    - ``pipeline_key``: key written into the eval pipeline_data dict
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
    """

    model_config = {"frozen": True}

    name: str = ""
    version: str = ""
    description: str = ""
    nodes: list[PipelineNode] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)

    # -------------------------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------------------------

    def get_node(self, name: str) -> PipelineNode | None:
        """Find a node by name, or None if not found."""
        for step in self.nodes:
            if step.name == name:
                return step
        return None

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        """Return a copy with only nodes present in *steps*."""
        active = set(steps)
        return PipelineSchema(
            name=self.name,
            version=self.version,
            description=self.description,
            nodes=[n for n in self.nodes if n.name in active],
        )

    def node_for_param(self, param_name: str) -> str | None:
        """Return the node name that owns *param_name*, or None."""
        for step in self.nodes:
            if param_name in step.param_keys:
                return step.name
        return None

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

        Used by ``_extract_eval_from_traces()`` in ``eval_dataset.py``.
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

    def upstream_of(self, node_name: str) -> list[str]:
        """Node names strictly before *node_name* in pipeline order.

        Used by partial pipeline caching to determine which node outputs
        are stable when *node_name*'s config changes.
        """
        result: list[str] = []
        for node in self.nodes:
            if node.name == node_name:
                break
            result.append(node.name)
        return result

    def split_at_ranker(self) -> tuple[list[str], list[str]]:
        """Split node names into (upstream, ranker_and_downstream).

        The first node with ``node_type == "ranker"`` marks the split point.
        Used by partial pipeline caching (Wave 4) to compute upstream config
        hashes.  When no ranker exists, all nodes are upstream.
        """
        upstream: list[str] = []
        downstream: list[str] = []
        found_ranker = False
        for node in self.nodes:
            if not found_ranker and node.node_type == NodeType.RANKER:
                found_ranker = True
            if found_ranker:
                downstream.append(node.name)
            else:
                upstream.append(node.name)
        return upstream, downstream

    def candidate_output_key(self, steps: list[str] | None = None) -> str | None:
        """Pipeline_data key for the last candidate-producing node in *steps*.

        Walks nodes in pipeline order, filters to *steps* (all if None),
        finds the last node with node_type candidate_source or ranker,
        and returns its first output_keys entry.  Returns None if no
        candidate-producing node exists.
        """
        active = set(steps) if steps else None
        last_key: str | None = None
        for node in self.nodes:
            if active is not None and node.name not in active:
                continue
            if node.node_type in (NodeType.CANDIDATE_SOURCE, NodeType.RANKER):
                keys = node.output_keys
                if keys:
                    last_key = keys[0]
        return last_key

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


def is_result_step_compatible(
    result: dict,
    target_nodes: set[str] | list[str],
) -> bool:
    """Tag whether a historical result's prediction matches what target config would produce.

    True when terminated_at is in target_nodes (the result never reached
    a step absent from the target config). False when terminated_at is
    missing or outside target_nodes. Used for annotation, not filtering.
    """
    pd = result.get("pipeline_data") or {}
    terminated_at = pd.get("terminated_at")
    if terminated_at is None:
        return False
    target = target_nodes if isinstance(target_nodes, set) else set(target_nodes)
    return terminated_at in target


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
            )
        )
    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )
