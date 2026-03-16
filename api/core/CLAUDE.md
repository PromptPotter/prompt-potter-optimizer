# api/core — Workflow Engine Scaffold

CWL-inspired workflow engine — **future architecture, not dead code**. The node base classes and optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode) are actively used by the feedback cycle. M6 Wave 2 (PipelineSchema) complete; workflow node migration deferred to M7.

## Future / Scaffold Nodes (no consumers yet)

Registered but zero callers today. Building blocks for the workflow server vision:

- **LLMNode** (`llm_node.py`) — General-purpose LLM inference with {{variable}} templates
- **RankerNode** (`ranker_node.py`) — LLM-based candidate ranking with scoring
- **PipelineConfigNode** (`pipeline_config_node.py`) — Pipeline parameter assembly

See [`docs/specs/m6-pipeline-composability.md`](../../docs/specs/m6-pipeline-composability.md) for migration roadmap.
