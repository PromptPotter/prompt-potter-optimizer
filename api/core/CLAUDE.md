# api/core — Workflow Engine Scaffold

CWL-inspired workflow engine — **future architecture, not dead code**. M6 migration will wrap service-layer functions into workflow nodes.

## Key files

| File | Role |
|------|------|
| `workflow_runner.py` | YAML-driven executor: topological sort (Kahn's), input resolution, step loop |
| `../nodes/base.py` | `NodeBase[TInput, TOutput]` — generic Pydantic I/O, `_execute()` template method, `NodeMetrics` |
| `../nodes/__init__.py` | Auto-registration by class name |
| `../evaluators/` | `EvaluatorBase`, registry, field-level rules |
| `../models/workflow.py` | Workflow data models |
| `../routers/workflows.py` | REST endpoints for workflow execution |
| `../../workflows/*.yaml` | Workflow definitions (CWL v1.2 structure) |

## CWL conventions

Workflow YAML follows CWL v1.2 structure: `cwlVersion`, `class`, `steps`, `outputs`. Each step references a node class and declares its inputs/outputs.

## Node types

6 built-in nodes: `LLMNode`, `RankerNode`, `PipelineConfigNode`, `InitNode`, `GrowFilterNode`, `AnalysisEvalNode`.

Nodes use `NodeBase[TInput, TOutput]` — a generic base with Pydantic models for input/output typing and a `_execute()` template method that subclasses implement.

## Migration intent

M6 adds `PipelineSchema` (replacing hardcoded TermNorm constants) then wraps service-layer functions into workflow nodes. The notebook will drive optimization via `WorkflowRunner` instead of direct service calls. See [`docs/specs/m6-workflow-migration.md`](../../docs/specs/m6-workflow-migration.md).
