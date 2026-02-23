"""
Pydantic models for CWL-inspired workflow definitions.

Based on Common Workflow Language v1.2 structure with extensions
for LLM-specific metadata (model, temperature, etc.).
"""
from typing import Any
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class StepType(str, Enum):
    """Classification of workflow step types."""

    ALGORITHM = "algorithm"  # Deterministic computational processes
    LLM = "llm"  # Large language model inference
    EXTERNAL = "external"  # Network calls, APIs, web scraping
    IO = "io"  # Input/output operations
    PARALLEL = "parallel"  # Concurrent execution blocks


class StepMetadata(BaseModel):
    """
    Metadata for a workflow step.

    Follows the classification rules from prompts/pipeline-to-cwl.md
    """

    type: StepType = Field(..., description="Step classification")
    deterministic: bool = Field(True, description="Whether output is reproducible")
    scientific_name: str | None = Field(
        None, description="Canonical algorithm name (for algorithm type)"
    )
    model: str | None = Field(None, description="Model identifier (for llm type)")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int | None = Field(None, gt=0, description="Maximum response tokens")
    algorithm_params: dict[str, Any] | None = Field(
        None, description="Algorithm-specific parameters"
    )


class StepDefinition(BaseModel):
    """
    Definition of a single workflow step.

    Maps to CWL step structure with extensions for node configuration.
    """

    id: str = Field(..., description="Unique step identifier (snake_case)")
    run: str = Field(
        ...,
        description="Node type to execute (e.g., 'nodes/LLMNode' or 'LLMNode')"
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        alias="in",
        description="Input mappings: {param_name: 'source_step/output' or literal}"
    )
    outputs: list[str] = Field(
        default_factory=list,
        alias="out",
        description="Output names to expose"
    )
    config: dict[str, Any] | None = Field(
        None, description="Node configuration (model, temperature, etc.)"
    )
    metadata: StepMetadata | None = Field(
        None, description="Step classification and metadata"
    )

    model_config = {"populate_by_name": True}

    @property
    def node_type(self) -> str:
        """Extract node type from run path (e.g., 'nodes/LLMNode' -> 'LLMNode')."""
        if "/" in self.run:
            return self.run.split("/")[-1]
        return self.run


class WorkflowInput(BaseModel):
    """Definition of a workflow-level input."""

    type: str = Field(..., description="Input type (string, int, float, string[], File)")
    description: str | None = Field(None, description="Human-readable description")
    default: Any | None = Field(None, description="Default value if not provided")


class WorkflowOutput(BaseModel):
    """Definition of a workflow-level output."""

    type: str = Field(..., description="Output type")
    outputSource: str = Field(..., description="Source: step_id/output_name")


class WorkflowDefinition(BaseModel):
    """
    Complete workflow definition (CWL-inspired).

    Compatible with the structure from prompts/pipeline-to-cwl.md.
    Workflows are DAGs of nodes with typed inputs/outputs.
    """

    cwlVersion: str = Field("v1.2", description="CWL version for compatibility")
    class_: str = Field("Workflow", alias="class", description="Document class")
    id: str = Field(..., description="Unique workflow identifier")
    label: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Workflow description")
    inputs: dict[str, WorkflowInput | str] = Field(
        default_factory=dict,
        description="Workflow-level inputs"
    )
    steps: list[StepDefinition] = Field(
        default_factory=list,
        description="Workflow steps (nodes)"
    )
    outputs: dict[str, WorkflowOutput] = Field(
        default_factory=dict,
        description="Workflow-level outputs"
    )

    model_config = {"populate_by_name": True}

    @field_validator("steps")
    @classmethod
    def validate_unique_step_ids(cls, v: list[StepDefinition]) -> list[StepDefinition]:
        """Ensure all step IDs are unique."""
        ids = [step.id for step in v]
        if len(ids) != len(set(ids)):
            duplicates = [id for id in ids if ids.count(id) > 1]
            raise ValueError(f"Duplicate step IDs: {set(duplicates)}")
        return v

    def get_step(self, step_id: str) -> StepDefinition | None:
        """Get a step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_step_dependencies(self, step_id: str) -> list[str]:
        """
        Get IDs of steps that this step depends on.

        Parses input references like 'other_step/output_name'.
        """
        step = self.get_step(step_id)
        if not step:
            return []

        dependencies = []
        for input_ref in step.inputs.values():
            if isinstance(input_ref, str) and "/" in input_ref:
                source_step = input_ref.split("/")[0]
                if source_step not in dependencies:
                    dependencies.append(source_step)
        return dependencies


class WorkflowExecuteRequest(BaseModel):
    """Request to execute a workflow."""

    workflow: WorkflowDefinition | None = Field(
        None, description="Inline workflow definition"
    )
    workflow_id: str | None = Field(
        None, description="ID of registered workflow to load from disk"
    )
    inputs: dict[str, Any] = Field(
        ..., description="Workflow input values"
    )
    trace_id: str | None = Field(
        None, description="Custom trace ID for Langfuse integration"
    )

    @field_validator("workflow", "workflow_id")
    @classmethod
    def validate_workflow_source(cls, v, info):
        """Ensure either workflow or workflow_id is provided."""
        # This runs for each field; the model validator below does the cross-field check
        return v

    def model_post_init(self, __context) -> None:
        """Validate that exactly one workflow source is provided."""
        if self.workflow is None and self.workflow_id is None:
            raise ValueError("Either 'workflow' or 'workflow_id' must be provided")
        if self.workflow is not None and self.workflow_id is not None:
            raise ValueError("Provide only one of 'workflow' or 'workflow_id', not both")


class WorkflowExecuteResponse(BaseModel):
    """Response from workflow execution."""

    success: bool = Field(..., description="Whether execution completed successfully")
    trace_id: str = Field(..., description="Trace ID for Langfuse")
    workflow_id: str = Field(..., description="Executed workflow ID")
    outputs: dict[str, Any] = Field(..., description="Final workflow outputs")
    step_outputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Outputs from each step (step_id -> {output_name: value})"
    )
    metrics: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Execution metrics per step"
    )
    execution_time_ms: float = Field(..., description="Total execution time")
    status: str = Field(..., description="Execution status: completed, failed")


class DatasetItem(BaseModel):
    """Single dataset item for workflow evaluation."""

    inputs: dict[str, Any] = Field(..., description="Workflow inputs")
    expected_output: dict[str, Any] = Field(
        ..., description="Expected workflow outputs"
    )


class WorkflowEvaluateRequest(BaseModel):
    """Request to evaluate a workflow on a dataset."""

    workflow: WorkflowDefinition | None = Field(None)
    workflow_id: str | None = Field(None)
    dataset: list[DatasetItem] = Field(
        ..., min_length=1, description="Dataset items to evaluate"
    )
    evaluator: str = Field(
        default="exact_match",
        description="Evaluator type: exact_match, substring, criteria"
    )
    evaluator_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Evaluator-specific configuration"
    )


class WorkflowEvaluateResponse(BaseModel):
    """Response from workflow evaluation."""

    success: bool
    total: int = Field(..., description="Total dataset items")
    passed: int = Field(..., description="Items that passed evaluation")
    failed: int = Field(..., description="Items that failed evaluation")
    accuracy: float = Field(..., ge=0.0, le=1.0, description="Pass rate")
    mean_score: float = Field(..., ge=0.0, le=1.0, description="Average score")
    results: list[dict[str, Any]] = Field(
        ..., description="Per-item evaluation results"
    )
    execution_time_ms: float
