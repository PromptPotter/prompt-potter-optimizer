# api/core — Building Blocks + Workflow Engine Scaffold

## Building block primitive (`llm_call.py`)

`llm_call()` is the shared LLM interaction primitive. Config-driven from `api/config/optimizer_pipeline.json` with runtime overrides. Used by all optimizer building block nodes (`generate_candidates`, `refine_context`, `modify_plan`, `CritiqueAgent`). `get_node_config(node_name)` loads node configs from the pipeline declaration.

See [`docs/building-blocks.md`](../../docs/building-blocks.md) for the full standard.

## Workflow engine scaffold (future architecture, not dead code)

CWL-inspired workflow engine — an aligned direction for eventually expressing both TermNorm and optimizer pipelines as declarative workflows. Registered but zero callers today:

- **LLMNode** (`llm_node.py`) — General-purpose LLM inference with {{variable}} templates
- **RankerNode** (`ranker_node.py`) — LLM-based candidate ranking with scoring
- **PipelineConfigNode** (`pipeline_config_node.py`) — Pipeline parameter assembly

See [`docs/specs/m6-pipeline-composability.md`](../../docs/specs/m6-pipeline-composability.md) for migration roadmap.

## Terminology bridge: scaffold ↔ building blocks

The scaffold and building block standard describe the same architectural concepts with different vocabulary. This mapping connects them:

| Scaffold (api/nodes/, api/core/) | Building block (docs/building-blocks.md) | Status |
|---|---|---|
| `NodeBase[TInput, TOutput]` | `async def run(ctx: Ctx) -> None` base | Scaffold has Pydantic-typed dispatch; building blocks envision shared-context dispatch. M8 will reconcile. |
| `WorkflowRunner` (DAG engine, topological sort) | Pipeline runner composing building block nodes | Scaffold is fully implemented. Building block runner is future. |
| `register_node()` / `_NODE_REGISTRY` | Building block type registration | Same concept, different registries. |
| `NodeMetrics` | `StepTrace` (`api/services/obs/step_tracer.py`) | `StepTrace` is the active runtime equivalent. |
| `LLMNode` | `llm` type | Raw prompt → response. |
| `RankerNode` | `llm/structured` type | Template + output schema. |
| `PipelineConfigNode` | `deterministic` type | Pure function. |

### How `observed_step()` bridges the gap (M7 Wave G)

`observed_step()` in `step_tracer.py` is the runtime bridge between direct service calls and the building block tracing model. It provides the same step-level timing + observability that `NodeBase.process()` would provide, without requiring Pydantic I/O wrappers or the `WorkflowRunner` dispatch chain.

`step_type` values in `observed_step()` callers use building block type names (`llm/meta`, `evaluation`) from `optimizer_pipeline.json`, connecting the active tracing to the declared pipeline structure.
