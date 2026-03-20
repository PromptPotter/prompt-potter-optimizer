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
