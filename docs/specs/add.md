# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md), [PRD v0.7.0](prd.md)

---

## Table of Contents

- [System Context](#system-context)
- [Two-Loop Architecture](#two-loop-architecture)
- [Service Architecture](#service-architecture)
- [The Optimization Pipeline](#the-optimization-pipeline)
- [Data Model](#data-model)
- [Architectural Decisions](#architectural-decisions)
- [Deployment Model](#deployment-model)
- [Integration Points](#integration-points)
- [Validation Scenario](#validation-scenario)

---

## System Context

```
+-----------------------------------------------------------+
|               Developer / CI Pipeline                     |
+-----------+---------------------------+-------------------+
            |                           |
            | Notebook (Python)         | HTTP (REST)
            v                           v
+-----------+-----------+  +------------+-------------------+
| _campaign_lib.py      |  |  FastAPI (api/main.py)         |
| (tqdm, IPython,       |  |  +----------+ +-----------+    |
|  progress display)    |  |  | backends | | workflows |    |
+-----------+-----------+  |  | router   | | router    |    |
            |              |  +----------+ +-----------+    |
            |              |  +----------+                  |
            |              |  | health   |                  |
            |              |  | router   |                  |
            |              +--+----------+------------------+
            |                           |
            +-------------+-------------+
                          |
          +---------------+----------------+
          |         Service Layer           |
          |  feedback_cycle.py             |
          |  search/ (smart_search,        |
          |    grid_core, coverage,         |
          |    context, synthesis)          |
          |  prompt_optimizer.py           |
          |  prompt_eval.py               |
          |  campaign_registry.py         |
          |  campaign_init.py             |
          |  backend_client.py            |
          |  project_store.py             |
          |  llm_client.py                |
          |  comparison.py                |
          |  langfuse_client.py           |
          +---------------+----------------+
                          |
          +------+--------+--------+-------+
          |      |        |        |       |
          v      v        v        v       v
      TermNorm  LLM    Langfuse  File   Evaluator
      Backend  Providers  SDK    System  Framework
      API      (Groq,           (.pp/)  (api/evaluators/)
               OpenAI)
```

- **Developers** run optimization campaigns via notebooks or call the REST API for sync/execute/compare
- **The notebook** is the primary optimization interface. `_campaign_lib.py` wraps services with progress bars and display formatting; it never implements business logic.
- **The API** provides backend management, experiment sync, pipeline replay, and statistical comparison. The feedback cycle orchestrator (`feedback_cycle.py`) runs optimization end-to-end, callable from both notebooks and API.
- **LLM providers** handle inference through an OpenAI-compatible client (Groq with Llama 4 Maverick as default, OpenAI as alternative)
- **Langfuse** provides per-trial tracing with accuracy scores, campaign-level trace grouping, and span logging for each optimization round
- **Evaluator framework** (`api/evaluators/`) provides pluggable scoring strategies. Currently `ExactMatchEvaluator` is used by `prompt_eval.py`; `CriteriaEvaluator` (LLM-as-judge) is available for future use.

---

## Two-Loop Architecture

PromptPotter operates through two nested feedback loops: an outer **Human Loop** for strategic decisions and an inner **AI Loop** for automated optimization.

### Human Loop (Explore - Optimize - Harvest - Reuse)

```
Generate data --> Explore (scan/grid) --> Optimize (feedback cycle)
      ^                                         |
      |         Human stops, reviews results     |
      |                                         v
      +<<<<< All eval data feeds back into <<<<<+
              coverage advisor & historical
              index for the next cycle
```

1. **Generate data** -- sync from backend, build eval dataset, run baseline eval
2. **Explore** -- sensitivity scan classifies axes by importance; grid search maps the accuracy landscape; coverage advisor shows what is already cached
3. **Optimize** -- feedback cycle (LLM candidate generation -> backend evaluation -> winner selection) runs iteratively until patience exhausted or human stops
4. **Harvest** -- human reviews results and stops the thread. All candidate evaluations are already persisted in `dataset_runs` via `evaluate_prompt_cached`
5. **Reuse** -- human re-runs sensitivity scan with the full data pool -> gets a new starting point informed by all prior eval data -> optimizes again

**Key principle:** Every backend evaluation -- whether from grid search, sensitivity scan, or feedback cycle -- writes to the same `dataset_runs` store with content-addressed deduplication. The coverage advisor (`search/coverage.py`) automatically discovers all stored results. No data is siloed per campaign or optimization thread.

### AI Loop (Generate - Evaluate - Select - Iterate)

The AI loop runs inside the "Optimize" step of the Human Loop. It is implemented by `feedback_cycle.py` orchestrating three optimizer nodes:

```
  InitNode  -->  GrowFilterNode  -->  AnalysisEvalNode
  (baseline)     (N candidates)       (eval + select)
                       ^                     |
                       |               next_action
                       |                     |
                       +---------------------+
                       (generate / refine_context / modify_plan / stop)
```

1. **InitNode** -- restructure raw context into Layer 1 fields, produce baseline PromptState
2. **GrowFilterNode** -- generate N candidate PromptStates via LLM meta-prompt informed by failure analysis
3. **AnalysisEvalNode** -- evaluate each candidate via `evaluate_prompt_cached()`, select winner, determine `next_action` routing

The three feedback paths form **nested optimization layers** with escalation:

| Layer | Fields | When to Change |
|-------|--------|---------------|
| **1 - Generate** (innermost) | persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples | Every optimization pass |
| **2 - Refine Context** (middle) | context, parameters | When Layer 1 stalls |
| **3 - Modify Plan** (outermost) | plan | Rarely -- strategy defaults |

Stopping conditions: `max_rounds` reached, `patience` consecutive non-improving rounds, `next_action == "stop"`, or perfect accuracy.

---

## Service Architecture

The optimizer is implemented as a **service-based architecture** where each service module has a single responsibility. The feedback cycle orchestrator (`feedback_cycle.py`) coordinates nodes and services. Services are stateless -- all persistence goes through `ProjectStore`.

### Service Layer

| Service | File | Responsibility |
|---------|------|---------------|
| **feedback_cycle** | `api/services/feedback_cycle.py` | Iterative optimization orchestrator: `CycleConfig` -> `GrowFilterNode` -> `AnalysisEvalNode` loop with patience-based stopping, 3-path routing, per-query/candidate progress callbacks, Langfuse trace/score logging |
| **search/smart_search** | `api/services/search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification, diagnostic set builder |
| **search/grid_core** | `api/services/search/grid_core.py` | Grid search evaluation engine. Skips `init_session` when all points are cached. |
| **search/coverage** | `api/services/search/coverage.py` | Historical index (`build_prompt_result_index`), coverage advisor (`assess_scan_coverage`). Discovers all stored `dataset_runs` for reuse across optimization threads. |
| **search/context** | `api/services/search/context.py` | LLM context restructuring -- decomposes raw instruction into Layer 1 fields |
| **search/eval_dataset** | `api/services/search/eval_dataset.py` | Evaluation dataset preparation and filtering |
| **search/plan_persistence** | `api/services/search/plan_persistence.py` | Grid plan and smart search plan serialization/deserialization |
| **search/synthesis** | `api/services/search/synthesis.py` | Result synthesis and analysis |
| **search/utils** | `api/services/search/utils.py` | Shared search utilities |
| **prompt_optimizer** | `api/services/prompt_optimizer.py` | LLM meta-prompt candidate generation, round winner selection, improvement suggestions with phrase fragments, campaign winner save |
| **prompt_eval** | `api/services/prompt_eval.py` | Backend evaluation via `/matches` with `ranking_prompt` override, content-addressed deduplication (`eval_content_hash()`), incremental `.partial.jsonl` writes, partial-run resume, `evaluate_prompt_cached()` as single gateway for eval persistence |
| **campaign_registry** | `api/services/campaign_registry.py` | Campaign/trial lifecycle: `create_campaign()`, `record_trial()`, `record_campaign_rounds()`, `complete_campaign()`, `get_campaign_lineage()` |
| **campaign_init** | `api/services/campaign_init.py` | Campaign initialization: `init_services()` sets up store, backend client, auto-syncs experiment data |
| **backend_client** | `api/services/backend_client.py` | HTTP client for TermNorm backend: sync experiments, replay queries, init sessions, extract eval data, extract session terms |
| **project_store** | `api/services/project_store.py` | Facade over focused store modules. Single import point for all file I/O. |
| **stores/** | `api/services/stores/` | `BackendStore`, `ExecutionStore`, `DatasetRunStore`, `GridPlanStore`, `SmartSearchStore`, `CampaignStore`. Shared I/O in `stores/base.py`. |
| **llm_client** | `api/services/llm_client.py` | `_OpenAICompatibleClient` base class. Providers: `GroqClient` (default), `OpenAIClient`. JSON mode. Global singleton via `get_llm_client()`. |
| **comparison** | `api/services/comparison.py` | Statistical A/B comparison: `hit_at_k()`, `mcnemar_test()`, `wilcoxon_test()`, `compute_comparison()` |
| **query_utils** | `api/services/query_utils.py` | Shared query-parsing utilities: `parse_bom_material()` |
| **langfuse_client** | `api/services/langfuse_client.py` | `LangfuseLogger` singleton. Full implementation: `create_trace()`, `create_span()`, `create_generation()`, `create_score()`, `update_trace()`, `flush()`, `shutdown()`. Graceful fallback when credentials missing. |

### Optimizer Nodes

| Node | File | Wraps |
|------|------|-------|
| **InitNode** | `api/nodes/optimizer_nodes.py` | `search.context.restructure_context()` -> initial PromptState |
| **GrowFilterNode** | `api/nodes/optimizer_nodes.py` | `prompt_optimizer.generate_candidates()` -> N variant PromptStates |
| **AnalysisEvalNode** | `api/nodes/optimizer_nodes.py` | `prompt_eval.evaluate_prompt_cached()` + `prompt_optimizer.select_round_winner()` + `prompt_optimizer.generate_suggestions()` -> scores + `next_action` routing |

All nodes follow the `NodeBase` pattern: typed Pydantic inputs/outputs, single responsibility, composable, testable with mock inputs.

### Existing Infrastructure Nodes (Pre-M1)

| Node | File | Purpose |
|------|------|---------|
| **LLMNode** | `api/nodes/llm_node.py` | Generic LLM call node |
| **RankerNode** | `api/nodes/ranker_node.py` | Ranking/scoring node |
| **PipelineConfigNode** | `api/nodes/pipeline_config_node.py` | Pipeline parameter configuration |

### Evaluator Framework

| Evaluator | File | Status |
|-----------|------|--------|
| **ExactMatchEvaluator** | `api/evaluators/exact_match.py` | Active -- used by `prompt_eval.py` for hit@1 scoring |
| **CriteriaEvaluator** | `api/evaluators/criteria.py` | Available -- LLM-as-judge scoring, not yet wired into the backend evaluation path |

### Workflow Engine

The workflow engine (`api/core/workflow_runner.py`) provides DAG execution with topological sort and context passing. It remains available as infrastructure for future use. The M3 feedback cycle orchestrator (`feedback_cycle.py`) coordinates optimizer nodes directly rather than through the DAG runner, as the optimization loop's iterative nature (variable number of rounds, conditional routing) fits a procedural orchestrator better than a static DAG.

---

## The Optimization Pipeline

### Mode 1: Sensitivity Scan (Axis Classification)

One-at-a-time (OAT) perturbation scanning to classify which prompt axes matter most.

**Flow:**
1. **Diagnostic Set** -- `build_diagnostic_set()` creates a stratified query set (~75% baseline hits for regression guard, ~25% misses for improvement signal)
2. **Coverage Check** -- `assess_scan_coverage()` checks historical data to skip already-evaluated variants
3. **OAT Scan** -- `sensitivity_scan()` perturbs one axis at a time, evaluating each variant on the diagnostic set
4. **Axis Classification** -- classify axes by sensitivity: high/medium/low impact on accuracy
5. **Result Persistence** -- scan results stored via `SmartSearchStore`, eval data via `evaluate_prompt_cached()` -> `DatasetRunStore`

### Mode 2: Grid Search (Landscape Exploration)

Systematic exploration of Layer 1 field variants before iterative refinement.

**Flow:**
1. **LLM Context Restructuring** -- `restructure_context()` uses LLM to parse user-provided context into structured Layer 1 fields
2. **Grid Plan Build** -- `build_grid_points()` computes the cartesian product of axis variants and samples via distance-weighted stratification (`grid_budget`, `exploration_rate`)
3. **Plan Persistence** -- `grid_plan_identity()` computes a stable hash over user-controlled inputs. Plans survive kernel restarts and resume automatically.
4. **Grid Execution** -- `grid_core.run_grid_search()` evaluates each point via backend `/matches` with rendered prompt. Per-point query sampling supported. Content-addressed deduplication prevents redundant backend calls.
5. **Result Analysis** -- ranked table, marginal stats per axis, pairwise interaction heatmaps, LLM-analyzed insights
6. **Winner Selection** -- best point seeds the iterative campaign

### Mode 3: Feedback Cycle (Iterative Refinement)

LLM-driven candidate generation with 3-path routing and patience-based stopping.

**Flow:**
1. **Initialize** -- InitNode restructures context into baseline PromptState (or use provided baseline from grid search winner)
2. **Generate Candidates** -- GrowFilterNode produces N variant PromptStates via LLM meta-prompt analyzing failures
3. **Evaluate and Select** -- AnalysisEvalNode evaluates each candidate via `evaluate_prompt_cached()`, selects round winner
4. **Route** -- `next_action` from analysis routes to: `generate` (Layer 1), `refine_context` (Layer 2), `modify_plan` (Layer 3), or `stop`
5. **Iterate or Stop** -- continue until patience exhausted, max_rounds reached, perfect score, or analysis signals stop
6. **Persist** -- campaign persisted via `CampaignStore`, all eval data in `DatasetRunStore`, Langfuse traces with per-round accuracy scores

### Evaluation Flow

All evaluation paths converge on `evaluate_prompt_cached()` -- the single gateway for eval persistence:

1. Renders PromptState into prompt text via `render()`
2. Computes content hash via `eval_content_hash()` (sha256 of prompt + sorted queries + model + temperature, truncated to 16 hex chars)
3. Checks `DatasetRunStore` for existing run with matching hash -- if found and not forced, returns cached results
4. For uncached runs: calls `backend_reranker_eval()` -> `POST /matches` with `ranking_prompt` override
5. Scores each result via `ExactMatchEvaluator` (hit@1: top-ranked candidate matches ground truth)
6. Writes incrementally to `.partial.jsonl` for crash recovery
7. On completion: finalizes to `dataset_runs/{run_id}.json` and updates `dataset_runs.json` index

### Deduplication and Crash Recovery

| Mechanism | Purpose |
|-----------|---------|
| **Content-addressed eval dedup** | `sha256(prompt + sorted_queries + model + temp)[:16]` -- identical inputs skip backend calls |
| **Incremental writes** | `.partial.jsonl` appended after each query -- crash recovery for long evaluations |
| **Partial-run resume** | On restart, load completed items from partial file, continue from where it stopped |
| **Grid plan persistence** | `grid_plan_identity()` hash + `serialize_grid_plan()` -- plans survive kernel restarts |
| **Smart search plan persistence** | `SmartSearchStore` saves scan plans with axis profiles and results |

---

## Data Model

### PROMPT_STATE

Immutable, versioned prompt configuration organized into three optimization layers (see [PRD P0.5](prd.md#p05-prompt_state-tracking)):

- **Layer 1 (Generate):** persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples
- **Layer 2 (Refine Context):** context, parameters (dict)
- **Layer 3 (Modify Plan):** plan
- **Metadata:** id (uuid.hex), parent_id, created_at, changes_description

`render()` assembles Layer 1 fields into prompt text. `derive(**changes)` creates a child with parent_id set. `diff(a, b)` produces structured comparison.

### CycleConfig

Configuration for feedback cycling (`feedback_cycle.py`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| max_rounds | int | 10 | Maximum optimization rounds |
| patience | int | 3 | Stop after N consecutive non-improvements |
| n_variants | int | 5 | Candidates per round |
| creativity | float | 0.7 | Temperature for candidate generation |
| improvement_threshold | float | 0.01 | Min accuracy delta to count as improvement |
| backend_url | str | required | Backend URL for evaluation |
| backend_id | str | "" | Backend identifier for caching |
| project_root | str | "" | Project root for store |
| pipeline_params | dict | None | Pipeline parameter overrides |
| session_terms | list[str] | None | Backend session terms |
| queries_per_eval | int | 0 | Subsample size (0 = use all) |

### ProjectStore File Layout

```
.promptpotter/projects/{backend_id}/
  backend.json                     # BackendConnection config
  sync/
    experiments.json               # Verbatim backend API response
    experiments/{exp_id}.json      # Individual experiment with traces
  executions/
    {execution_id}.json            # Pipeline replay results
  dataset_runs.json                # Index of eval runs (by content_hash)
  dataset_runs/
    {run_id}.json                  # Completed eval run detail
    {run_id}.partial.jsonl         # In-progress eval (crash recovery)
  grid_plans/
    {plan_id}.json                 # Persisted grid search plans
  smart_search_plans/
    {plan_id}.json                 # Sensitivity scan plans (axis profiles, results)
  campaigns/
    {campaign_id}.json             # Campaign metadata + trial index
    {campaign_id}/
      trial_NNNN.json             # Individual trial details
```

**Key design:** Dataset runs are indexed by `content_hash` (content-addressed). Looking up an existing run is a hash comparison, not a file scan. Grid plans and smart search plans use separate identity hashes over user-controlled inputs. Campaign data follows a two-level structure: metadata file + trial detail directory.

### API Models

- **BackendConnection** -- id, name, backend_type, base_url, created_at, last_synced_at
- **Execution** -- execution_id, backend_id, experiment_id, variant_label, results[], counts
- **ExecutionResultItem** -- query, ground_truth, predicted, confidence, ranked_candidates, latency_ms, pipeline_data
- **CycleResult** -- rounds[], n_rounds, best_accuracy, best_round, baseline_accuracy, winner_prompt_state, stop_reason, langfuse_trace_id
- **CycleRoundResult** -- round, label, accuracy, hits, total, improved, next_action, prompt_state, candidate_scores

---

## Architectural Decisions

| Decision | Why | Tradeoff |
|----------|-----|----------|
| **No framework dependency** (no DSPy, LangChain, TextGrad) | Avoid lock-in; borrow ideas from [literature review](../literature-review.md), build on own abstractions | More initial work, but strategies are swappable |
| **Procedural feedback cycle orchestrator** | The iterative optimization loop (variable rounds, conditional routing, patience-based stopping) fits a procedural orchestrator (`feedback_cycle.py`) better than a static DAG. The workflow runner remains available for future linear pipeline use. | Feedback cycle is not a DAG workflow; it is a loop with dynamic termination. |
| **Content-addressed deduplication** | Identical prompt+queries+model+temp produces identical results. Deduplicate by hash, not by run ID. Enables cross-session, cross-campaign result reuse. | Hash collisions theoretically possible but SHA256 truncated to 16 hex chars (64 bits) is sufficient for this scale. |
| **Shared dataset_runs store** | All evaluation paths (grid search, sensitivity scan, feedback cycle) write to the same `dataset_runs` store. The coverage advisor discovers all stored results regardless of origin. This is the "data loop" that makes every optimization run enrich the next. | No per-campaign isolation. Acceptable because content hashing makes results deterministic. |
| **Incremental writes for crash recovery** | Backend evaluation takes 10-30s/query. A 500-query evaluation must survive kernel crashes, rate limit errors, and network interruptions. | `.partial.jsonl` files need cleanup; finalization step merges partial into detail file. |
| **Grid plan persistence with stable identity** | Grid plans involve a non-deterministic LLM restructure call. Persisting the plan means kernel restarts resume the exact same grid without re-calling the LLM. | Plan files accumulate on disk. |
| **Backend evaluation only** | The token matching step requires the backend's loaded database -- it cannot be replicated locally. Local evaluation was removed (`82157ef`) to simplify the codebase. | Every evaluation requires a running backend instance. No offline optimization. |
| **File-based project store** | No database for MVP; JSON files are human-readable and debuggable | Limited query capability; acceptable at single-user scale. Swappable later behind interface. |
| **PROMPT_STATE as first-class model** | Track all tunable params (not just prompt text); enable structured diffs and lineage | Open-ended parameters dict means no schema enforcement on values |
| **Notebook-first HITL** | Campaign config as editable JSON, manual round control, LLM suggestions with phrase fragments -- the notebook is a natural fit for HITL optimization | Requires Jupyter environment; not usable from CLI/CI. The feedback cycle orchestrator is callable from any Python context. |
| **Optimizer nodes as thin wrappers** | InitNode, GrowFilterNode, AnalysisEvalNode wrap existing service functions. No reimplementation. Service logic is independently testable and reusable. | Node layer adds indirection. Acceptable because it provides typed contracts and composability. |
| **Langfuse per-trial tracing** | Each feedback cycle round gets a span with accuracy score under a campaign-level trace. Enables progress monitoring via Langfuse dashboard. | Depends on Langfuse SDK v2; graceful fallback when credentials missing. |

---

## Deployment Model

Currently: **single-user localhost** only. No authentication, no multi-tenancy.

| Stage | Timeframe | Auth | Users | Hosting |
|-------|-----------|------|-------|---------|
| **Local** | M1-M4 | None | Single user | `localhost` |
| **Private server** | Post-M4 | API key | Team | Self-hosted |
| **Public service** | Post-M4 | Multi-tenant | Any developer | Cloud |

Docker Compose (`docker/docker-compose.yml`) provides JupyterLab + FastAPI in a single deployment. The API is designed stateless (no server-side session state) to enable future horizontal scaling.

---

## Integration Points

| System | Direction | Protocol | Status |
|--------|-----------|----------|--------|
| **LLM providers** (Groq, OpenAI) | Inference requests | OpenAI chat completions API | Implemented |
| **Langfuse** | Traces, spans, scores | Langfuse Python SDK v2 | Implemented (per-trial tracing, campaign grouping, accuracy scores) |
| **TermNorm backend API** | Sync experiments, replay pipelines, backend evaluation via `/matches` with `ranking_prompt` override | HTTP REST | Implemented |
| **TermNorm discovery** | `GET /pipeline` for pipeline topology and tunable parameters | HTTP REST | Implemented |
| **ProjectStore** | Backend data, eval records, grid plans, smart search plans, campaigns | JSON files in `.promptpotter/projects/` | Implemented |
| **Evaluator framework** | Scoring strategies (ExactMatch, CriteriaEvaluator) | Python API | Implemented (ExactMatch active; CriteriaEvaluator available) |

---

## Validation Scenario

The pinnacle validation for PromptPotter is the **TermNorm pipeline variant comparison** (SC5).

### TermNorm Pipeline

| Stage | Component | Type | Prompt Family |
|-------|-----------|------|---------------|
| 1 | Web scrape | External | -- |
| 2 | Entity profiling (LLM1) | LLM call | `entity_profiling` |
| 3 | Table Reranker | Non-LLM | -- (token/string matching) |
| 4 | Semantic reranking (LLM2) | LLM call | `llm_ranking` |

### Variant Comparison

- **Variant A**: Web scrape --> LLM1 --> Table Reranker --> done (skip LLM2)
- **Variant B**: Web scrape --> LLM1 --> Table Reranker --> LLM2 (`llm_ranking`) --> done

**Research question:** Does LLM2 semantic reranking add enough accuracy over the table reranker to justify the extra LLM cost and latency?

### Optimization Strategy

1. Evaluate Variant A (once) to establish baseline score
2. Evaluate Variant B with `llm_ranking` v1 as initial candidate
3. Run sensitivity scan to identify high-impact axes
4. Run grid search to explore Layer 1 field combinations on high-impact axes
5. Run feedback cycle on best grid winner: generate candidates, evaluate, select, iterate
6. Compare optimized Variant B against Variant A baseline
7. Produce recommendation: is the optimized LLM2 call worth it?

**Evaluation mode:** Each query runs the full pipeline via `/matches` with `ranking_prompt` override (~10-30s/query). A future `/rerank` endpoint on TermNorm (accepting pre-computed intermediates) would eliminate redundant steps 1-3.

**Benchmark:** BC5CDR 500-term subset (primary, publication-suitable). MedMentions 500-term subset (additional biomedical benchmark). LCA dataset validation follows for real-world deployment.

### Ablation Pattern

The Variant A vs B comparison is an instance of **pipeline ablation**: remove a component, replay, compare with statistical significance (McNemar's test for accuracy, Wilcoxon signed-rank for latency). Implemented via `comparison.py` and exposed at `POST /api/v1/backends/{id}/compare`.
