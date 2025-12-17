"""
Workflow execution endpoints.

Provides REST API for:
- Executing workflows with inputs
- Evaluating workflows on datasets
- Managing workflow definitions
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional
from pathlib import Path
import yaml
import time

from api.models.workflow import (
    WorkflowDefinition,
    WorkflowExecuteRequest,
    WorkflowExecuteResponse,
    WorkflowEvaluateRequest,
    WorkflowEvaluateResponse,
    DatasetItem,
)
from api.core.workflow_runner import WorkflowRunner
from api.evaluators import get_evaluator
from api.nodes import list_node_types, get_node_info_all


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
# Workflow Evaluation
# ============================================================================

@router.post("/workflows/evaluate", response_model=WorkflowEvaluateResponse)
async def evaluate_workflow(request: WorkflowEvaluateRequest):
    """
    Evaluate a workflow on a dataset.

    Runs the workflow on each dataset item and compares
    outputs against expected values using the specified evaluator.

    Evaluator types:
    - `exact_match`: Exact string equality
    - `criteria`: LLM-based semantic evaluation
    """
    start = time.time()

    # Get workflow definition
    if request.workflow:
        workflow_def = request.workflow
    elif request.workflow_id:
        workflow_def = _load_workflow(request.workflow_id)
    else:
        raise HTTPException(400, "Provide either 'workflow' or 'workflow_id'")

    # Get evaluator
    try:
        evaluator = get_evaluator(request.evaluator, request.evaluator_config)
    except ValueError as e:
        raise HTTPException(400, str(e))

    results: List[Dict[str, Any]] = []
    runner = WorkflowRunner(workflow_def)

    for item in request.dataset:
        try:
            context = await runner.execute(inputs=item.inputs)
            final_outputs = runner.get_final_outputs(context)

            # Evaluate outputs
            if isinstance(item.expected_output, dict) and isinstance(final_outputs, dict):
                eval_result = evaluator.evaluate_workflow_output(
                    expected=item.expected_output,
                    actual=final_outputs
                )
            else:
                eval_result = evaluator.evaluate(
                    expected=item.expected_output,
                    actual=final_outputs
                )

            results.append({
                "inputs": item.inputs,
                "expected": item.expected_output,
                "actual": final_outputs,
                "evaluation": eval_result.model_dump(),
                "trace_id": context.trace_id
            })

        except Exception as e:
            results.append({
                "inputs": item.inputs,
                "expected": item.expected_output,
                "actual": None,
                "evaluation": {
                    "result": "error",
                    "score": 0.0,
                    "reason": str(e)
                },
                "trace_id": None
            })

    # Aggregate metrics
    passed = sum(1 for r in results if r["evaluation"].get("result") == "pass")
    scores = [r["evaluation"].get("score", 0.0) for r in results]
    execution_time = (time.time() - start) * 1000

    return WorkflowEvaluateResponse(
        success=True,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        accuracy=passed / len(results) if results else 0.0,
        mean_score=sum(scores) / len(scores) if scores else 0.0,
        results=results,
        execution_time_ms=execution_time
    )


# ============================================================================
# Workflow Management
# ============================================================================

@router.post("/workflows", response_model=Dict[str, Any])
async def create_workflow(workflow: WorkflowDefinition):
    """
    Register a new workflow definition.

    Stores the workflow YAML in the workflows directory for later use.
    """
    workflows_dir = Path("workflows")
    workflows_dir.mkdir(exist_ok=True)

    workflow_path = workflows_dir / f"{workflow.id}.yaml"

    # Convert to dict for YAML serialization
    workflow_dict = workflow.model_dump(by_alias=True, exclude_none=True)

    with open(workflow_path, "w") as f:
        yaml.dump(workflow_dict, f, default_flow_style=False, sort_keys=False)

    return {
        "success": True,
        "workflow_id": workflow.id,
        "path": str(workflow_path)
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get a registered workflow by ID."""
    workflow = _load_workflow(workflow_id)
    return workflow.model_dump(by_alias=True, exclude_none=True)


@router.get("/workflows")
async def list_workflows():
    """List all registered workflows."""
    workflows_dir = Path("workflows")
    examples_dir = workflows_dir / "examples"

    workflow_files: List[Dict[str, str]] = []

    # Check main workflows directory
    if workflows_dir.exists():
        for yaml_file in workflows_dir.glob("*.yaml"):
            workflow_files.append({
                "id": yaml_file.stem,
                "path": str(yaml_file)
            })

    # Check examples directory
    if examples_dir.exists():
        for yaml_file in examples_dir.glob("*.yaml"):
            workflow_files.append({
                "id": f"examples/{yaml_file.stem}",
                "path": str(yaml_file)
            })

    return {"workflows": workflow_files}


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a registered workflow."""
    workflow_path = Path(f"workflows/{workflow_id}.yaml")

    if not workflow_path.exists():
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    workflow_path.unlink()

    return {"success": True, "deleted": workflow_id}


# ============================================================================
# Node Discovery
# ============================================================================

@router.get("/nodes")
async def list_nodes():
    """List all available node types."""
    return {
        "nodes": list_node_types(),
        "details": get_node_info_all()
    }


# ============================================================================
# Helpers
# ============================================================================

def _load_workflow(workflow_id: str) -> WorkflowDefinition:
    """
    Load workflow from file.

    Searches in:
    - workflows/{workflow_id}.yaml
    - workflows/examples/{workflow_id}.yaml

    Args:
        workflow_id: Workflow identifier

    Returns:
        Parsed WorkflowDefinition

    Raises:
        HTTPException: If workflow not found
    """
    # Try main workflows directory
    workflow_path = Path(f"workflows/{workflow_id}.yaml")

    if not workflow_path.exists():
        # Try examples directory
        workflow_path = Path(f"workflows/examples/{workflow_id}.yaml")

    if not workflow_path.exists():
        # Try with examples/ prefix stripped
        if workflow_id.startswith("examples/"):
            stripped_id = workflow_id[9:]  # Remove "examples/"
            workflow_path = Path(f"workflows/examples/{stripped_id}.yaml")

    if not workflow_path.exists():
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    with open(workflow_path, "r") as f:
        data = yaml.safe_load(f)

    try:
        return WorkflowDefinition.model_validate(data)
    except Exception as e:
        raise HTTPException(400, f"Invalid workflow definition: {str(e)}")
