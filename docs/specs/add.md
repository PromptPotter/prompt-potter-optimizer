# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.6.0
**Date:** 2026-02-23
**Status:** Active
**Depends on:** [Project Charter v0.6.0](project-charter.md), [PRD v0.6.0](prd.md)

---

## Table of Contents

- [System Context](#system-context)
- [What Exists Today](#what-exists-today)
- [Service Architecture](#service-architecture)
- [The Optimization Loop](#the-optimization-loop)
- [Data Model](#data-model)
- [Architectural Decisions](#architectural-decisions)
- [Target Architecture: Workflow Engine Migration](#target-architecture-workflow-engine-migration)
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
          |  grid_search.py                |
          |  prompt_optimizer.py           |
          |  prompt_eval.py               |
          |  backend_client.py            |
          |  project_store.py             |
          |  llm_client.py                |
          |  comparison.py                |
          |  langfuse_client.py           |
          +---------------+----------------+
                          |
          +---------------+----------------+
          |       External Systems         |
          |  - TermNorm backend API        |
          |  - LLM Providers (Groq,OpenAI) |
          |  - Langfuse (partial)          |
          |  - File system                 |
          +--------------------------------+
```

- **Developers** run optimization campaigns via notebooks or call the REST API for sync/execute/compare
- **The notebook** is the primary optimization interface. `_campaign_lib.py` wraps services with progress bars and display formatting; it never implements business logic.
- **The API** provides backend management, experiment sync, pipeline replay, and statistical comparison. Optimization itself is currently notebook-driven; API-driven orchestration is planned for M3 (P1.1).
- **LLM providers** handle inference through an OpenAI-compatible client (Groq with Llama 4 Maverick as default, OpenAI as alternative)
- **Langfuse** wrapper exists (singleton + trace creation); full per-trial integration is planned for M3

---

## What Exists Today

### Implemented Components

| Component | File(s) | Since | Description |
|-----------|---------|-------|-------------|
| **Grid search service** | `api/services/grid_search.py` | M2 | Default axis library, LLM context restructuring, distance-weighted stratified sampling, plan persistence with stable identity hash, per-point query sampling, content-addressed deduplication, crash recovery, LLM result analysis |
| **Prompt optimizer service** | `api/services/prompt_optimizer.py` | M2 | LLM meta-prompt candidate generation, round winner selection, improvement suggestions with phrase fragments, campaign winner save |
| **Prompt eval service** | `api/services/prompt_eval.py` | M2 | Backend evaluation via `/matches` with `ranking_prompt` override, content-addressed deduplication (`eval_content_hash()`), incremental `.partial.jsonl` writes, partial-run resume |
| **Backend client** | `api/services/backend_client.py` | M1 | HTTP client for TermNorm backend: sync experiments, replay queries, init sessions, extract eval data |
| **Project store** | `api/services/project_store.py` | M1 | File-based storage for backends, synced experiments, executions, dataset runs (evaluation records), grid plans. Incremental writes + crash recovery. |
| **LLM client** | `api/services/llm_client.py` | Pre-M1 | OpenAI-compatible abstraction. Providers: Groq (default), OpenAI. JSON mode support. Global singleton via `get_llm_client()`. |
| **Comparison service** | `api/services/comparison.py` | M1 | hit@k, MRR, McNemar's test, Wilcoxon signed-rank, per-query A/B classification |
| **Langfuse client** | `api/services/langfuse_client.py` | Pre-M1 | Singleton wrapper with `create_trace()`. Stubs for create_generation, create_score, flush. |
| **PromptState model** | `api/models/prompt_state.py` | M1 | Immutable, versioned, 3-layer prompt snapshot. `render()`, `derive()`, `diff()`. |
| **Campaign notebook** | `notebooks/optimization_campaign.ipynb` | M2 | Full HITL optimization: config editing, diagnostics, baseline eval, grid search, optimization loop, LLM suggestions, campaign summary |
| **Campaign library** | `notebooks/_campaign_lib.py` | M2 | Notebook-facing layer with tqdm progress and IPython display. Currently also contains orchestration logic (dedup-aware eval, grid plan lifecycle, replay reuse) pending promotion to `api/services/` in M3 (WP 3.0). |
| **Backends router** | `api/routers/backends.py` | M1 | `/api/v1/backends/*` — register, sync, execute, compare, list dataset runs |
| **Workflows router** | `api/routers/workflows.py` | Pre-M1 | `/api/v1/workflows/*` — execute workflow, evaluate, list nodes |
| **Health router** | `api/routers/health.py` | Pre-M1 | `/api/v1/health`, `/api/v1/ready` |
| **Workflow engine** | `api/core/workflow_runner.py` | Pre-M1 | DAG execution with topological sort and context passing. Used by workflows router. Target for optimizer migration (M3). |
| **Workflow nodes** | `api/nodes/` | Pre-M1 | LLMNode, RankerNode, PipelineConfigNode |
| **Evaluators** | `api/evaluators/` | Pre-M1 | ExactMatchEvaluator, CriteriaEvaluator (LLM-as-judge). Currently only exact match is used in the backend eval path. |
| **Test suite** | `tests/` | M1 | `test_prompt_state.py` (3 tests), `test_project_store_evals.py` (18+ tests). Evaluator and workflow tests were pruned in `ceb9031`. |
| **CI pipeline** | `.github/workflows/` | M1 | GitHub Actions: ruff lint + pytest on push/PR |
| **Pipeline parameter passthrough** | via `backend_client.run_match()` | M1 | Forward controllable knobs to backend `/matches` payload |

### Planned (Not Yet Built)

| Component | Milestone | PRD Req |
|-----------|-----------|---------|
| **Optimizer nodes** (InitNode, GrowFilterNode, AnalysisEvalNode wrapping existing services) | M3 | P1.1 |
| **Optimization workflow definition** (CWL-style, wires nodes into DAG) | M3 | P1.1 |
| **Campaign registry** (formal campaign/trial persistence, Langfuse/MLflow-compatible) | M3 | P1.3 |
| **Feedback cycling** (3-path routing via workflow engine) | M3 | P1.2 |
| **Full Langfuse integration** (per-trial tracing with scores) | M3 | P1.4 |
| **Streamlit dashboard** | M4 | P2.3 |

---

## Service Architecture

The optimizer is implemented as a **service-based architecture** where each service module has a single responsibility. The notebook (`_campaign_lib.py`) orchestrates the workflow by calling services in sequence. Services are stateless — all persistence goes through `ProjectStore`.

### Service Layer

| Service | File | Responsibility | Key Functions |
|---------|------|---------------|---------------|
| **grid_search** | `api/services/grid_search.py` | Grid search over Layer 1 prompt fields | `validate_grid_config()`, `build_grid_points()`, `run_grid_search()`, `resolve_point_evals()`, `restructure_context()`, `analyze_grid_results()`, `select_grid_winner()`, `grid_plan_identity()`, `serialize_grid_plan()`, `deserialize_grid_plan()`, `load_eval_dataset()` |
| **prompt_optimizer** | `api/services/prompt_optimizer.py` | LLM-driven candidate generation and campaign management | `generate_candidates()`, `select_round_winner()`, `generate_suggestions()`, `save_campaign_winner()` |
| **prompt_eval** | `api/services/prompt_eval.py` | Backend evaluation and deduplication | `backend_reranker_eval()`, `evaluate_prompt_batch()`, `compute_accuracy()`, `eval_content_hash()`, `extract_baseline_prompt()`, `make_incremental_writer()` |
| **backend_client** | `api/services/backend_client.py` | HTTP client for TermNorm backend | `sync_experiments()`, `fetch_experiment()`, `init_session()`, `run_match()`, `replay_queries()`, `extract_session_terms()`, `extract_replay_queries()` |
| **project_store** | `api/services/project_store.py` | File-based project persistence | Backend CRUD, sync save/load, execution save/load, dataset run storage, grid plan persistence, incremental eval writes |
| **llm_client** | `api/services/llm_client.py` | LLM abstraction | `OpenAIClient`, `GroqClient`, `get_llm_client()` |
| **comparison** | `api/services/comparison.py` | Statistical A/B comparison | `compute_comparison()`, `hit_at_k()`, `mcnemar_test()`, `wilcoxon_test()` |
| **langfuse_client** | `api/services/langfuse_client.py` | Langfuse observability wrapper | `get_instance()`, `create_trace()` (stubs: create_generation, create_score) |
---

## The Optimization Loop

The optimization loop is a **DAG-based iterative workflow** with an initialization phase followed by a main loop with conditional feedback paths. The design is derived from the reference n8n workflow (`docs/design/optimization-workflow.n8n.json`).

### 3-Layer Optimization Architecture

The three feedback paths form **nested optimization layers** with escalation.
The innermost layer runs first; outer layers activate only when inner ones stall:

| Layer | Fields | When to Change |
|-------|--------|---------------|
| **1 - Generate** (innermost) | persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples | Every optimization pass (grid search + iterative generation) |
| **2 - Refine Context** (middle) | context, parameters | When Layer 1 stalls — user adjusts via campaign config |
| **3 - Modify Plan** (outermost) | plan | Rarely — strategy defaults that control generation behavior |

Currently, Layer 1 is varied automatically (grid search + candidate generation). Layers 2 and 3 are adjusted manually via the campaign config JSON. Automated 3-layer escalation is planned for M3 (P1.2: Feedback Cycling).

### Mode 1: Grid Search (Landscape Exploration)

Systematic exploration of Layer 1 field variants before iterative refinement.

**Flow:**
1. **LLM Context Restructuring** — `restructure_context()` uses LLM to parse user-provided context into structured Layer 1 fields, optionally guided by `improvement_areas`
2. **Grid Plan Build** — `build_grid_points()` computes the cartesian product of axis variants and samples via distance-weighted stratification (`grid_budget`, `exploration_rate`, `SAMPLING_ALPHA = 3.0`)
3. **Plan Persistence** — `grid_plan_identity()` computes a stable hash over user-controlled inputs. Plans survive kernel restarts and resume automatically (skipping the non-deterministic LLM restructure call).
4. **Grid Execution** — `run_grid_search()` evaluates each point via backend `/matches` with rendered prompt. Per-point query sampling supported (`eval_queries_per_point`, `shared_queries`). Content-addressed deduplication prevents redundant backend calls.
5. **Result Analysis** — `display_grid_results()` shows ranked table, marginal stats per axis, pairwise interaction heatmaps. `analyze_grid_results()` uses LLM to identify strongest fields.
6. **Winner Selection** — `select_grid_winner()` picks the best point. Winner seeds the iterative campaign.

### Mode 2: Iterative Candidate Generation (Refinement)

LLM-driven candidate generation with patience-based stopping.

**Flow:**
1. **Baseline Evaluation** — extract baseline prompt from synced experiment, evaluate via backend, create round 0
2. **Generate Candidates** — `generate_candidates()` produces N variant PROMPT_STATEs via LLM meta-prompt analyzing failures
3. **Evaluate Candidates** — each candidate evaluated on subsampled eval data (`queries_per_eval`)
4. **Select Winner** — `select_round_winner()` picks best if improvement > threshold
5. **Continue or Stop** — semi-automatic: continue if improved, stop after `patience` rounds without improvement. Manual: user decides per round.
6. **LLM Suggestions** — `generate_suggestions()` produces failure patterns, parameter suggestions, phrase fragments, and suggested config JSON for the next round

### Evaluation Flow

Backend evaluation is the only path. `prompt_eval.backend_reranker_eval()`:

1. Calls `POST /matches` on the backend with `ranking_prompt` = rendered PROMPT_STATE
2. Backend runs its pipeline with the overridden prompt (pipeline topology discovered via `GET /pipeline`)
3. Top-ranked candidate is compared to ground truth via exact string match (hit@1)
4. Result deduplicated by content hash (`eval_content_hash()`)
5. Written incrementally to `.partial.jsonl` for crash recovery

### Deduplication and Crash Recovery

| Mechanism | Purpose |
|-----------|---------|
| **Content-addressed eval dedup** | `sha256(prompt + sorted_queries + model + temp)[:16]` — identical inputs skip backend calls |
| **Incremental writes** | `.partial.jsonl` appended after each query — crash recovery for long evaluations |
| **Partial-run resume** | On restart, load completed items from partial file, continue from where it stopped |
| **Grid plan persistence** | `grid_plan_identity()` hash + `serialize_grid_plan()` — plans survive kernel restarts |

---

## Data Model

### PROMPT_STATE

Immutable, versioned prompt configuration organized into three optimization layers (see [PRD P0.5](prd.md#p05-prompt_state-tracking)):

- **Layer 1 (Generate):** persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples
- **Layer 2 (Refine Context):** context, parameters (dict)
- **Layer 3 (Modify Plan):** plan
- **Metadata:** id (uuid.hex), parent_id, created_at, changes_description

`render()` assembles Layer 1 fields into prompt text. `derive(**changes)` creates a child with parent_id set. `diff(a, b)` produces structured comparison.

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
```

**Key design:** Dataset runs are indexed by `content_hash` (content-addressed). Looking up an existing run is a hash comparison, not a file scan. Grid plans use a separate `plan_id` hash over user-controlled inputs (axes, baseline, budget, exploration_rate, seed).

### API Models

- **BackendConnection** — id, name, backend_type, base_url, created_at, last_synced_at
- **Execution** — execution_id, backend_id, experiment_id, variant_label, results[], counts
- **ExecutionResultItem** — query, ground_truth, predicted, confidence, ranked_candidates, latency_ms, pipeline_data

---

## Architectural Decisions

| Decision | Why | Tradeoff |
|----------|-----|----------|
| **No framework dependency** (no DSPy, LangChain, TextGrad) | Avoid lock-in; borrow ideas from [literature review](../literature-review.md), build on own abstractions | More initial work, but strategies are swappable |
| **Service-based architecture with notebook orchestration** | Delivered working optimization faster than building formal DAG nodes. Services are independently testable and composable. The notebook provides natural HITL control. | No API-driven orchestration yet; notebook dependency for optimization. Migration to workflow engine planned for M3. |
| **Workflow engine as target architecture** | The existing `api/core/workflow_runner.py` provides DAG execution, node reuse, and context passing. Migrating service logic into nodes gives API-driven orchestration without reimplementation. | M3 investment; current services work well for notebook-driven use. |
| **Content-addressed deduplication** | Identical prompt+queries+model+temp produces identical results. Deduplicate by hash, not by run ID. Enables cross-session result reuse. | Hash collisions theoretically possible but SHA256 truncated to 16 hex chars (64 bits) is sufficient for this scale. |
| **Incremental writes for crash recovery** | Backend evaluation takes 10-30s/query. A 500-query evaluation must survive kernel crashes, rate limit errors, and network interruptions. | `.partial.jsonl` files need cleanup; finalization step merges partial into detail file. |
| **Grid plan persistence with stable identity** | Grid plans involve a non-deterministic LLM restructure call. Persisting the plan means kernel restarts resume the exact same grid (same points, same prompt states) without re-calling the LLM. | Plan files on disk accumulate; `grid_plans/` may need cleanup utilities. |
| **Backend evaluation only** | The token matching step requires the backend's loaded database — it cannot be replicated locally. Local evaluation was removed (`82157ef`) to simplify the codebase. | Every evaluation requires a running backend instance. No offline optimization. |
| **File-based project store** | No database for MVP; JSON files are human-readable and debuggable | Limited query capability; acceptable at single-user scale. Swappable later behind interface. |
| **PROMPT_STATE as first-class model** | Track all tunable params (not just prompt text); enable structured diffs and lineage | Open-ended parameters dict means no schema enforcement on values |
| **Notebook-first HITL** | Campaign config as editable JSON, manual round control, LLM suggestions with phrase fragments — the notebook is a natural fit for HITL optimization | Requires Jupyter environment; not usable from CLI/CI. Workflow engine migration (P1.1) enables API-driven orchestration. |

---

## Target Architecture: Workflow Engine Migration

The existing workflow engine (`api/core/workflow_runner.py`) is the target for the optimizer's next evolution (M3, PRD P1.1).

### Current State (M2)

```
Notebook → _campaign_lib.py → services (grid_search, prompt_optimizer, prompt_eval)
                                   ↓
                              backend_client → TermNorm API
```

Services implement optimization logic. The notebook orchestrates the flow (which service to call, when to stop, how to display results). This works well for HITL but cannot be driven by an API endpoint.

### Target State (M3)

```
API Endpoint (/api/v1/optimize)
        ↓
  Workflow Runner (api/core/workflow_runner.py)
        ↓
  Optimization Workflow Definition (CWL-style)
        ↓
  +--------+     +-----------+     +-------------+
  |InitNode| --> |GrowFilter | --> |AnalysisEval |
  |        |     |Node       |     |Node         |
  +--------+     +-----------+     +------+------+
                                          |
                                    next_action
                                          |
                              +-----------+-----------+
                              |           |           |
                          "generate"  "refine"   "modify"
                              |       context      plan
                              |           |           |
                              +-----------+-----------+
                                          |
                                    [loop back]
```

**Key principle:** Nodes are thin wrappers around existing service functions. No reimplementation.

- **InitNode** wraps `restructure_context()` → initial PROMPT_STATE
- **GrowFilterNode** wraps `generate_candidates()` → N variant PROMPT_STATEs
- **AnalysisEvalNode** wraps `evaluate_prompt_batch()` + `generate_suggestions()` → scores + `next_action`

The workflow runner provides DAG execution, node reuse, tracing, and error handling. The workflow definition makes optimization available to API clients and CI pipelines.

### Node Pattern

All nodes follow the pattern established by existing nodes (`LLMNode`, `RankerNode`):
- Typed inputs and outputs (Pydantic models)
- Single responsibility
- Composable (wire into any workflow definition)
- Testable (unit test with mock inputs)
- Stateless (state flows through the DAG)

---

## Deployment Model

Currently: **single-user localhost** only. No authentication, no multi-tenancy.

| Stage | Timeframe | Auth | Users | Hosting |
|-------|-----------|------|-------|---------|
| **Local** | M1–M4 | None | Single user | `localhost` |
| **Private server** | Post-M4 | API key | Team | Self-hosted |
| **Public service** | Post-M4 | Multi-tenant | Any developer | Cloud |

The API is designed stateless (no server-side session state) to enable future horizontal scaling. Auth middleware, rate limiting, and data isolation are post-M4 concerns.

---

## Integration Points

| System | Direction | Protocol | Status |
|--------|-----------|----------|--------|
| **LLM providers** (Groq, OpenAI) | Inference requests | OpenAI chat completions API | Implemented |
| **Langfuse** | Traces | Langfuse Python SDK | Partial (singleton + create_trace) |
| **TermNorm backend API** | Sync experiments, replay pipelines, backend evaluation via `/matches` with `ranking_prompt` override | HTTP REST | Implemented |
| **TermNorm discovery** | `GET /pipeline` for pipeline topology and tunable parameters | HTTP REST | Implemented |
| **ProjectStore** | Backend data, eval records, grid plans | JSON files in `.promptpotter/projects/` | Implemented |
| **File system** (campaigns) | Formal campaign/trial persistence | JSON/JSONL in `.promptpotter/campaigns/` | Planned (M3, P1.3) |

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
3. Run grid search to explore Layer 1 field combinations
4. Run iterative optimization on best grid winner: analyze failures, generate candidates (v2, v3, ...), evaluate, select best
5. Compare optimized Variant B against Variant A baseline
6. Produce recommendation: is the optimized LLM2 call worth it?

**Evaluation mode:** Each query runs the full pipeline via `/matches` with `ranking_prompt` override (~10-30s/query). A future `/rerank` endpoint on TermNorm (accepting pre-computed intermediates) would eliminate redundant steps 1-3.

**Benchmark:** BC5CDR 500-term subset (primary, publication-suitable). MedMentions 500-term subset (additional biomedical benchmark). LCA dataset validation follows for real-world deployment.

### Ablation Pattern

The Variant A vs B comparison is an instance of **pipeline ablation**: remove a component, replay, compare with statistical significance (McNemar's test for accuracy, Wilcoxon signed-rank for latency). Implemented via `comparison.py` and exposed at `POST /api/v1/backends/{id}/compare`.
