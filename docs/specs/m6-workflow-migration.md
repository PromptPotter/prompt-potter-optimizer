# Milestone 6: PipelineSchema + Cross-Repo Pipeline Composability

**Version:** 1.0.0
**Date:** 2026-02-28
**Status:** In Progress (Wave 2 complete)
**Depends on:** [Roadmap M6](roadmap.md), [ADD v0.9.0](add.md), [PRD P1.12, P1.14](prd.md)

---

## Context

**Current state:** The CWL-inspired workflow scaffold is ~70% wired. What works:

- DAG executor with topological sort (`api/core/workflow_runner.py`)
- Node framework with typed I/O (`api/nodes/base.py`, `NodeBase[TInput, TOutput]`)
- Three optimizer nodes: `InitNode`, `GrowFilterNode`, `AnalysisEvalNode` (`api/nodes/optimizer_nodes.py`)
- REST API for workflow execution (`api/routers/workflows.py`)
- Workflow data models with CWL-compatible schema (`api/models/workflow.py`)
- Example single-pass workflow (`workflows/optimizer_single_pass.yaml`)

**PipelineSchema** is the backend-agnostic pipeline description model. It provides derivation methods that services call instead of using hardcoded constants. `PipelineSchema` is the prerequisite foundation for all workflow node work in this milestone.

**Cross-repo extension:** The TermNorm pipeline has a 3-tier matching flow: Cache → Fuzzy → LLM Research. But the pipeline config (synced via experiment data) only describes the backend steps (entity_profiling, token_matching, llm_ranking). Fuzzy matching runs entirely in the JS frontend and is invisible to observability, non-reproducible, and can't be optimized by PromptPotter. This milestone extends M6 with TermNorm-side work (Waves 0-1, Wave 3) so that PipelineSchema describes the **full pipeline** including frontend steps.

Two repos affected:
- **TermNorm** (`C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`) — Waves 0, 1, 3
- **PromptPotter** (`C:\Users\dsacc\Desktop\PromptPotter\prompt-potter-optimizer\`) — Waves 2, 4, 5

For the TermNorm work packages, see the self-contained task doc: [`TermNorm: docs/pipeline-composability.md`](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md).

---

## The 13 Chokepoints

| # | Hardcoded Thing | File | Fix (which wave) |
|---|----------------|------|-----------------|
| 1 | `PIPELINE_STEP_PARAMS` | `backend_client.py:29` | Wave 2 — `schema.step_param_keys()` |
| 2 | `_STEP_PARAM_KEYS` | `pipeline_nodes.py:33` | Wave 2 — `schema.step_param_keys()` |
| 3 | `OBS_EXTRACTION_MAP` | `eval_dataset.py:25` | Wave 2 — `schema.obs_extraction_map()` |
| 4 | `parse_bom_material()` | `query_utils.py:6` | M7 — query parser registry |
| 5 | GT mapping (bom→entry) | `backend_client.py:246` | M7 — `schema.query_config` |
| 6 | `REQUIRED_PIPELINE_KEY` | `eval_dataset.py:42` | Wave 2 — `schema.required_step` |
| 7 | Hit@1 exact match | `prompt_eval.py:249` | M7 — `schema.eval_config` |
| 8 | `REQUIRED_TEMPLATE_VARS` | `grid_core.py:43` | Wave 2 — `schema.template_variables()` |
| 9 | `DATASET_NAME = "termnorm_ground_truth"` | `langfuse_push.py:54` | Wave 2 — schema-derived name |
| 10 | `skip_llm_ranking` | 5+ files | M7 — generic `excluded_steps` |
| 11 | `BackendClient` concrete | everywhere | M7 — `ConnectorProtocol` |
| 12 | `ExecutionResultItem.bom_material` | `backend.py:31` | M7 — generic `query_fields` |
| 13 | `extract_session_terms()` | `backend_client.py:228` | M7 — `schema.session_config` |

**This milestone resolves:** chokepoints 1, 2, 3, 6, 8, 9 (Wave 2).
**M7 resolves:** chokepoints 4, 5, 7, 10, 11, 12, 13 (require ConnectorProtocol).

---

## Gap Analysis

| Gap | Problem | Resolution |
|-----|---------|------------|
| **A: `backend_url` blank** | `optimizer_single_pass.yaml` has `backend_url: ""` — must be set at runtime | Add `runtime_config: dict` param to `WorkflowRunner.execute()`. Merged into node configs at execution time. |
| **B: No loop construct** | Feedback cycle is iterative; DAG executor does single-pass only | `FeedbackCycleNode` wraps `run_feedback_cycle()` as a single node. Loop stays in service layer. |
| **C: Callbacks can't be YAML** | `on_round_complete`, etc. are Python callables | Pass callbacks via `runtime_config`, not YAML. |
| **D: `generate_suggestions` false** | YAML sets `generate_suggestions: false` but feedback cycle needs it | Expose as `FeedbackCycleNode` config field (default `false`, overridable). |
| **E: `eval_data` raw input** | No way to load from disk in YAML | `DatasetLoadNode` reads experiment data and builds eval dataset. |
| **F: `InitNode` doesn't persist** | InitNode creates PromptState in memory only | Add optional `project_root` / `backend_id` config. When set, persist via ProjectStore. |
| **G: No dependency injection** | `AnalysisEvalNode` instantiates `BackendClient` internally | `runtime_config` carries `ProjectStore`, `BackendClient`, and `PipelineSchema` refs. Nodes check runtime_config before instantiating. |
| **H: Frontend steps invisible** | Pipeline config only describes backend steps; cache and fuzzy matching in JS frontend are untracked | TermNorm exposes `GET /pipeline` with full 6-step pipeline config (Wave 1). PipelineSchema includes `runtime` and `short_circuit` fields (Wave 2). |
| **I: Fuzzy matcher overparameterized** | 3 fuzzy thresholds, bidirectional search with different semantics | Simplify to single-direction, single-threshold before building pipeline infrastructure (Wave 0). |
| **J: Separate traces per method** | Cache/fuzzy matches create independent traces; no unified pipeline view | Trace lifecycle endpoints in TermNorm; one trace per query with all steps as observations (Wave 3). |

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
| Docker Compose | Absorbed from M4.3 | Workflow packaging fits naturally alongside notebook migration |
| TermNorm cleanup first | Wave 0 before pipeline contract | Simplify fuzzy matcher (single threshold, single direction) before codifying its config in pipeline JSON |
| Full pipeline in schema | `PipelineStep` gets `runtime` and `short_circuit` fields | Frontend steps (cache, fuzzy) become first-class; PipelineSchema describes the complete pipeline |
| Unified tracing | One trace per query, all steps as observations | Enables fuzzy hit rate analysis, full pipeline latency breakdown, and end-to-end reproducibility |

---

## PipelineSchema Model Sketch

File: `api/models/pipeline_schema.py`

```python
class PipelineStep(BaseModel):
    """One step in the pipeline (frontend or backend)."""
    name: str                        # e.g. "cache_lookup", "llm_ranking"
    type: str                        # "LLMGeneration" | "DeterministicFunction" | "ExternalService"
    runtime: str = "backend"         # "frontend" | "backend" — who executes this step
    short_circuit: bool = False      # if True, pipeline stops when this step produces a result
    param_keys: list[str] = []       # tunable parameter names for this step
    observation_name: str | None = None  # Langfuse observation name (if tracked)

class ObservationMapping(BaseModel):
    """Maps a pipeline observation to eval_dataset fields."""
    obs_name: str                    # observation name in traces
    target_field: str                # field name in pipeline_data dict
    extract: str = "output"          # what to extract: "output", "model", "timing"

class PipelineSchema(BaseModel):
    """Backend-agnostic pipeline description — single source of truth.

    PromptState = what prompt. PipelineSchema = what pipeline.
    Every service becomes: f(PromptState, PipelineSchema, eval_data) → scores
    """
    name: str                        # e.g. "termnorm"
    version: str = ""                # pipeline config version (e.g. "v2.0")
    display_name: str = ""
    steps: list[PipelineStep]
    observation_mappings: list[ObservationMapping] = []
    required_step: str = ""          # step that must produce output for eval
    template_variables: list[str] = []  # required vars in prompt template
    dataset_name: str = ""           # Langfuse dataset name

    def step_param_keys(self) -> dict[str, list[str]]:
        """Return tunable parameter names keyed by step name."""

    def obs_extraction_map(self) -> dict[str, dict]:
        """Return observation-to-field extraction rules."""

    def langfuse_type_map(self) -> dict[str, str]:
        """Return step name to Langfuse observation type mapping."""

    def backend_steps(self) -> list[PipelineStep]:
        """Return only steps with runtime='backend'."""

    def frontend_steps(self) -> list[PipelineStep]:
        """Return only steps with runtime='frontend'."""
```

**Factory:** `api/services/pipeline_discovery.py`
- `parse_pipeline_response(json) → PipelineSchema` — from `GET /pipeline` response (full 6-step config)
- `TERMNORM_DEFAULT_SCHEMA` — static fallback for offline use (includes frontend steps)

---

## Pipeline Config Contract (TermNorm → PromptPotter)

TermNorm's `GET /pipeline` endpoint returns the complete pipeline config. This is the contract PipelineSchema parses.

```json
{
  "name": "TermNorm",
  "version": "v1.1",
  "nodes": {
    "cache_lookup": {
      "type": "DeterministicFunction",
      "short_circuit": true,
      "config": {}
    },
    "fuzzy_matching": {
      "type": "DeterministicFunction",
      "short_circuit": true,
      "config": { "threshold": 70, "scorer": "WRatio", "limit": 5 }
    },
    "web_search": {
      "type": "ExternalService",
      "config": { "max_sites": 7, "num_results": 20, "content_char_limit": 800, "raw_content_limit": 5000 }
    },
    "entity_profiling": {
      "type": "LLMGeneration",
      "config": {
        "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "temperature": 0.3, "max_tokens": 1800, "output_format": "json",
        "prompt_family": "entity_profiling", "prompt_version": 1,
        "schema_family": "entity_profile", "schema_version": 1
      }
    },
    "token_matching": {
      "type": "DeterministicFunction",
      "config": { "max_token_candidates": 20, "relevance_weight_core": 0.7 }
    },
    "llm_ranking": {
      "type": "LLMGeneration",
      "config": {
        "model": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "temperature": 0.0, "max_tokens": 4000, "ranking_sample_size": 20,
        "prompt_family": "llm_ranking", "prompt_version": 1,
        "schema_family": "llm_ranking_output", "schema_version": 1
      }
    }
  },
  "pipelines": {
    "default": ["web_search", "entity_profiling", "token_matching", "llm_ranking"],
    "with_fuzzy": ["fuzzy_matching", "web_search", "entity_profiling", "token_matching", "llm_ranking"]
  },
  "resolved_schemas": {
    "entity_profile/1": {
      "family": "entity_profile", "version": 1,
      "description": "Entity profile extraction schema",
      "fields": ["entity_name", "core_concept", "distinguishing_features", "..."]
    },
    "llm_ranking_output/1": {
      "family": "llm_ranking_output", "version": 1,
      "description": "LLM ranking step output schema",
      "fields": ["profile_summary", "core_concept_description", "ranked_candidates"]
    }
  },
  "resolved_prompts": {
    "entity_profiling/1": {
      "family": "entity_profiling", "version": 1,
      "template_variables": ["query", "format_string", "combined_text"],
      "template": "You are a comprehensive technical database API..."
    },
    "llm_ranking/1": {
      "family": "llm_ranking", "version": 1,
      "template_variables": ["core_concept", "entity_profile_json", "matches"],
      "template": "You are a candidate evaluation expert..."
    }
  }
}
```

Key fields:
- `nodes`: node definitions with type, config, and optional `short_circuit` flag
- `pipelines`: named pipeline variants — ordered lists of node names to execute
- `type`: step category — `"LLMGeneration"`, `"DeterministicFunction"`, `"ExternalService"`
- `short_circuit`: if `true`, pipeline stops when this step produces a result
- `schema_family`/`prompt_family`: references into TermNorm's on-disk registries (LLMGeneration nodes only)
- `resolved_schemas`: output schemas resolved from the schema registry, keyed by `{family}/{version}`
- `resolved_prompts`: prompt templates resolved from the prompt registry, keyed by `{family}/{version}`

---

## Deliverables

**TermNorm repo** (cross-repo prereqs — see [TermNorm task doc](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md)):

| # | File (TermNorm) | Action | What | Wave |
|---|------|--------|------|:----:|
| T1 | `src/matchers/matchers.js` | MODIFY | Simplify `findFuzzyMatch()` — single direction, single threshold | 0 |
| T2 | `src/config/config.js` | MODIFY | Collapse `FUZZY_THRESHOLDS` to single `FUZZY_THRESHOLD = 0.7` | 0 |
| T3 | `src/services/normalizer.js` | MODIFY | Update `findFuzzyMatch()` call, drop threshold args | 0 |
| T4 | `backend-api/config/pipeline.json` | CREATE | Full 6-step pipeline config JSON | 1 |
| T5 | `backend-api/api/pipeline.py` | CREATE | `GET /pipeline` endpoint | 1 |
| T6 | `backend-api/main.py` | MODIFY | Register pipeline router | 1 |
| T7 | `src/services/normalizer.js` | MODIFY | Fetch pipeline config, use `fuzzy_matching.config.threshold` | 1 |
| T8 | `backend-api/api/pipeline.py` | MODIFY | Add `POST /pipeline/trace`, `POST /pipeline/steps` | 3 |
| T9 | `backend-api/utils/langfuse_logger.py` | MODIFY | `log_pipeline()` accepts optional `trace_id` | 3 |
| T10 | `backend-api/api/research_pipeline.py` | MODIFY | `/matches` accepts optional `trace_id` | 3 |
| T11 | `src/services/normalizer.js` | MODIFY | Trace integration in `processTermNormalization()` | 3 |
| T12 | `src/utils/api-fetch.js` | MODIFY | Add `createPipelineTrace()`, `reportPipelineStep()` helpers | 3 |

**PromptPotter repo:**

| # | File | Action | What | Wave |
|---|------|--------|------|:----:|
| 1 | `api/models/pipeline_schema.py` | CREATE | `PipelineSchema`, `PipelineStep`, `ObservationMapping` models (with `runtime`, `short_circuit`) | 2 |
| 2 | `api/services/pipeline_discovery.py` | CREATE | Factory: `parse_pipeline_response()` parses `GET /pipeline` → PipelineSchema, merging `resolved_schemas`/`resolved_prompts` onto `PipelineStep` objects as `StepOutputSchema`/`StepPromptMeta` (live always wins). Static `TERMNORM_DEFAULT_SCHEMA` carries structural metadata only — no hardcoded `output_schema` or `prompt_meta`. | 2 |
| 3 | `tests/test_pipeline_schema.py` | CREATE | Schema model tests, factory tests, derivation method tests | 2 |
| 4 | `api/services/backend_client.py` | MODIFY | Replace `PIPELINE_STEP_PARAMS` with `schema.step_param_keys()` | 2 |
| 5 | `api/services/search/eval_dataset.py` | MODIFY | Replace `OBS_EXTRACTION_MAP` with `schema.obs_extraction_map()` | 2 |
| 6 | `api/services/search/grid_core.py` | MODIFY | Replace `REQUIRED_TEMPLATE_VARS` with `schema.template_variables` | 2 |
| 7 | `api/services/obs/langfuse_push.py` | MODIFY | Replace `DATASET_NAME` with `schema.dataset_name` | 2 |
| 8 | `api/core/workflow_runner.py` | MODIFY | Add `runtime_config: dict` to `execute()`, merge into node configs | 4 |
| 9 | `api/nodes/feedback_cycle_node.py` | CREATE | `FeedbackCycleNode` wrapping `run_feedback_cycle()` | 4 |
| 10 | `api/nodes/dataset_load_node.py` | CREATE | `DatasetLoadNode` — load experiment, build eval dataset | 4 |
| 11 | `api/nodes/scan_node.py` | CREATE | `ScanNode` wrapping `sensitivity_scan()` | 4 |
| 12 | `workflows/optimization_campaign.yaml` | CREATE | Full optimization workflow: DatasetLoad → FeedbackCycle | 4 |
| 13 | `workflows/sensitivity_scan.yaml` | CREATE | Scan workflow: DatasetLoad → Scan | 4 |
| 14 | `notebooks/_campaign_lib.py` | MODIFY | Add `run_workflow()` wrapper | 5 |
| 15 | `docker/docker-compose.yaml` | MODIFY | Update for workflow packaging | 5 |
| 16 | `tests/test_workflow_migration.py` | CREATE | Tests for new nodes, runtime_config, YAML workflows | 4 |

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
Wave 0 (TermNorm cleanup)
  ↓
Wave 1 (GET /pipeline endpoint)
  ↓                              ↘
Wave 2 (PipelineSchema)       Wave 3 (unified tracing)
  ↓
Wave 4 (workflow nodes)
  ↓
Wave 5 (notebook migration)
```

Wave 0 → 1 are TermNorm-only, can start immediately.
Wave 2 depends on Wave 1 (needs the pipeline contract to parse).
Wave 3 is TermNorm-only, can run in parallel with Wave 2.
Waves 4-5 are existing M6 PromptPotter work.

---

## Work Packages

**Wave 0: TermNorm Cleanup** (TermNorm repo — see [task doc](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md))

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.0a | Simplify fuzzy matcher + confidence constants | 1 | — | Remove bidirectional fuzzy search, collapse to single threshold (0.7). Update `matchers.js`, `config.js`, `normalizer.js`. |

**Wave 1: Pipeline Contract** (TermNorm repo — see [task doc](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md))

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.0b | GET /pipeline endpoint + pipeline config JSON | 1 | 6.0a | Create `pipeline.json` (6-step config), `GET /pipeline` endpoint, frontend reads config at init. |

**Wave 2: Schema Foundation** (PromptPotter repo — prerequisite for all Wave 4 work)

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.0 | Write M6 spec | 1 | — | This document |
| 6.1 | PipelineSchema model + TermNorm factory | 1 | 6.0b | Create `pipeline_schema.py` (PipelineSchema, PipelineStep with `runtime`/`short_circuit`, ObservationMapping). Create `pipeline_discovery.py` (parse `GET /pipeline` → schema, static TermNorm default with all 6 steps). Tests for model, factory, derivation methods. |
| 6.2 | Replace hardcoded dicts with schema derivation | 1 | 6.1 | Replace chokepoints 1,2,3,6,8,9 with schema method calls. Thread `PipelineSchema` through `backend_client`, `eval_dataset`, `grid_core`, `langfuse_push`. Tests verifying no hardcoded pipeline step names remain. **Note:** chokepoint #2 (`_STEP_PARAM_KEYS` in `pipeline_nodes.py`) intentionally excludes `ranking_prompt` from the `llm_ranking` step's param keys — a straight `schema.step_param_keys()` replacement would break this filtering. The node must subtract `ranking_prompt` after calling the schema method. |

**Wave 3: Unified Tracing** (TermNorm repo — see [task doc](../../../OfficeAddinApps/TermNorm-excel/docs/pipeline-composability.md))

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.0c | Unified tracing (trace lifecycle + frontend integration) | 1 | 6.0b | `POST /pipeline/trace` + `POST /pipeline/steps` endpoints. Frontend creates trace, reports cache/fuzzy steps, passes `trace_id` to `/matches`. One Langfuse trace per query. |

**Wave 4: Workflow Nodes** (PromptPotter repo)

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.3 | runtime_config injection in WorkflowRunner | 1 | 6.2 | Modify `WorkflowRunner.execute()` to accept and merge `runtime_config` (includes `PipelineSchema`). Update node instantiation. Tests for config merge behavior. |
| 6.4 | DatasetLoadNode | 1 | 6.3 | Create `dataset_load_node.py`. Read experiment from ProjectStore, extract eval data + session terms. Unit tests. |
| 6.5 | FeedbackCycleNode | 1 | 6.3 | Create `feedback_cycle_node.py`. Wrap `run_feedback_cycle()`, extract callbacks from runtime_config. Unit tests with mocked feedback cycle. |
| 6.6 | ScanNode + YAML workflows | 1 | 6.4, 6.5 | Create `scan_node.py`. Write `optimization_campaign.yaml` and `sensitivity_scan.yaml`. Integration tests for YAML-driven execution. |

**Wave 5: Notebook Migration** (PromptPotter repo)

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.7 | Notebook migration + Docker Compose | 1 | 6.6 | Add `run_workflow()` to `_campaign_lib.py`. Update Docker Compose (from M4.3). Verify notebook drives optimization through WorkflowRunner. Update E2E test. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 6.0a | `src/matchers/matchers.js` (findFuzzyMatch, findBestMatch), `src/config/config.js` (FUZZY_THRESHOLDS), `src/services/normalizer.js` (findFuzzyMatch wrapper) |
| 6.0b | `backend-api/main.py` (router registration), `backend-api/api/research_pipeline.py` (existing endpoint patterns), `src/services/normalizer.js` (where to fetch config) |
| 6.1 | `api/services/backend_client.py` (PIPELINE_STEP_PARAMS, load_pipeline_config), `api/services/search/eval_dataset.py` (OBS_EXTRACTION_MAP), `api/models/workflow.py` (StepDefinition for reference) |
| 6.2 | `api/services/search/grid_core.py` (REQUIRED_TEMPLATE_VARS), `api/nodes/pipeline_nodes.py` (_STEP_PARAM_KEYS), `api/services/obs/langfuse_push.py` (DATASET_NAME) |
| 6.0c | `backend-api/utils/langfuse_logger.py` (log_pipeline, log_cache_match, log_fuzzy_match), `backend-api/api/research_pipeline.py` (/matches endpoint), `src/utils/api-fetch.js` (logMatch) |
| 6.3 | `api/core/workflow_runner.py` (execute method, _resolve_step_inputs), `api/nodes/base.py` (NodeBase.__init__ config handling) |
| 6.4 | `api/services/backend_client.py` (extract_session_terms, extract_replay_queries), `api/services/project_store.py` (load_experiment) |
| 6.5 | `api/services/campaign/feedback_cycle.py` (run_feedback_cycle signature, CycleConfig), `api/nodes/optimizer_nodes.py` (AnalysisEvalNode for pattern reference) |
| 6.6 | `workflows/optimizer_single_pass.yaml` (existing YAML format), `api/services/search/smart_search.py` (sensitivity_scan signature) |
| 6.7 | `notebooks/_campaign_lib.py` (current workflow), `tests/test_e2e_optimization.py` (E2E test pattern), `docker/docker-compose.yaml` |

---

## Entry Criteria

- M5 exit gate passed (observability integrated, LLM retry working)
- Existing workflow scaffold passes its tests
- All existing tests pass (`pytest -v --tb=short`)

## Exit Criteria

- **TermNorm (Waves 0-1, 3):**
  - Fuzzy matcher uses single threshold (0.7), single direction search
  - `GET /pipeline` returns complete 6-step pipeline config
  - Processing a query produces a single Langfuse trace with all pipeline steps as observations
- **PromptPotter (Waves 2, 4-5):**
  - `PipelineSchema` model exists with `runtime`/`short_circuit` fields and derivation methods replacing all Wave 2 chokepoints
  - No hardcoded pipeline step names in service layer (all derived from `PipelineSchema`)
  - `optimization_campaign.yaml` executes end-to-end via `WorkflowRunner` with `runtime_config`
  - `sensitivity_scan.yaml` executes scan workflow
  - `_campaign_lib.py` has `run_workflow()` function that drives optimization through `WorkflowRunner`
  - `runtime_config` correctly injects `backend_url`, `project_root`, `PipelineSchema`, callbacks
  - Docker Compose updated for workflow packaging
  - All new nodes have typed I/O models and unit tests
  - All existing tests still pass

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_pipeline_schema_model` | Unit | PipelineSchema, PipelineStep (with runtime/short_circuit), ObservationMapping construction and validation |
| `test_pipeline_schema_derivation` | Unit | `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `backend_steps()`, `frontend_steps()` produce correct output |
| `test_pipeline_discovery_factory` | Unit | Parse full 6-step `GET /pipeline` JSON into PipelineSchema; static TermNorm default matches |
| `test_schema_replaces_constants` | Integration | Services use schema methods instead of hardcoded constants; no import of old constants |
| `test_runtime_config_merge` | Unit | `runtime_config` values override YAML defaults; explicit YAML values preserved |
| `test_dataset_load_node` | Unit | Loads experiment from ProjectStore, extracts eval_data + session_terms |
| `test_feedback_cycle_node` | Unit | Wraps `run_feedback_cycle()` with correct CycleConfig construction |
| `test_feedback_cycle_node_callbacks` | Unit | Callbacks extracted from `runtime_config` and forwarded |
| `test_scan_node` | Unit | Wraps `sensitivity_scan()` with correct config |
| `test_optimization_yaml` | Integration | Load `optimization_campaign.yaml`, execute with mocked backend, verify end-to-end |
| `test_scan_yaml` | Integration | Load `sensitivity_scan.yaml`, execute with mocked backend |
| `test_campaign_lib_workflow` | Integration | `run_workflow()` in `_campaign_lib.py` drives optimization through WorkflowRunner |

## Verification (TermNorm)

| Check | Wave | How |
|-------|:----:|-----|
| Fuzzy matching works with single threshold | 0 | Run TermNorm — match terms in Excel, verify no behavioral regression |
| `GET /pipeline` returns full config | 1 | `curl http://localhost:8000/pipeline` — 6 steps, frontend + backend |
| Frontend reads threshold from config | 1 | Change `pipeline.json` threshold → verify fuzzy matching uses new value |
| Single trace per query | 3 | Process a query in Excel → check `logs/langfuse/traces/` — one trace with cache_lookup + fuzzy_matching + backend observations |
