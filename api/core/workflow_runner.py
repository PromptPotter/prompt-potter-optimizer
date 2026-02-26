"""
Workflow execution engine.

Executes CWL-inspired workflow definitions by:
1. Parsing workflow spec (YAML or Pydantic model)
2. Topologically sorting nodes (DAG)
3. Executing nodes with data flow routing
4. Collecting traces for Langfuse integration
"""
from typing import Any, TYPE_CHECKING
from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path
import uuid
import yaml

from api.models.workflow import WorkflowDefinition, StepDefinition

if TYPE_CHECKING:
    pass


class WorkflowContext(BaseModel):
    """Runtime context for workflow execution."""

    workflow_id: str = Field(..., description="Workflow identifier")
    trace_id: str = Field(..., description="Unique trace ID for this execution")
    langfuse_trace_id: str | None = Field(
        None, description="Langfuse trace ID (for cloud logging)"
    )
    inputs: dict[str, Any] = Field(..., description="Workflow-level inputs")
    step_outputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Outputs from each step: {step_id: {output_name: value}}"
    )
    metrics: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Execution metrics per step"
    )
    start_time: str = Field(..., description="ISO timestamp of execution start")
    end_time: str | None = Field(None, description="ISO timestamp of execution end")
    status: str = Field(default="pending", description="pending, running, completed, failed")
    error: str | None = Field(None, description="Error message if failed")


def generate_trace_id() -> str:
    """Generate a unique trace ID with timestamp prefix."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return f"trace_{timestamp}_{short_uuid}"


class WorkflowRunner:
    """
    Executes workflow definitions.

    Responsibilities:
    - Parse and validate workflow YAML/JSON
    - Build execution DAG from step dependencies
    - Route data between nodes via references
    - Collect metrics and traces per node
    """

    def __init__(self, workflow_def: WorkflowDefinition):
        """
        Initialize runner with a workflow definition.

        Args:
            workflow_def: Parsed workflow definition
        """
        self.workflow = workflow_def
        self._execution_order: list[str] = []
        self._node_registry: dict[str, type] = {}

    def _get_node_registry(self) -> dict[str, type]:
        """
        Get the node registry (lazy load to avoid circular imports).
        """
        if not self._node_registry:
            from api.nodes import get_node_registry
            self._node_registry = get_node_registry()
        return self._node_registry

    def _topological_sort(self) -> list[str]:
        """
        Sort steps by dependency order using Kahn's algorithm.

        Returns:
            List of step IDs in execution order

        Raises:
            ValueError: If workflow contains cycles
        """
        # Build dependency graph from step.in references
        in_degree: dict[str, int] = {step.id: 0 for step in self.workflow.steps}
        graph: dict[str, list[str]] = {step.id: [] for step in self.workflow.steps}

        for step in self.workflow.steps:
            deps = self.workflow.get_step_dependencies(step.id)
            for dep in deps:
                if dep in graph:
                    graph[dep].append(step.id)
                    in_degree[step.id] += 1

        # Kahn's algorithm
        queue = [step_id for step_id, degree in in_degree.items() if degree == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.workflow.steps):
            raise ValueError("Workflow contains cycles - not a valid DAG")

        return result

    def _resolve_input(
        self,
        input_ref: Any,
        context: WorkflowContext
    ) -> Any:
        """
        Resolve an input reference to its actual value.

        References can be:
        - Workflow input: "input_name" (string matching a workflow input)
        - Step output: "step_id/output_name"
        - Literal value: anything else (dict, list, int, etc.)

        Args:
            input_ref: The reference or literal value
            context: Current workflow context

        Returns:
            Resolved value
        """
        if not isinstance(input_ref, str):
            # Literal value (dict, list, int, etc.)
            return input_ref

        if "/" in input_ref:
            # Step output reference: "step_id/output_name"
            parts = input_ref.split("/", 1)
            step_id = parts[0]
            output_name = parts[1] if len(parts) > 1 else None

            if step_id not in context.step_outputs:
                raise ValueError(f"Step '{step_id}' not yet executed or doesn't exist")

            step_output = context.step_outputs[step_id]

            if output_name:
                if output_name not in step_output:
                    raise ValueError(
                        f"Output '{output_name}' not found in step '{step_id}'. "
                        f"Available: {list(step_output.keys())}"
                    )
                return step_output[output_name]
            else:
                # Return entire step output if no specific field
                return step_output

        elif input_ref in context.inputs:
            # Workflow input reference
            return context.inputs[input_ref]

        else:
            # Treat as literal string
            return input_ref

    def _resolve_step_inputs(
        self,
        step: StepDefinition,
        context: WorkflowContext
    ) -> dict[str, Any]:
        """
        Resolve all inputs for a step.

        Args:
            step: Step definition
            context: Current workflow context

        Returns:
            Dict of resolved input values
        """
        resolved = {}
        for input_name, input_ref in step.inputs.items():
            resolved[input_name] = self._resolve_input(input_ref, context)
        return resolved

    async def execute(
        self,
        inputs: dict[str, Any],
        trace_id: str | None = None
    ) -> WorkflowContext:
        """
        Execute the workflow with given inputs.

        Args:
            inputs: Workflow-level input values
            trace_id: Optional custom trace ID (auto-generated if not provided)

        Returns:
            WorkflowContext with all outputs and metrics
        """
        # Initialize Langfuse logger (singleton, no-op if disabled)
        from api.services.obs.langfuse_client import LangfuseLogger
        langfuse = LangfuseLogger.get_instance()

        context = WorkflowContext(
            workflow_id=self.workflow.id,
            trace_id=trace_id or generate_trace_id(),
            inputs=inputs,
            start_time=datetime.utcnow().isoformat() + "Z",
            status="running"
        )

        # Create Langfuse trace for this workflow execution
        context.langfuse_trace_id = langfuse.create_trace(
            name=self.workflow.id,
            input=inputs,
            metadata={
                "workflow_label": self.workflow.label,
                "workflow_description": self.workflow.description,
                "local_trace_id": context.trace_id,
            },
            tags=["workflow"],
        )

        # Get execution order
        self._execution_order = self._topological_sort()

        try:
            node_registry = self._get_node_registry()

            for step_id in self._execution_order:
                step = self.workflow.get_step(step_id)
                if not step:
                    raise ValueError(f"Step '{step_id}' not found in workflow")

                # Get node class from registry
                node_type = step.node_type
                node_class = node_registry.get(node_type)

                if not node_class:
                    # Try with 'nodes/' prefix
                    node_class = node_registry.get(f"nodes/{node_type}")

                if not node_class:
                    raise ValueError(
                        f"Unknown node type: '{node_type}'. "
                        f"Available: {list(node_registry.keys())}"
                    )

                # Merge step config with metadata config
                node_config = step.config or {}
                if step.metadata:
                    if step.metadata.model:
                        node_config.setdefault("model", step.metadata.model)
                    if step.metadata.temperature is not None:
                        node_config.setdefault("temperature", step.metadata.temperature)
                    if step.metadata.max_tokens:
                        node_config.setdefault("max_tokens", step.metadata.max_tokens)
                    if step.metadata.algorithm_params:
                        node_config.update(step.metadata.algorithm_params)

                # Instantiate node
                node = node_class(node_id=step_id, config=node_config)

                # Resolve inputs
                step_inputs = self._resolve_step_inputs(step, context)

                # Execute node
                output = await node.process(step_inputs)

                # Store outputs
                output_dict = output.model_dump()
                context.step_outputs[step_id] = output_dict

                # Collect metrics
                if node.get_last_metrics():
                    context.metrics.append(node.get_last_metrics().model_dump())

                # Log to Langfuse based on node type
                if context.langfuse_trace_id:
                    node_meta_type = step.metadata.type if step.metadata else None
                    if node_meta_type == "llm":
                        # LLM node → log as generation
                        usage = None
                        if hasattr(output, "usage") and output.usage:
                            usage = output.usage
                        elif "usage" in output_dict:
                            usage = output_dict["usage"]
                        langfuse.create_generation(
                            trace_id=context.langfuse_trace_id,
                            name=step_id,
                            model=node_config.get("model", "unknown"),
                            input=step_inputs,
                            output=output_dict,
                            usage=usage,
                            metadata={"node_type": node_type},
                        )
                    else:
                        # Non-LLM node → log as span
                        langfuse.create_span(
                            trace_id=context.langfuse_trace_id,
                            name=step_id,
                            input=step_inputs,
                            output=output_dict,
                            metadata={"node_type": node_type},
                        )

            context.status = "completed"

        except Exception as e:
            context.status = "failed"
            context.error = str(e)
            context.metrics.append({
                "error": str(e),
                "step_id": step_id if "step_id" in locals() else None,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })
            raise

        finally:
            context.end_time = datetime.utcnow().isoformat() + "Z"

            # Update Langfuse trace with final output and flush
            if context.langfuse_trace_id:
                final_outputs = self.get_final_outputs(context)
                langfuse.update_trace(
                    trace_id=context.langfuse_trace_id,
                    output=final_outputs,
                    metadata={
                        "status": context.status,
                        "duration_ms": self._calculate_duration_ms(
                            context.start_time, context.end_time
                        ) if context.end_time else None,
                        "error": context.error,
                    }
                )
                langfuse.flush()

        return context

    def _calculate_duration_ms(self, start_time: str, end_time: str) -> float:
        """Calculate duration in milliseconds between two ISO timestamps."""
        try:
            start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            return (end - start).total_seconds() * 1000
        except Exception:
            return 0.0

    def get_final_outputs(self, context: WorkflowContext) -> dict[str, Any]:
        """
        Extract final workflow outputs from context.

        Maps workflow output definitions to actual values from step outputs.

        Args:
            context: Completed workflow context

        Returns:
            Dict of output name to value
        """
        final_outputs = {}

        for output_name, output_def in self.workflow.outputs.items():
            source = output_def.outputSource
            if "/" in source:
                step_id, output_field = source.split("/", 1)
                if step_id in context.step_outputs:
                    step_output = context.step_outputs[step_id]
                    if output_field in step_output:
                        final_outputs[output_name] = step_output[output_field]
                    else:
                        final_outputs[output_name] = None
                else:
                    final_outputs[output_name] = None
            else:
                # Direct reference to step output
                final_outputs[output_name] = context.step_outputs.get(source)

        return final_outputs

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "WorkflowRunner":
        """
        Create a WorkflowRunner from a YAML file.

        Args:
            yaml_path: Path to workflow YAML file

        Returns:
            WorkflowRunner instance
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {yaml_path}")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        workflow_def = WorkflowDefinition.model_validate(data)
        return cls(workflow_def)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowRunner":
        """
        Create a WorkflowRunner from a dictionary.

        Args:
            data: Workflow definition as dict

        Returns:
            WorkflowRunner instance
        """
        workflow_def = WorkflowDefinition.model_validate(data)
        return cls(workflow_def)
