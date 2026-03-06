# api/core — Workflow Engine Scaffold

CWL-inspired workflow engine — **future architecture, not dead code**. M6 Wave 2 (PipelineSchema) is complete. Remaining M6 waves will wrap service-layer functions into workflow nodes.

## Key files

| File | Role |
|------|------|
| `workflow_runner.py` | YAML-driven executor: topological sort (Kahn's), input resolution, step loop |
| `../nodes/base.py` | `NodeBase[TInput, TOutput]` — generic Pydantic I/O, `_execute()` template method, `NodeMetrics` |
| `../nodes/__init__.py` | Auto-registration by class name |
| `../evaluators/` | `EvaluatorBase`, registry, field-level rules |
| `../models/workflow.py` | Workflow data models |
| `../routers/workflows.py` | REST endpoint: `POST /workflows/execute` only (management/evaluation/discovery endpoints removed) |
| `../../workflows/*.yaml` | Workflow definitions (CWL v1.2 structure) |

## CWL conventions

Workflow YAML follows CWL v1.2 structure: `cwlVersion`, `class`, `steps`, `outputs`. Each step references a node class and declares its inputs/outputs.

## Node types

6 built-in nodes: `LLMNode`, `RankerNode`, `PipelineConfigNode`, `InitNode`, `GrowFilterNode`, `AnalysisEvalNode`.

Nodes use `NodeBase[TInput, TOutput]` — a generic base with Pydantic models for input/output typing and a `_execute()` template method that subclasses implement.

## Future / Scaffold Nodes (no consumers yet)

Registered but zero callers today. Building blocks for the workflow server vision:

- **LLMNode** (`llm_node.py`) — General-purpose LLM inference with {{variable}} templates
- **RankerNode** (`ranker_node.py`) — LLM-based candidate ranking with scoring
- **PipelineConfigNode** (`pipeline_config_node.py`) — Pipeline parameter assembly

## Migration intent

M6 Wave 2 added PipelineSchema (derivation methods now wired through services). Workflow node migration (Wave 4) deferred to M7. M6 now focuses on composite scoring (Wave 5) and node-role-driven metrics (Wave 6). See [`docs/specs/m6-pipeline-composability.md`](../../docs/specs/m6-pipeline-composability.md).
