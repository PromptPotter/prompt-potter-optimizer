"""
PipelineSchema — backend-agnostic description of an LLM pipeline.

Describes the steps, parameters, observation mappings, and metadata of
a backend pipeline so that PromptPotter services can work generically
instead of hardcoding TermNorm-specific constants.

Derivation methods replace scattered constants:
  step_param_keys()    → PIPELINE_STEP_PARAMS  (backend_client.py)
  obs_extraction_map() → OBS_EXTRACTION_MAP    (eval_dataset.py)
  langfuse_type_map()  → as_type mapping       (pipeline_nodes.py)
  backend_steps()      → runtime filtering
  frontend_steps()     → runtime filtering
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
# Step metadata (output schema + prompt info)
# ---------------------------------------------------------------------------

class StepOutputSchema(BaseModel):
    """Resolved output schema for a pipeline step.

    Carries the field names and descriptions from the backend's schema registry
    so that downstream consumers (scan advisor, notebooks) can reason about
    what a step produces without calling the registry directly.
    """

    model_config = {"frozen": True}

    family: str = ""
    version: int | None = None
    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict = Field(default_factory=dict)


class StepPromptMeta(BaseModel):
    """Resolved prompt metadata for a pipeline step.

    Carries template variable names, a human description, and the full
    prompt template text from the backend's prompt registry.
    """

    model_config = {"frozen": True}

    family: str = ""
    version: int | None = None
    template_variables: list[str] = Field(default_factory=list)
    description: str = ""
    template: str = ""


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------

class PipelineStep(BaseModel):
    """One step in a backend pipeline."""

    model_config = {"frozen": True}

    name: str
    type: str = "tool"
    runtime: str = "backend"  # "backend" | "frontend"
    short_circuit: bool = False
    node_role: str = ""  # "candidate_source" | "ranker" | "enricher" | "cache" | ""
    param_keys: set[str] = Field(default_factory=set)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    langfuse_type: str = "span"  # "generation" | "tool" | "retriever" | "span"
    output_schema: StepOutputSchema | None = None
    prompt_meta: StepPromptMeta | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)


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
    generically: evaluation, grid search, observability, and Langfuse push.
    """

    model_config = {"frozen": True}

    name: str = ""
    version: str = ""
    description: str = ""
    steps: list[PipelineStep] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)
    required_step: str | None = None
    template_variables: set[str] = Field(default_factory=set)
    dataset_name: str = ""

    # -------------------------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------------------------

    def get_step(self, name: str) -> PipelineStep | None:
        """Find a step by name, or None if not found."""
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def infer_terminating_step(self, step_timings: dict[str, float | None]) -> str | None:
        """Infer which pipeline step produced the final result.

        Walks steps in pipeline order. The last step with a non-None timing
        is the one that produced the result.
        """
        last_executed: str | None = None
        for step in self.steps:
            if step_timings.get(step.name) is not None:
                last_executed = step.name
        return last_executed

    # -------------------------------------------------------------------
    # Derivation methods
    # -------------------------------------------------------------------

    def step_param_keys(self) -> dict[str, set[str]]:
        """Map step name → parameter keys.

        Replaces ``PIPELINE_STEP_PARAMS`` in ``backend_client.py``.
        """
        return {
            step.name: step.param_keys
            for step in self.steps
            if step.param_keys
        }

    def obs_extraction_map(self) -> dict[str, list[ObservationMapping]]:
        """Map observation name → extraction rules.

        Replaces ``OBS_EXTRACTION_MAP`` in ``eval_dataset.py``.
        """
        return {
            step.observation_name: step.observation_mappings
            for step in self.steps
            if step.observation_name and step.observation_mappings
        }

    def langfuse_type_map(self) -> dict[str, str]:
        """Map step name → Langfuse ``as_type``.

        Replaces the implicit mapping in ``pipeline_nodes.py``.
        """
        return {step.name: step.langfuse_type for step in self.steps}

    def backend_steps(self) -> list[PipelineStep]:
        """Steps that run on the backend."""
        return [s for s in self.steps if s.runtime == "backend"]

    def frontend_steps(self) -> list[PipelineStep]:
        """Steps that run on the frontend."""
        return [s for s in self.steps if s.runtime == "frontend"]

    def derive_metrics(
        self,
        results: list[dict],
        *,
        metric_weights: dict[str, float] | None = None,
        accuracy_weight: float = 0.9,
    ) -> dict[str, float]:
        """Compute intermediate metrics from pipeline step node_roles.

        Walks ``self.steps``; for each with a ``node_role`` in the registry,
        computes the corresponding metric scoped to queries where the step ran.

        Returns dict with per-metric values and a weighted ``composite`` score.
        """
        from api.services.prompt_eval import compute_accuracy

        base = compute_accuracy(results)
        accuracy = base["accuracy"]
        weights = dict(metric_weights or {})
        metric_values: dict[str, float] = {}

        # Collect steps by role (namespace when >1 step shares a role)
        role_steps: dict[str, list[PipelineStep]] = {}
        for step in self.steps:
            if step.node_role and step.node_role in ROLE_METRIC_REGISTRY:
                role_steps.setdefault(step.node_role, []).append(step)

        for role, steps in role_steps.items():
            metrics = ROLE_METRIC_REGISTRY.get(role, [])
            needs_namespace = len(steps) > 1
            for step in steps:
                for metric_def in metrics:
                    metric_name = (
                        f"{step.name}_{metric_def.name}"
                        if needs_namespace
                        else metric_def.name
                    )
                    value = self._compute_role_metric(
                        metric_def, step, results,
                    )
                    metric_values[metric_name] = value

        # Composite: accuracy_weight * accuracy + distributed remaining weight
        remaining_weight = 1.0 - accuracy_weight
        weighted_sum = accuracy_weight * accuracy
        if metric_values:
            n_metrics = len(metric_values)
            for m_name, m_val in metric_values.items():
                w = weights.get(m_name, remaining_weight / n_metrics)
                weighted_sum += w * m_val
        composite = weighted_sum

        return {
            **base,
            **metric_values,
            "composite": round(composite, 6),
        }

    @staticmethod
    def _compute_role_metric(
        metric_def: "IntermediateMetric",
        step: "PipelineStep",
        results: list[dict],
    ) -> float:
        """Compute a single role-based metric value."""
        if metric_def.name == "source_recall":
            return _compute_source_recall(step, results)
        if metric_def.name == "candidate_recall":
            return _compute_candidate_recall(step, results)
        if metric_def.name == "cache_hit_rate":
            return _compute_cache_hit_rate(step, results)
        return 0.0


def _compute_source_recall(step: PipelineStep, results: list[dict]) -> float:
    """Fraction of queries where GT appears in the candidate source output."""
    scoped = [r for r in results if _step_ran(step.name, r) and not r.get("error")]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        candidates = pd.get("token_matched_candidates", [])
        gt = r.get("ground_truth", "")
        for c in candidates:
            name = c[0] if isinstance(c, (list, tuple)) else str(c)
            if name == gt:
                found += 1
                break
    return found / len(scoped)


def _compute_candidate_recall(step: PipelineStep, results: list[dict]) -> float:
    """Fraction of LLM-ranked queries where GT was in the candidate list."""
    scoped = [r for r in results if _step_ran(step.name, r) and not r.get("error")]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        candidates = pd.get("token_matched_candidates", [])
        gt = r.get("ground_truth", "")
        for c in candidates:
            name = c[0] if isinstance(c, (list, tuple)) else str(c)
            if name == gt:
                found += 1
                break
    return found / len(scoped)


def _compute_cache_hit_rate(step: PipelineStep, results: list[dict]) -> float:
    """Fraction of queries resolved by cache (non-null cache timing)."""
    if not results:
        return 0.0
    cache_hits = 0
    for r in results:
        if r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        timings = pd.get("step_timings") or {}
        if timings.get(step.name) is not None:
            cache_hits += 1
    non_error = sum(1 for r in results if not r.get("error"))
    return cache_hits / non_error if non_error else 0.0


def _step_ran(step_name: str, result: dict) -> bool:
    """Check if a step ran for a given result (via step_timings or terminated_at)."""
    pd = result.get("pipeline_data") or {}
    terminated_at = pd.get("terminated_at")
    if terminated_at == step_name:
        return True
    timings = pd.get("step_timings") or {}
    return timings.get(step_name) is not None
