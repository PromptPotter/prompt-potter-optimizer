# Milestone 6: PipelineSchema + Cross-Repo Pipeline Composability

**Version:** 0.10.0
**Date:** 2026-03-05
**Status:** Waves 0-3, 5, 7 complete; Wave 6 mostly complete; Wave 4 deferred to M9
**Depends on:** [Roadmap M6](roadmap.md), [ADD v0.10.0](architecture-design.md), [PRD P1.12, P1.14](product-requirements.md)

---

## Context

**Waves 0-3 are complete.** PipelineSchema is implemented with derivation methods replacing all Wave 2 chokepoints. TermNorm exposes `GET /pipeline` with the full 6-step config, fuzzy matcher is simplified, and unified tracing is in place. Former Wave 5 (notebook migration + Docker) moved to M9.

**Wave 4 (workflow nodes) deferred to M9** — the YAML-driven workflow engine is not needed for the current optimization loop. Remaining M6 work focuses on scoring resolution: Wave 5 adds a hardcoded composite score (accuracy + token recall) with per-query rank display. Wave 6 generalizes this into auto-wired intermediate metrics derived from pipeline step roles.

**Exit gate (reframed):** MVP performance validation — prove TermNorm accuracy improvement from ~15% to >90% using the PipelineSchema-driven evaluation pipeline.

Two repos affected:
- **TermNorm** (`C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`) — Waves 0, 1, 3 (complete)
- **PromptPotter** (`C:\Users\dsacc\Desktop\PromptPotter\prompt-potter-optimizer\`) — Waves 2, 5, 6 (Wave 4 deferred to M9)

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

**Remaining (M9 — require ConnectorProtocol):**

| # | Hardcoded Thing | Fix |
|---|----------------|-----|
| 4 | `parse_bom_material()` | Query parser registry |
| 5 | GT mapping (bom→entry) | `schema.query_config` |
| 7 | Hit@1 exact match | `schema.eval_config` |
| 10 | ~~`skip_llm_ranking`~~ (removed) | Controlled via `steps` list |
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
| TermNorm default schema | ~~Static `TERMNORM_DEFAULT_SCHEMA`~~ (deleted — pipeline built entirely from live `GET /pipeline` response) | Self-describing pipeline; no offline fallback. |
| Full pipeline in schema | `PipelineStep` gets `runtime` and `short_circuit` fields | Frontend steps (cache, fuzzy) become first-class; PipelineSchema describes the complete pipeline |
| Unified tracing | One trace per query, all steps as observations | Enables fuzzy hit rate analysis, full pipeline latency breakdown, and end-to-end reproducibility |

---

## Deliverables

**Waves 0-3 (complete):** See [`docs/connectors/termnorm.md`](../connectors/termnorm.md) for the pipeline config contract. PipelineSchema model and factory: [`api/models/pipeline_schema.py`](../../api/models/pipeline_schema.py), [`api/services/pipeline_discovery.py`](../../api/services/pipeline_discovery.py).

**Wave 4: Workflow Nodes** — Deferred to M9. See Work Packages section below.

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
Wave 5 (composite scoring + rank display — hardcoded stepping stone)
  ↓
Wave 6 (auto-wired intermediate metrics)

Wave 4 (workflow nodes) → deferred to M9
```

---

## Work Packages

**Waves 0-3: Complete.** TermNorm cleanup (6.0a), GET /pipeline (6.0b), PipelineSchema model + factory (6.1), schema derivation replacement (6.2), n8n mapper spec (6.0d), unified tracing (6.0c).

**Wave 4: Workflow Nodes** — **Deferred to M9.** The YAML-driven workflow engine (`WorkflowRunner`, `runtime_config`, DatasetLoadNode, FeedbackCycleNode, ScanNode) is not needed for the current notebook-driven optimization loop. Work packages 6.3-6.6 move to M9 alongside notebook migration.

---

## Wave 5: Composite Scoring (Stepping Stone)

Binary accuracy (hit@1) is the only optimization signal today. `compute_accuracy()` counts exact top-1 matches and returns `{hits, total, accuracy, errors}`. This works but lacks resolution: two prompts with 60% accuracy may differ significantly in *how close* they came on misses. For ranker-type pipelines, "was the ground truth at least present in the candidate set?" is a cheap, informative secondary signal already partially surfaced by `_find_gt_rank()` in the notebook.

Wave 5 hardcodes a composite score for TermNorm as a stepping stone before Wave 6 generalizes it. It also enriches per-query output with candidate rank info so misses carry visual signal alongside the numeric metric.

### 5.1 Per-query rank display

Currently `_fmt_query_result()` shows HIT/MISS and the terminating step. Wave 5 adds candidate rank info for misses — compact `rank/total` format showing where ground truth landed among candidates:

```
    HIT   [fuzzy]  Polyethylen Rohr 50mm             -> PE Pipe 50mm         0.1s
    MISS   [llm]   Edelstahl Blech 2mm               -> Stainless Sheet 2mm  3/15   12.1s
    MISS   [llm]   Aluminiumlegierung 6061            -> Al Alloy 6082        --/15  11.3s
    HIT  [token]   Kupferrohr DN15                    -> Copper Tube DN15     0.4s
```

`3/15` = ground truth ranked 3rd of 15 candidates. `--/15` = ground truth not in the 15 candidates at all. This gives immediate visual signal: a miss at rank 2/20 is much closer than --/20. The data comes from `_find_gt_rank()` (already implemented) searching `ranked_candidates` then `token_matched_candidates`.

### 5.2 Token recall metric

For each query where `llm_ranking` is the terminating step:

1. Extract `token_matched_candidates` from `pipeline_data`
2. Check if `ground_truth` appears in the candidate list (string match)
3. `token_recall = n_gt_in_candidates / n_queries_reaching_llm`

This measures candidate-source quality independent of ranking quality. When token recall is low, improving the ranking prompt cannot help — the ground truth never reaches the ranker.

### 5.3 Composite score formula

```
composite = 0.9 * accuracy + 0.1 * token_recall
```

- `accuracy`: existing hit@1 from `compute_accuracy()`
- `token_recall`: fraction of LLM-routed queries where GT was in candidates

The 0.9/0.1 split ensures accuracy dominates while breaking ties with recall signal. This composite replaces raw accuracy as the optimization target in the feedback cycle and sensitivity scan winner selection.

### 5.4 Implementation scope

| What | Where | Change |
|------|-------|--------|
| `compute_composite_score()` | `api/services/prompt_eval.py` | New function: takes results list, returns `{accuracy, token_recall, composite}` |
| Winner selection | `api/services/l1_optimizer.py` | `_select_round_winner()` uses composite instead of raw accuracy |
| Sensitivity scan | `api/services/search/smart_search.py` | Axis ranking uses composite |
| Rank display | `notebooks/_campaign_lib.py` | `_fmt_query_result()` shows compact `rank/total` for misses (replaces verbose `(#3 of 15)` format) |
| Notebook display | `notebooks/_campaign_lib.py` | Show composite alongside accuracy |

### 5.5 Why not generalize yet

Token recall is hardcoded to `token_matched_candidates` — a TermNorm-specific `pipeline_data` key. The metric name and computation are baked in. This is acceptable as a stepping stone because:

1. It validates the composite concept with real optimization runs
2. Wave 6 replaces it with auto-wired metrics derived from `node_role`
3. The composite formula shape (`accuracy_weight * accuracy + sum(metric_weights)`) stays the same

### Work packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.7 | `compute_composite_score()` + integration | 1 | 6.2 | Implement composite in `prompt_eval.py`, wire into winner selection and scan ranking |

---

## Wave 6: Node-Role-Driven Intermediate Metrics

### Problem statement

Wave 5's hardcoded token recall only works for TermNorm's specific pipeline shape. A different backend might have no `token_matched_candidates` key, or its candidate-sourcing step might use a different name and data format. Hardcoding each backend's intermediate metrics creates the same kind of chokepoint that `PipelineSchema` was designed to eliminate.

The insight: every pipeline step has a **functional role** that determines what intermediate performance metrics it naturally contributes. A candidate-sourcing step always produces a set of candidates, so "was the ground truth in that set?" is always computable. A ranker always selects from upstream candidates, so "was the ground truth at least available to rank?" is always computable. These metrics follow from the role, not the implementation.

### Node role taxonomy

`PipelineStep` gains a `node_role` field (distinct from `type` which maps to Langfuse observation types like `"generation"` or `"tool"`):

| Role | Description | Pipeline steps (TermNorm) | Auto-metric | Data source |
|------|-------------|---------------------------|-------------|-------------|
| `candidate_source` | Produces candidate set from query | `token_matching`, `fuzzy_matching` | **source_recall**: GT in output candidates? | step's `pipeline_data` output (candidate list) |
| `ranker` | Ranks/selects from upstream candidates | `llm_ranking` | **candidate_recall**: GT in input candidates (from upstream source)? | upstream source output vs ground truth |
| `enricher` | Adds context, no candidates produced | `entity_profiling`, `web_search` | (none) | -- |
| `cache` | Short-circuits on cache hit | `cache_lookup` | **cache_hit_rate**: fraction with non-null timing | `step_timings` |

A step with role `candidate_source` always outputs a list of candidates under a known `pipeline_data` key (specified by its `observation_mappings`). A step with role `ranker` consumes that list and produces ranked output. The role tells you *what shape of data* the step works with, which determines *what metrics* are meaningful.

### IntermediateMetric model

```python
class IntermediateMetric(BaseModel):
    """Declarative metric derived from a pipeline step's node_role."""

    model_config = {"frozen": True}

    name: str                          # e.g. "source_recall", "candidate_recall"
    node_role: str                     # which role produces this metric
    pipeline_data_key: str             # key in pipeline_data to read candidates from
    description: str = ""
    default_weight: float = 0.0        # weight in composite score (0 = display-only)
```

Metrics are registered per `node_role` in a module-level registry (not per step instance). Each role maps to zero or more metrics.

### Auto-wiring flow

```
PipelineSchema
  └── steps: [PipelineStep(node_role="candidate_source", ...), ...]
        │
        ▼
derive_metrics(results: list[dict]) -> dict[str, float]
  │
  ├── for each step with a node_role:
  │     └── look up IntermediateMetric(s) for that role
  │         └── compute metric from step's pipeline_data across all results
  │
  └── return {"source_recall": 0.85, "candidate_recall": 0.72, ...}
```

`PipelineSchema.derive_metrics(results)` walks `self.steps`, looks up registered metrics by each step's `node_role`, computes each metric from per-query `pipeline_data`, and returns aggregated values. Steps with no `node_role` or with role `"enricher"` contribute no metrics.

### Composite score formula

```
composite = accuracy_weight * accuracy + sum(metric.weight * metric.value for metric in active_metrics)
```

Where:
- `accuracy_weight` defaults to `0.9`
- `active_metrics` are those derived from the current pipeline's active steps
- Weights are configurable per `PipelineSchema` (with defaults from `IntermediateMetric.default_weight`)
- Weights are normalized: `accuracy_weight + sum(metric_weights) = 1.0`

When no intermediate metrics are active (e.g., a simple single-step pipeline), composite falls back to raw accuracy.

### TermNorm concrete mapping

| Step | `node_role` | Auto-metric | `pipeline_data` key | Default weight |
|------|------------|-------------|---------------------|----------------|
| `cache_lookup` | `cache` | `cache_hit_rate` | (via `step_timings`) | 0.0 (display-only) |
| `fuzzy_matching` | `candidate_source` | `source_recall` | `fuzzy_matches` | 0.0 (display-only, short-circuits) |
| `web_search` | `enricher` | -- | -- | -- |
| `entity_profiling` | `enricher` | -- | -- | -- |
| `token_matching` | `candidate_source` | `source_recall` | `token_matched_candidates` | 0.05 |
| `llm_ranking` | `ranker` | `candidate_recall` | `ranked_candidates` (input = `token_matched_candidates`) | 0.05 |

Default composite for TermNorm: `0.9 * accuracy + 0.05 * source_recall + 0.05 * candidate_recall`

### Prerequisites

1. **`GET /pipeline` exposes `node_role`** — TermNorm adds `node_role` to each step's `optimizer` sub-object in `pipeline.json`, consumed via `parse_pipeline_response()`.
2. **Per-step I/O in `pipeline_data`** — Already available via `OBS_EXTRACTION_MAP` / `obs_extraction_map()`. Token matching outputs `token_matched_candidates`, LLM ranking outputs `ranked_candidates`.
3. **Wave 5 composite** — Proves the formula shape works before auto-wiring generalizes the metric sources.

### Relationship to M9 chokepoints

Wave 6 resolves chokepoint #7 ("Hit@1 exact match" → `schema.eval_config`). The `IntermediateMetric` registry + `PipelineSchema.derive_metrics()` make scoring backend-agnostic. A new connector only needs to declare its steps' `node_role` values; metrics and composite scoring follow automatically.

### Work packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 6.8 | IntermediateMetric model + PipelineStep.node_role | 1 | 6.7 | Add `node_role` field to `PipelineStep`. Create `IntermediateMetric` model + role-to-metric registry. Role assignments come from `GET /pipeline` response. |
| 6.9 | `derive_metrics()` + composite scoring | 1 | 6.8 | Implement `PipelineSchema.derive_metrics()`. Replace hardcoded composite from Wave 5 with auto-wired version. |
| 6.10 | Wire through eval/search/feedback paths | 1 | 6.9 | Update `evaluate_prompt_cached()`, sensitivity scan, and feedback cycle to use composite scores from `derive_metrics()`. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 6.8 | `api/models/pipeline_schema.py` (PipelineStep fields), `api/services/pipeline_discovery.py` (parse_pipeline_response) |
| 6.9 | `api/services/prompt_eval.py` (compute_accuracy, compute_composite_score from Wave 5), `api/services/search/smart_search.py` (axis ranking) |
| 6.10 | `api/services/l1_optimizer.py` (_select_round_winner), `api/services/campaign/feedback_cycle.py` (winner selection), `notebooks/_campaign_lib.py` (display) |

---

## Entry Criteria

- M5 exit gate passed (observability integrated, LLM retry working)
- Existing workflow scaffold passes its tests
- All existing tests pass (`pytest -v --tb=short`)

## Unified Query Result Output Format

All evaluation paths (baseline eval, sensitivity scan, adaptive search, feedback cycle) use `_fmt_query_result()` in `notebooks/_campaign_lib.py` as the single formatting function. Every query result line shows:

1. **HIT/MISS** — whether the prediction matched ground truth
2. **Pipeline termination step** — which step resolved the query (e.g. `[fuzzy]`, `[llm]`, `[token]`), via `_step_tag()`. Critical signal: shows whether a query was resolved by cache, fuzzy match, or went all the way to LLM ranking.
3. **Candidate rank info** (MISS only) — ground truth position among candidates, e.g. `(#4 of 20)` if ground truth was ranked 4th out of 20, or `(not in 15 candidates)` if absent. Uses `_find_gt_rank()` to search `ranked_candidates` then `token_matched_candidates`.
4. **Timing** — backend response time per query, or cached marker

```
        HIT   [fuzzy]  Polyethylen Rohr 50mm                     -> PE Pipe 50mm   0.1s
        MISS   [llm]   Edelstahl Blech 2mm                       -> Stainless Sheet 2mm  (#3 of 15)   12.1s
        MISS   [llm]   Aluminiumlegierung 6061                   -> Al Alloy 6082  (not in 15 candidates)   11.3s
        HIT  [token]   Kupferrohr DN15                           -> Copper Tube DN15   0.4s
        HIT   [fuzzy]  PVC Rohr 110mm                            -> PVC Pipe 110mm ⚡
```

**Real-time output:** Results print one-by-one as each backend call returns (not batched at variant completion). Achieved by threading `on_result` callback from the notebook layer through `sensitivity_scan()` → `_make_eval_fn()` → `evaluate_prompt_cached()`.

### Implementation files

| File | What |
|------|------|
| `notebooks/_campaign_lib.py` | `_fmt_query_result()`, `_step_tag()`, `_find_gt_rank()`, `_STEP_SHORT_TAGS` |
| `api/services/search/smart_search.py` | `on_result` param on `sensitivity_scan()` and `_make_eval_fn()` |
| `api/services/prompt_eval.py` | `on_result` callback on `evaluate_prompt_cached()` (fires per-query) |

---

## Exit Criteria

- **MVP performance validation:** TermNorm accuracy from ~15% to >90% using PipelineSchema-driven evaluation
- `compute_composite_score()` returns accuracy + token_recall + composite for any results list
- Per-query output shows compact `rank/total` for misses
- Composite score used as optimization target in feedback cycle and sensitivity scan
- `PipelineStep.node_role` assigns roles to all TermNorm steps
- `PipelineSchema.derive_metrics()` auto-computes intermediate metrics from active steps
- All existing tests still pass

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_pipeline_schema_model` | Unit | (Implemented) PipelineSchema, PipelineStep, ObservationMapping construction and validation |
| `test_pipeline_schema_derivation` | Unit | (Implemented) `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `backend_steps()`, `frontend_steps()` |
| `test_pipeline_discovery_factory` | Unit | (Implemented) Parse full 6-step `GET /pipeline` JSON into PipelineSchema |
| `test_schema_replaces_constants` | Integration | (Implemented) Services use schema methods instead of hardcoded constants |
| `test_composite_score` | Unit | `compute_composite_score()` correctly computes accuracy, token_recall, composite |
| `test_rank_display` | Unit | `_fmt_query_result()` shows `rank/total` for misses, nothing for hits |
| `test_node_role_assignment` | Unit | All TermNorm steps have correct `node_role` in default schema |
| `test_derive_metrics` | Unit | `PipelineSchema.derive_metrics()` computes source_recall, candidate_recall from results |
| `test_composite_winner_selection` | Integration | Winner selection uses composite instead of raw accuracy |
