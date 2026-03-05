"""
Workflow execution endpoints.

Provides REST API for executing workflows with inputs.
"""
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from api.models.workflow import (
    WorkflowDefinition,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
)
from api.core.workflow_runner import WorkflowRunner


router = APIRouter()


# ============================================================================
# Workflow Execution
# ============================================================================

@router.post("/workflows/execute", response_model=WorkflowExecuteResponse)
async def execute_workflow(request: WorkflowExecuteRequest):
    """
    Execute a workflow with given inputs.

    Provide either:
    - `workflow`: Inline workflow definition (CWL-inspired YAML/JSON)
    - `workflow_id`: ID of a registered workflow to load from disk

    Returns workflow outputs, step outputs, and execution metrics.
    """
    start = time.time()

    # Get workflow definition
    if request.workflow:
        workflow_def = request.workflow
    elif request.workflow_id:
        workflow_def = _load_workflow(request.workflow_id)
    else:
        raise HTTPException(400, "Provide either 'workflow' or 'workflow_id'")

    try:
        runner = WorkflowRunner(workflow_def)
        context = await runner.execute(
            inputs=request.inputs,
            trace_id=request.trace_id
        )

        # Extract final outputs
        final_outputs = runner.get_final_outputs(context)
        execution_time = (time.time() - start) * 1000

        return WorkflowExecuteResponse(
            success=context.status == "completed",
            trace_id=context.trace_id,
            workflow_id=workflow_def.id,
            outputs=final_outputs,
            step_outputs=context.step_outputs,
            metrics=context.metrics,
            execution_time_ms=execution_time,
            status=context.status
        )

    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Workflow execution failed: {str(e)}")


# ============================================================================
# Helpers
# ============================================================================

def _load_workflow(workflow_id: str) -> WorkflowDefinition:
    """Load workflow from workflows/{workflow_id}.yaml."""
    workflow_path = Path(f"workflows/{workflow_id}.yaml")

    if not workflow_path.exists():
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    with open(workflow_path, "r") as f:
        data = yaml.safe_load(f)

    try:
        return WorkflowDefinition.model_validate(data)
    except Exception as e:
        raise HTTPException(400, f"Invalid workflow definition: {str(e)}")
