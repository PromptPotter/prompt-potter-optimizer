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
    param_keys: set[str] = Field(default_factory=set)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    langfuse_type: str = "span"  # "generation" | "tool" | "retriever" | "span"
    output_schema: StepOutputSchema | None = None
    prompt_meta: StepPromptMeta | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)


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
    required_step: str | None = None
    template_variables: set[str] = Field(default_factory=set)
    dataset_name: str = ""

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
