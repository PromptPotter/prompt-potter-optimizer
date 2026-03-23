# Node Development Guide

## Architecture

`NodeBase[TInput, TOutput]` (`api/nodes/base.py`) — Template Method pattern with Pydantic generics. Override `get_input_model()`, `get_output_model()`, and `async _execute()`. The public `process()` method validates I/O and captures timing metrics automatically.

## Creating a Node

**1. Define I/O models + subclass:**

```python
from pydantic import BaseModel, Field
from typing import Type
from api.nodes.base import NodeBase

class MyInput(BaseModel):
    query: str = Field(..., description="Search query")

class MyOutput(BaseModel):
    results: list[str] = Field(..., description="Matched results")

class MyNode(NodeBase[MyInput, MyOutput]):
    @classmethod
    def get_input_model(cls) -> Type[MyInput]: return MyInput
    @classmethod
    def get_output_model(cls) -> Type[MyOutput]: return MyOutput

    async def _execute(self, input_data: MyInput) -> MyOutput:
        threshold = self.config.get("threshold", 0.5)  # from YAML config
        # ... your logic ...
        return MyOutput(results=["..."])
```

**2. Register** in `api/nodes/__init__.py`:

```python
from .my_node import MyNode  # noqa: E402
register_node(MyNode)
```

**3. Use in YAML:**

```yaml
- id: search
  run: nodes/MyNode
  in:
    query: user_query        # workflow input ref
  out: [results]
  config:
    threshold: 0.7
```

## Workflow Scaffold Nodes (`api/nodes/`)

| Node | File | Purpose |
|------|------|---------|
| `LLMNode` | `llm_node.py` | General LLM inference with `{{variable}}` templates |
| `RankerNode` | `ranker_node.py` | LLM candidate ranking with scoring |
| `PipelineConfigNode` | `pipeline_config_node.py` | Pipeline parameter assembly for backend forwarding |

These use `NodeBase` and are part of the workflow engine scaffold (future adoption).

## Optimizer Building Block Nodes (`optimizer_pipeline.json`)

The optimizer steps are building block nodes — service functions with config declared in [`api/config/optimizer_pipeline.json`](../api/config/optimizer_pipeline.json). They use `llm_call()` + `get_node_config()` instead of `NodeBase` wrappers. See [`docs/building-blocks.md`](building-blocks.md) for the type hierarchy.

| Node | Type | Service function | Purpose |
|------|------|-----------------|---------|
| `l1_generate` | `llm/meta` | `prompt_optimizer.l1_generate()` | Generate N candidate PromptState variants |
| `l1_evaluate` | `evaluation` | `prompt_optimizer.l1_evaluate()` | Evaluate candidates via backend, select winner |
| `critique` | `agent` | `critique.run_critique()` | 5-field critique analysis |
| `l2_refine_context` | `llm/meta` | `layer_transitions.refine_context()` | Refine task_context + meta-settings |
| `l3_modify_plan` | `llm/meta` | `layer_transitions.modify_plan()` | Modify strategic plan |

## Config

Three sources merged in order (later wins):

1. **YAML `config:`** — static defaults
2. **YAML `metadata:`** — `model`, `temperature`, `max_tokens` extracted by runner via `setdefault` (so explicit `config:` wins)
3. **`runtime_config`** (M8) — dynamic values from `WorkflowRunner.execute()`

Access: `self.config.get("key", default)`.

## Metrics

`process()` auto-captures `NodeMetrics` (timing, error). LLM nodes can set `self._last_metrics.model`, `.input_tokens`, `.output_tokens` — but note `_last_metrics` is from the *previous* call during `_execute()` (created in `finally` block after return). See `LLMNode` for the established pattern.

## YAML Input Resolution

Handled by `WorkflowRunner._resolve_input()`:

1. `"step_id/output_name"` — step output reference
2. `"input_name"` — workflow input reference
3. Anything else — literal value

## Patterns

- **Workflow scaffold** (`LLMNode`, `RankerNode`): `NodeBase._execute()` pattern — call `get_llm_client()`, parse structured JSON responses
- **Optimizer building blocks** (`l1_generate`, `l2_refine_context`, etc.): service functions using `llm_call()` + `get_node_config()` from `optimizer_pipeline.json`. Step tracing via `observed_step()` context manager.

## Files

| File | Role |
|------|------|
| `api/nodes/base.py` | `NodeBase`, `NodeMetrics` |
| `api/nodes/__init__.py` | Registry: `register_node()`, `get_node_class()` |
| `api/core/workflow_runner.py` | DAG executor, input resolution, Langfuse logging |
| `api/models/workflow.py` | `WorkflowDefinition`, `StepDefinition`, `StepMetadata` |
| `workflows/optimizer_single_pass.yaml` | Example: Init → Grow → Evaluate pipeline |
