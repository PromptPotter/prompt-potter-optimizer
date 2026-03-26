"""
PipelineSchema — backend-agnostic description of an LLM pipeline.

Describes the nodes, parameters, observation mappings, and metadata of
a backend pipeline so that PromptPotter services can work generically
instead of hardcoding TermNorm-specific constants.

Derivation methods:
  node_param_keys()       → node name → param keys
  obs_extraction_map()    → observation name → extraction rules
  langfuse_type_map()     → node name → Langfuse as_type
  backend_nodes()         → runtime filtering
  frontend_nodes()        → runtime filtering
"""

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Observation mapping
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Node metadata (output schema + prompt info)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pipeline node
# ---------------------------------------------------------------------------



class PipelineNode(BaseModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = {"frozen": True}

    name: str
    type: str = "tool"
    runtime: str = "backend"  # "backend" | "frontend"
    short_circuit: bool = False
    node_role: str = ""  # "candidate_source" | "ranker" | "enricher" | "cache" | ""
    description: str = ""
    param_keys: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
    override_map: dict[str, str] = Field(default_factory=dict)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    langfuse_type: str = "span"  # "generation" | "tool" | "retriever" | "span"
    output_schema: NodeOutputSchema | None = None
    prompt_meta: NodePromptMeta | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)
    default_config: dict[str, Any] = Field(default_factory=dict)
    input_keys: set[str] = Field(default_factory=set)


# ---------------------------------------------------------------------------
# Intermediate metrics
# ---------------------------------------------------------------------------

class IntermediateMetric(BaseModel):
    """A metric derived from a pipeline step's node_role."""

    model_config = {"frozen": True}

    name: str
    node_role: str
    pipeline_data_key: str
    description: str = ""
    default_weight: float = 0.0  # 0 = display-only


ROLE_METRIC_REGISTRY: dict[str, list[IntermediateMetric]] = {
    "candidate_source": [
        IntermediateMetric(
            name="source_recall",
            node_role="candidate_source",
            pipeline_data_key="token_matched_candidates",
            description="Fraction of queries where ground truth appears in candidate list",
        ),
    ],
    "ranker": [
        IntermediateMetric(
            name="candidate_recall",
            node_role="ranker",
            pipeline_data_key="ranked_candidates",
            description="Fraction of LLM-ranked queries where ground truth was available",
        ),
    ],
    "cache": [
        IntermediateMetric(
            name="cache_hit_rate",
            node_role="cache",
            pipeline_data_key="step_timings",
            description="Fraction of queries resolved by cache",
        ),
    ],
    "enricher": [],
}


# ---------------------------------------------------------------------------
# PipelineSchema
# ---------------------------------------------------------------------------

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
    required_step: str | None = None
    template_variables: set[str] = Field(default_factory=set)
    dataset_name: str = ""

    # -------------------------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------------------------

    def get_node(self, name: str) -> PipelineNode | None:
        """Find a node by name, or None if not found."""
        for step in self.nodes:
            if step.name == name:
                return step
        return None

    def resolve_flat_param(self, flat_key: str) -> tuple[str, str] | None:
        """Resolve a single flat param name to ``(node_name, wire_key)``.

        Returns ``None`` if the key is not mapped by any step.
        """
        for step in self.nodes:
            if flat_key in step.override_map:
                return (step.name, step.override_map[flat_key])
        return None

    def node_for_flat_param(self, flat_key: str) -> str | None:
        """Return the step name that owns a flat param, or None."""
        for step in self.nodes:
            if flat_key in step.param_keys:
                return step.name
        return None

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
        """Map step name → parameter keys.
        """
        return {
            step.name: step.param_keys
            for step in self.nodes
            if step.param_keys
        }

    def obs_extraction_map(self) -> dict[str, list[ObservationMapping]]:
        """Map observation name → extraction rules.

        Replaces ``OBS_EXTRACTION_MAP`` in ``eval_dataset.py``.
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

    def backend_nodes(self) -> list[PipelineNode]:
        """Steps that run on the backend."""
        return [s for s in self.nodes if s.runtime == "backend"]

    def frontend_nodes(self) -> list[PipelineNode]:
        """Steps that run on the frontend."""
        return [s for s in self.nodes if s.runtime == "frontend"]



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




# ---------------------------------------------------------------------------
# Unified pipeline loading
# ---------------------------------------------------------------------------

def load_pipeline_from_dict(data: dict) -> PipelineSchema:
    """Load a PipelineSchema from a raw dict (e.g., optimizer_pipeline.json).

    Accepts the standard ``{nodes: {...}, pipelines: {...}}`` format used
    by both TermNorm and the optimizer pipeline declarations.
    """
    nodes = []
    for name, node_data in data.get("nodes", {}).items():
        nodes.append(PipelineNode(
            name=name,
            type=node_data.get("type", ""),
            current_config=node_data.get("config", {}),
            default_config=node_data.get("config", {}),
        ))
    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )
