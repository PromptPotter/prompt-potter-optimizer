"""Tests for the DAG workflow runner."""
from typing import Type
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from api.core.workflow_runner import WorkflowRunner, WorkflowContext
from api.models.workflow import WorkflowDefinition, StepDefinition, WorkflowOutput
from api.nodes.base import NodeBase


class StubInput(BaseModel):
    value: str = ""


class StubOutput(BaseModel):
    result: str = ""


class StubNode(NodeBase[StubInput, StubOutput]):
    @classmethod
    def get_input_model(cls) -> Type[StubInput]:
        return StubInput

    @classmethod
    def get_output_model(cls) -> Type[StubOutput]:
        return StubOutput

    async def _execute(self, input_data: StubInput) -> StubOutput:
        return StubOutput(result=f"processed:{input_data.value}")


def _wf(steps, outputs=None):
    return WorkflowDefinition(**{
        "class": "Workflow", "id": "test-wf",
        "steps": steps, "outputs": outputs or {},
    })


def _step(sid, run="StubNode", inputs=None):
    return StepDefinition(id=sid, run=run, **{"in": inputs or {}, "out": []})


def test_topological_sort():
    wf = _wf([
        _step("a"),
        _step("b", inputs={"x": "a/result"}),
        _step("c", inputs={"x": "b/result"}),
    ])
    runner = WorkflowRunner(wf)
    order = runner._topological_sort()
    assert order.index("a") < order.index("b") < order.index("c")

    # Cycle raises
    wf_cycle = _wf([
        _step("a", inputs={"x": "b/result"}),
        _step("b", inputs={"x": "a/result"}),
    ])
    with pytest.raises(ValueError, match="cycle"):
        WorkflowRunner(wf_cycle)._topological_sort()


async def test_execute_two_step_flow():
    wf = _wf([
        _step("a", inputs={"value": "x"}),
        _step("b", inputs={"value": "a/result"}),
    ])
    runner = WorkflowRunner(wf)
    with patch.object(runner, "_get_node_registry", return_value={"StubNode": StubNode}):
        ctx = await runner.execute({})
    assert ctx.status == "completed"
    assert ctx.step_outputs["b"]["result"] == "processed:processed:x"
