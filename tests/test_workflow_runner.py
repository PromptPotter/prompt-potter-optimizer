"""Tests for the DAG workflow runner."""
from typing import Type
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from api.core.workflow_runner import WorkflowRunner, WorkflowContext
from api.models.workflow import (
    WorkflowDefinition,
    StepDefinition,
    WorkflowOutput,
)
from api.nodes.base import NodeBase


# ---------------------------------------------------------------------------
# StubNode — minimal node for testing
# ---------------------------------------------------------------------------

class StubInput(BaseModel):
    value: str = ""


class StubOutput(BaseModel):
    result: str = ""


class StubNode(NodeBase[StubInput, StubOutput]):
    """Trivial node that echoes its input with a prefix."""

    @classmethod
    def get_input_model(cls) -> Type[StubInput]:
        return StubInput

    @classmethod
    def get_output_model(cls) -> Type[StubOutput]:
        return StubOutput

    async def _execute(self, input_data: StubInput) -> StubOutput:
        return StubOutput(result=f"processed:{input_data.value}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workflow(steps, outputs=None):
    """Build a minimal WorkflowDefinition."""
    return WorkflowDefinition(
        **{
            "class": "Workflow",
            "id": "test-wf",
            "steps": steps,
            "outputs": outputs or {},
        }
    )


def _step(step_id, run="StubNode", inputs=None, outputs=None):
    """Shorthand for StepDefinition construction."""
    return StepDefinition(
        id=step_id,
        run=run,
        **{"in": inputs or {}, "out": outputs or []},
    )


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    def test_single_step(self):
        wf = _make_workflow([_step("a")])
        runner = WorkflowRunner(wf)
        assert runner._topological_sort() == ["a"]

    def test_linear_chain(self):
        wf = _make_workflow([
            _step("a"),
            _step("b", inputs={"x": "a/result"}),
            _step("c", inputs={"x": "b/result"}),
        ])
        runner = WorkflowRunner(wf)
        order = runner._topological_sort()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond_dag(self):
        wf = _make_workflow([
            _step("a"),
            _step("b", inputs={"x": "a/result"}),
            _step("c", inputs={"x": "a/result"}),
            _step("d", inputs={"x1": "b/result", "x2": "c/result"}),
        ])
        runner = WorkflowRunner(wf)
        order = runner._topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_raises(self):
        wf = _make_workflow([
            _step("a", inputs={"x": "b/result"}),
            _step("b", inputs={"x": "a/result"}),
        ])
        runner = WorkflowRunner(wf)
        with pytest.raises(ValueError, match="cycle"):
            runner._topological_sort()


# ---------------------------------------------------------------------------
# _resolve_input
# ---------------------------------------------------------------------------

class TestResolveInput:
    def _ctx(self, inputs=None, step_outputs=None):
        return WorkflowContext(
            workflow_id="wf",
            trace_id="t",
            inputs=inputs or {},
            step_outputs=step_outputs or {},
            start_time="2024-01-01T00:00:00Z",
        )

    def test_literal_passthrough(self):
        wf = _make_workflow([_step("a")])
        runner = WorkflowRunner(wf)
        ctx = self._ctx()
        assert runner._resolve_input(42, ctx) == 42
        assert runner._resolve_input({"k": "v"}, ctx) == {"k": "v"}

    def test_workflow_input_ref(self):
        wf = _make_workflow([_step("a")])
        runner = WorkflowRunner(wf)
        ctx = self._ctx(inputs={"query": "hello"})
        assert runner._resolve_input("query", ctx) == "hello"

    def test_step_output_ref(self):
        wf = _make_workflow([_step("a")])
        runner = WorkflowRunner(wf)
        ctx = self._ctx(step_outputs={"step_a": {"result": "ok"}})
        assert runner._resolve_input("step_a/result", ctx) == "ok"


# ---------------------------------------------------------------------------
# execute (async, mocks node registry)
# ---------------------------------------------------------------------------

class TestExecute:
    def _stub_registry(self):
        return {"StubNode": StubNode}

    async def test_single_step(self):
        wf = _make_workflow(
            [_step("a", inputs={"value": "hello"})],
            outputs={"final": WorkflowOutput(type="string", outputSource="a/result")},
        )
        runner = WorkflowRunner(wf)
        with patch.object(runner, "_get_node_registry", return_value=self._stub_registry()):
            ctx = await runner.execute({"value": "hello"})
        assert ctx.status == "completed"
        assert ctx.step_outputs["a"]["result"] == "processed:hello"

    async def test_two_step_data_flow(self):
        wf = _make_workflow([
            _step("a", inputs={"value": "x"}),
            _step("b", inputs={"value": "a/result"}),
        ])
        runner = WorkflowRunner(wf)
        with patch.object(runner, "_get_node_registry", return_value=self._stub_registry()):
            ctx = await runner.execute({})
        assert ctx.status == "completed"
        assert ctx.step_outputs["b"]["result"] == "processed:processed:x"

    async def test_unknown_node_type_raises(self):
        wf = _make_workflow([_step("a", run="NoSuchNode")])
        runner = WorkflowRunner(wf)
        with patch.object(runner, "_get_node_registry", return_value=self._stub_registry()):
            with pytest.raises(ValueError, match="Unknown node type"):
                await runner.execute({})
