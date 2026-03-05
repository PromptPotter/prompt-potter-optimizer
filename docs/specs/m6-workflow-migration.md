# Milestone 6: PipelineSchema + Cross-Repo Pipeline Composability

**Version:** 0.10.0
**Date:** 2026-03-05
**Status:** Waves 0-3 complete, Wave 4 in progress
**Depends on:** [Roadmap M6](roadmap.md), [ADD v0.9.0](add.md), [PRD P1.12, P1.14](prd.md)

---

## Context

**Waves 0-3 are complete.** PipelineSchema is implemented with derivation methods replacing all Wave 2 chokepoints. TermNorm exposes `GET /pipeline` with the full 6-step config, fuzzy matcher is simplified, and unified tracing is in place. Wave 5 (notebook migration + Docker) moved to M7.

**Remaining work:** Wave 4 wires existing service functions into the workflow engine scaffold (`api/core/`, `api/nodes/`).

**Exit gate (reframed):** MVP performance validation — prove TermNorm accuracy improvement from ~15% to >90% using the PipelineSchema-driven evaluation pipeline.

Two repos affected:
- **TermNorm** (`C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`) — Waves 0, 1, 3 (complete)
- **PromptPotter** (`C:\Users\dsacc\Desktop\PromptPotter\prompt-potter-optimizer\`) — Waves 2, 4

---

## The 13 Chokepoints

**Resolved (Wave 2):**

| # | Hardcoded Thing | Fix |
|---|----------------|-----|
| 1 | `PIPELINE_STEP_PARAMS` | `schema.step_param_keys()` |
| 2 | `_STEP_PARAM_KEYS` | `schema.step_param_keys()` (minus `ranking_prompt`) |
| 3 | `OBS_EXTRACTION_MAP` | `schema.obs_extraction_map()` |
| 6 | `REQUIRED_PIPELINE_KEY` | `schema.required_step` |
| 8 | `REQUIRED_TEMPLATE_VARS` | `schema.template_variables` |
| 9 | `DATASET_NAME` | `schema.dataset_name` |

**Remaining (M7 — require ConnectorProtocol):**

| # | Hardcoded Thing | Fix |
|---|----------------|-----|
| 4 | `parse_bom_material()` | Query parser registry |
| 5 | GT mapping (bom→entry) | `schema.query_config` |
| 7 | Hit@1 exact match | `schema.eval_config` |
| 10 | `skip_llm_ranking` | Generic `excluded_steps` |
| 11 | `BackendClient` concrete | `ConnectorProtocol` |
| 12 | `ExecutionResultItem.bom_material` | Generic `query_fields` |
| 13 | `extract_session_terms()` | `schema.session_config` |

---

## Gap Analysis

Gaps A-J identified and resolved during planning. Key decisions: `FeedbackCycleNode` wraps the iterative loop (no native DAG loop construct), `runtime_config` dict carries DI params, `DatasetLoadNode` makes YAML self-contained. Full pipeline visibility via TermNorm's `GET /pipeline` (Gap H, resolved Wave 1).

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| PipelineSchema placement | Wave 2 prerequisite, not parallel | All Wave 4 nodes need schema for DI; hardcoded dicts must be gone before node work starts |
| Loop construct | Wrapping node (`FeedbackCycleNode`), not native DAG loop | Iterative loop with patience and conditional routing doesn't fit a static DAG |
| `DatasetLoadNode` | Makes YAML self-contained | Workflow can reference experiment ID instead of requiring pre-built eval data |
| `ScanNode` | New node wrapping `sensitivity_scan()` | Enables scan → optimize workflows entirely in YAML |
| `runtime_config` pattern | Dict passed to `execute()`, merged into node configs | Simple, explicit. Nodes declare what they need; runner provides it. Carries `PipelineSchema` alongside `BackendClient`. |
| TermNorm default schema | Static `TERMNORM_DEFAULT_SCHEMA` in `pipeline_discovery.py` | Offline use without backend. Factory also parses live `GET /pipeline` response. |
| Full pipeline in schema | `PipelineStep` gets `runtime` and `short_circuit` fields | Frontend steps (cache, fuzzy) become first-class; PipelineSchema describes the complete pipeline |
| Unified tracing | One trace per query, all steps as observations | Enables fuzzy hit rate analysis, full pipeline latency breakdown, and end-to-end reproducibility |

---

## Deliverables

**Waves 0-3 (complete):** See [`docs/connectors/termnorm.md`](../connectors/termnorm.md) for the pipeline config contract. PipelineSchema model and factory: [`api/models/pipeline_schema.py`](../../api/models/pipeline_schema.py), [`api/services/pipeline_discovery.py`](../../api/services/pipeline_discovery.py). See [`api/models/CLAUDE.md`](../../api/models/CLAUDE.md) for field details.

**Wave 4: Workflow Nodes** (PromptPotter repo — active):

| # | File | Action | What |
|---|------|--------|------|
| 8 | `api/core/workflow_runner.py` | MODIFY | Add `runtime_config: dict` to `execute()`, merge into node configs |
| 9 | `api/nodes/feedback_cycle_node.py` | CREATE | `FeedbackCycleNode` wrapping `run_feedback_cycle()` |
| 10 | `api/nodes/dataset_load_node.py` | CREATE | `DatasetLoadNode` — load experiment, build eval dataset |
| 11 | `api/nodes/scan_node.py` | CREATE | `ScanNode` wrapping `sensitivity_scan()` |
| 12 | `workflows/optimization_campaign.yaml` | CREATE | Full optimization workflow: DatasetLoad → FeedbackCycle |
| 13 | `workflows/sensitivity_scan.yaml` | CREATE | Scan workflow: DatasetLoad → Scan |
| 16 | `tests/test_workflow_migration.py` | CREATE | Tests for new nodes, runtime_config, YAML workflows |

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
    ├── pipeline_schema: PipelineSchema | None  # from runtime_config
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
    ├── backend_url: str = ""         # for sync if experiment not cached
    └── pipeline_schema: PipelineSchema | None  # for extraction config
```

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
    ├── pipeline_schema: PipelineSchema | None
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
        "pipeline_schema": schema,           # PipelineSchema instance
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

## Dependencies

```
Waves 0-3 (Complete)
  ↓
Wave 4 (workflow nodes) ← only remaining M6 work
```

---

## Work Packages

**Waves 0-3: Complete.** TermNorm cleanup (6.0a), GET /pipeline (6.0b), PipelineSchema model + factory (6.1), schema derivation replacement (6.2), n8n mapper spec (6.0d), unified tracing (6.0c).

**Wave 4: Workflow Nodes** (PromptPotter repo)

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.3 | runtime_config injection in WorkflowRunner | 1 | 6.2 | Modify `WorkflowRunner.execute()` to accept and merge `runtime_config` (includes `PipelineSchema`). Update node instantiation. Tests for config merge behavior. |
| 6.4 | DatasetLoadNode | 1 | 6.3 | Create `dataset_load_node.py`. Read experiment from ProjectStore, extract eval data + session terms. Unit tests. |
| 6.5 | FeedbackCycleNode | 1 | 6.3 | Create `feedback_cycle_node.py`. Wrap `run_feedback_cycle()`, extract callbacks from runtime_config. Unit tests with mocked feedback cycle. |
| 6.6 | ScanNode + YAML workflows | 1 | 6.4, 6.5 | Create `scan_node.py`. Write `optimization_campaign.yaml` and `sensitivity_scan.yaml`. Integration tests for YAML-driven execution. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 6.3 | `api/core/workflow_runner.py` (execute method, _resolve_step_inputs), `api/nodes/base.py` (NodeBase.__init__ config handling) |
| 6.4 | `api/services/backend_client.py` (extract_session_terms, extract_replay_queries), `api/services/project_store.py` (load_experiment) |
| 6.5 | `api/services/campaign/feedback_cycle.py` (run_feedback_cycle signature, CycleConfig), `api/nodes/optimizer_nodes.py` (AnalysisEvalNode for pattern reference) |
| 6.6 | `workflows/optimizer_single_pass.yaml` (existing YAML format), `api/services/search/smart_search.py` (sensitivity_scan signature) |

---

## Entry Criteria

- M5 exit gate passed (observability integrated, LLM retry working)
- Existing workflow scaffold passes its tests
- All existing tests pass (`pytest -v --tb=short`)

## Exit Criteria

- **MVP performance validation:** TermNorm accuracy from ~15% to >90% using PipelineSchema-driven evaluation
- `optimization_campaign.yaml` executes end-to-end via `WorkflowRunner` with `runtime_config`
- `sensitivity_scan.yaml` executes scan workflow
- `runtime_config` correctly injects `backend_url`, `project_root`, `PipelineSchema`, callbacks
- All new nodes have typed I/O models and unit tests
- All existing tests still pass

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_pipeline_schema_model` | Unit | (Implemented) PipelineSchema, PipelineStep, ObservationMapping construction and validation |
| `test_pipeline_schema_derivation` | Unit | (Implemented) `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `backend_steps()`, `frontend_steps()` |
| `test_pipeline_discovery_factory` | Unit | (Implemented) Parse full 6-step `GET /pipeline` JSON into PipelineSchema |
| `test_schema_replaces_constants` | Integration | (Implemented) Services use schema methods instead of hardcoded constants |
| `test_runtime_config_merge` | Unit | `runtime_config` values override YAML defaults; explicit YAML values preserved |
| `test_dataset_load_node` | Unit | Loads experiment from ProjectStore, extracts eval_data + session_terms |
| `test_feedback_cycle_node` | Unit | Wraps `run_feedback_cycle()` with correct CycleConfig construction |
| `test_feedback_cycle_node_callbacks` | Unit | Callbacks extracted from `runtime_config` and forwarded |
| `test_scan_node` | Unit | Wraps `sensitivity_scan()` with correct config |
| `test_optimization_yaml` | Integration | Load `optimization_campaign.yaml`, execute with mocked backend, verify end-to-end |
| `test_scan_yaml` | Integration | Load `sensitivity_scan.yaml`, execute with mocked backend |
