# Milestone 6: CWL Workflow Migration

**Version:** 0.8.0
**Date:** 2026-02-25
**Status:** Planned
**Depends on:** [Roadmap M6](roadmap.md), [ADD v0.7.0](add.md), [M5 Observability](m5-observability.md), [PRD P1.12](prd.md)

---

## Context

**Current state:** The CWL-inspired workflow scaffold is ~70% wired. What works:

- DAG executor with topological sort (`api/core/workflow_runner.py`)
- Node framework with typed I/O (`api/nodes/base.py`, `NodeBase[TInput, TOutput]`)
- Three optimizer nodes: `InitNode`, `GrowFilterNode`, `AnalysisEvalNode` (`api/nodes/optimizer_nodes.py`)
- REST API for workflow execution (`api/routers/workflows.py`)
- Workflow data models with CWL-compatible schema (`api/models/workflow.py`)
- Example single-pass workflow (`workflows/optimizer_single_pass.yaml`)

**What's missing:** Seven gaps (A–G) prevent the notebook from driving optimization through `WorkflowRunner` instead of direct service calls.

---

## Gap Analysis

| Gap | Problem | Resolution |
|-----|---------|------------|
| **A: `backend_url` blank** | `optimizer_single_pass.yaml` has `backend_url: ""` — must be set at runtime, not baked into YAML | Add `runtime_config: dict` param to `WorkflowRunner.execute()`. Node configs merged with runtime_config at execution time. |
| **B: No loop construct** | Feedback cycle is iterative (variable rounds, patience-based stopping). DAG executor does single-pass only. | Create `FeedbackCycleNode` that wraps `run_feedback_cycle()` as a single node. Loop stays in service layer, not in DAG engine. |
| **C: Callbacks can't be YAML** | `on_round_complete`, `on_candidate_eval`, `on_query_eval` are Python callables | Pass callbacks via `runtime_config`, not YAML. Node extracts them at execution time. |
| **D: `generate_suggestions` false** | `optimizer_single_pass.yaml` sets `generate_suggestions: false` but feedback cycle needs it | Expose as `FeedbackCycleNode` config field (default `false`, overridable in YAML). |
| **E: `eval_data` raw input** | Workflow receives `eval_data` as pre-built list. No way to load from disk in YAML. | Create `DatasetLoadNode` that reads experiment data and builds eval dataset. Makes YAML self-contained. |
| **F: `InitNode` doesn't persist** | InitNode creates PromptState in memory only — no project store write | Add optional `project_root` / `backend_id` config to InitNode. When set, persist via ProjectStore. |
| **G: No dependency injection** | `AnalysisEvalNode` instantiates `BackendClient` internally. No way to inject pre-configured instances. | `runtime_config` carries `ProjectStore` / `BackendClient` refs. Nodes check runtime_config before instantiating. |

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Loop construct | Wrapping node (`FeedbackCycleNode`), not native DAG loop | Iterative loop with variable rounds, patience, and conditional routing doesn't fit a static DAG. Service layer already handles it well. |
| Campaign persistence | Partially inside workflow | `FeedbackCycleNode` uses `campaign_registry` internally (via `run_feedback_cycle()`). Workflow doesn't duplicate persistence. |
| `DatasetLoadNode` | Makes YAML self-contained | Workflow can reference experiment ID instead of requiring pre-built eval data. Enables `workflows/*.yaml` to be shareable. |
| `ScanNode` | New node wrapping `sensitivity_scan()` | Enables scan → optimize workflows entirely in YAML. |
| `runtime_config` pattern | Dict passed to `execute()`, merged into node configs | Simple, explicit, no magic. Nodes declare what they need; runner provides it. |

---

## Deliverables

| # | File | Action | What |
|---|------|--------|------|
| 1 | `api/nodes/feedback_cycle_node.py` | CREATE | `FeedbackCycleNode` wrapping `run_feedback_cycle()` with full CycleConfig exposure |
| 2 | `api/nodes/dataset_load_node.py` | CREATE | `DatasetLoadNode` — load experiment, extract eval dataset, output `eval_data` + `session_terms` |
| 3 | `api/nodes/scan_node.py` | CREATE | `ScanNode` wrapping `sensitivity_scan()` for scan workflows |
| 4 | `api/core/workflow_runner.py` | MODIFY | Add `runtime_config: dict` param to `execute()`. Merge into node configs before instantiation. |
| 5 | `workflows/optimization_campaign.yaml` | CREATE | Full optimization workflow: DatasetLoad → FeedbackCycle (replaces notebook direct calls) |
| 6 | `workflows/sensitivity_scan.yaml` | CREATE | Scan workflow: DatasetLoad → Scan |
| 7 | `notebooks/_campaign_lib.py` | MODIFY | Add `run_workflow()` wrapper that calls `WorkflowRunner` with runtime_config |
| 8 | `tests/test_workflow_migration.py` | CREATE | Tests for new nodes, runtime_config injection, YAML workflow execution |

---

## Node Input/Output Contracts

### FeedbackCycleNode

Wraps `run_feedback_cycle()` as a single workflow node. The iterative loop runs inside the node.

```
FeedbackCycleNode
├── Input
│   ├── instruction: str              # optimization instruction
│   ├── eval_data: list[dict]         # evaluation dataset
│   ├── improvement_areas: str = ""   # optional focus areas
│   ├── baseline_prompt_state: dict | None  # skip InitNode if provided
│   ├── baseline_accuracy: float = 0.0
│   └── baseline_results: list | None
├── Output
│   ├── winner_prompt_state: dict     # best PromptState found
│   ├── best_accuracy: float
│   ├── n_rounds: int
│   ├── stop_reason: str              # max_rounds | perfect_score | patience_exhausted | next_action_stop
│   ├── rounds: list[dict]            # per-round results (CycleRoundResult dicts)
│   └── langfuse_trace_id: str | None
└── Config (from YAML + runtime_config merge)
    ├── max_rounds: int = 10
    ├── patience: int = 3
    ├── n_variants: int = 5
    ├── creativity: float = 0.7
    ├── improvement_threshold: float = 0.01
    ├── backend_url: str              # from runtime_config
    ├── backend_id: str = ""
    ├── project_root: str = ""
    ├── generate_suggestions: bool = false
    ├── pipeline_params: dict | None
    ├── session_terms: list[str] | None
    ├── temperature: float = 0.0
    ├── queries_per_eval: int = 0
    └── seed: int = 42
```

**Callbacks:** Extracted from `runtime_config` at execution time:
- `runtime_config["on_round_complete"]` → `CycleConfig` callback
- `runtime_config["on_candidate_eval"]` → forwarded
- `runtime_config["on_query_eval"]` → forwarded

### DatasetLoadNode

Loads experiment data from a synced backend and builds the eval dataset.

```
DatasetLoadNode
├── Input
│   ├── experiment_id: str            # which experiment to load
│   └── backend_id: str = ""          # override (or from config)
├── Output
│   ├── eval_data: list[dict]         # query/expected pairs for evaluation
│   ├── session_terms: list[str]      # terms for init_session()
│   ├── n_queries: int
│   └── experiment_name: str | None
└── Config
    ├── project_root: str             # from runtime_config
    ├── backend_id: str = ""          # fallback if not in input
    └── backend_url: str = ""         # for sync if experiment not cached
```

**Execution:** Reads from ProjectStore (`sync/experiments/{id}.json`). Calls `BackendClient.extract_session_terms()` and `extract_replay_queries()` to build output.

### ScanNode

Wraps `sensitivity_scan()` for workflow-driven scan campaigns.

```
ScanNode
├── Input
│   ├── prompt_state: dict            # baseline PromptState
│   ├── eval_data: list[dict]         # evaluation dataset
│   └── session_terms: list[str] | None
├── Output
│   ├── axis_profiles: list[dict]     # per-axis sensitivity results
│   ├── recommended_axes: list[str]   # axes worth optimizing
│   ├── baseline_accuracy: float
│   └── plan_id: str
└── Config
    ├── backend_url: str              # from runtime_config
    ├── backend_id: str = ""
    ├── project_root: str = ""
    ├── n_variants_per_axis: int = 3
    ├── pipeline_params: dict | None
    └── temperature: float = 0.0
```

---

## runtime_config Injection

The `runtime_config` dict is passed to `WorkflowRunner.execute()` and merged into each node's config:

```python
# Before (current)
context = await runner.execute(inputs={"instruction": "...", "eval_data": data})

# After (M6)
context = await runner.execute(
    inputs={"instruction": "...", "experiment_id": "exp_001"},
    runtime_config={
        "backend_url": "http://localhost:8000",
        "backend_id": "termnorm",
        "project_root": "/path/to/project",
        "on_round_complete": my_callback,
        "on_candidate_eval": my_eval_callback,
    },
)
```

**Merge rule:** `runtime_config` values override YAML config values. Node-specific config in YAML takes precedence over `runtime_config` only if explicitly set to a non-default value.

---

## Example Workflow YAML

```yaml
# workflows/optimization_campaign.yaml
cwlVersion: v1.2
class: Workflow
id: optimization_campaign
label: "Full optimization campaign"
description: "Load dataset → run feedback cycle → output best prompt"

inputs:
  experiment_id:
    type: string
    description: "Experiment to load for evaluation"
  instruction:
    type: string
    description: "Optimization instruction for InitNode"
  improvement_areas:
    type: string
    default: ""

steps:
  - id: load_data
    run: nodes/DatasetLoadNode
    in:
      experiment_id: experiment_id
    out: [eval_data, session_terms, n_queries]
    config:
      backend_id: ""    # from runtime_config

  - id: optimize
    run: nodes/FeedbackCycleNode
    in:
      instruction: instruction
      eval_data: load_data/eval_data
      improvement_areas: improvement_areas
    out: [winner_prompt_state, best_accuracy, n_rounds, stop_reason, rounds]
    config:
      max_rounds: 10
      patience: 3
      n_variants: 5
      session_terms: load_data/session_terms  # step reference in config

outputs:
  winner_prompt_state:
    type: object
    outputSource: optimize/winner_prompt_state
  best_accuracy:
    type: float
    outputSource: optimize/best_accuracy
  n_rounds:
    type: int
    outputSource: optimize/n_rounds
  stop_reason:
    type: string
    outputSource: optimize/stop_reason
```

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.0 | Write M6 spec | 1 | — | This document |
| 6.1 | runtime_config injection | 1 | 6.0 | Modify `WorkflowRunner.execute()` to accept and merge `runtime_config`. Update node instantiation to receive merged config. Tests for config merge behavior. |
| 6.2 | DatasetLoadNode | 1 | 6.1 | Create `dataset_load_node.py`. Read experiment from ProjectStore, extract eval data + session terms. Unit tests. |
| 6.3 | FeedbackCycleNode | 1 | 6.1 | Create `feedback_cycle_node.py`. Wrap `run_feedback_cycle()`, extract callbacks from runtime_config. Unit tests with mocked feedback cycle. |
| 6.4 | ScanNode + YAML workflows | 1 | 6.2, 6.3 | Create `scan_node.py`. Write `optimization_campaign.yaml` and `sensitivity_scan.yaml`. Integration tests for YAML-driven execution. |
| 6.5 | Notebook migration | 1 | 6.4 | Add `run_workflow()` to `_campaign_lib.py`. Verify notebook can drive optimization through WorkflowRunner. Update E2E test. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 6.1 | `api/core/workflow_runner.py` (execute method, _resolve_step_inputs), `api/nodes/base.py` (NodeBase.__init__ config handling) |
| 6.2 | `api/services/backend_client.py` (extract_session_terms, extract_replay_queries), `api/services/project_store.py` (load_experiment) |
| 6.3 | `api/services/feedback_cycle.py` (run_feedback_cycle signature, CycleConfig), `api/nodes/optimizer_nodes.py` (AnalysisEvalNode for pattern reference) |
| 6.4 | `workflows/optimizer_single_pass.yaml` (existing YAML format), `api/services/search/smart_search.py` (sensitivity_scan signature) |
| 6.5 | `notebooks/_campaign_lib.py` (current workflow), `tests/test_e2e_optimization.py` (E2E test pattern) |

---

## Entry Criteria

- M5 exit gate passed (observability integrated, LLM retry working)
- Existing workflow scaffold passes its tests
- All existing tests pass (`pytest -v --tb=short`)

## Exit Criteria

- `optimization_campaign.yaml` executes end-to-end via `WorkflowRunner` with `runtime_config`
- `sensitivity_scan.yaml` executes scan workflow
- `_campaign_lib.py` has `run_workflow()` function that drives optimization through `WorkflowRunner`
- `runtime_config` correctly injects `backend_url`, `project_root`, callbacks into nodes
- All new nodes have typed I/O models and unit tests
- All existing tests still pass

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_runtime_config_merge` | Unit | `runtime_config` values override YAML defaults; explicit YAML values preserved |
| `test_dataset_load_node` | Unit | Loads experiment from ProjectStore, extracts eval_data + session_terms |
| `test_feedback_cycle_node` | Unit | Wraps `run_feedback_cycle()` with correct CycleConfig construction |
| `test_feedback_cycle_node_callbacks` | Unit | Callbacks extracted from `runtime_config` and forwarded |
| `test_scan_node` | Unit | Wraps `sensitivity_scan()` with correct config |
| `test_optimization_yaml` | Integration | Load `optimization_campaign.yaml`, execute with mocked backend, verify end-to-end |
| `test_scan_yaml` | Integration | Load `sensitivity_scan.yaml`, execute with mocked backend |
| `test_campaign_lib_workflow` | Integration | `run_workflow()` in `_campaign_lib.py` drives optimization through WorkflowRunner |
