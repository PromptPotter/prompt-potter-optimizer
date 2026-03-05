# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.9.0
**Date:** 2026-02-27
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md), [PRD v0.9.0](prd.md)

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
| (tqdm, IPython,       |  |  backends (+pipeline discovery)|
|  progress display)    |  |  workflows / health / campaigns|
+-----------+-----------+  +--------------------------------+
            |                           |
            +-------------+-------------+
                          |
          +---------------+----------------+
          |         Service Layer           |
          |  feedback_cycle, search/*,      |
          |  prompt_optimizer, prompt_eval, |
          |  campaign/campaign_init,        |
          |  backend_client, project_store, |
          |  llm_client, comparison,        |
          |  search/context, constants,     |
          |  obs/langfuse_client,           |
          |  obs/langfuse_push              |
          +---------------+----------------+
                          |
          +------+--------+--------+-------+-------+
          |      |        |        |       |       |
          v      v        v        v       v       v
      Connector LLM    Langfuse  File   Evaluator  obs/
      Protocol Providers  SDK    System  Framework  (Langfuse
      (M7)     (Groq,           (.pp/)  (api/       traces,
       |       OpenAI)                   evaluators/ MLflow
       v                                )           experiments,
   TermNorm                                         prompts)
   Backend                                          (M5)
   API
```

- **Notebooks** are the primary optimization interface. `_campaign_lib.py` wraps services with progress bars; no business logic.
- **The API** provides backend management, experiment sync, and pipeline discovery.
- **LLM providers** handle inference through an OpenAI-compatible client (Groq default, OpenAI alternative).
- **Langfuse** provides per-trial tracing with accuracy scores and campaign-level grouping.

---

## Two-Loop Architecture

See [CLAUDE.md](../../CLAUDE.md) for the canonical description of the Human Loop and AI Loop.

**Summary:** An outer Human Loop (explore -> optimize -> harvest -> reuse) wraps an inner AI Loop (generate -> evaluate -> select -> iterate). The **data loop** connects them: every evaluation writes to the shared `dataset_runs` store with content-addressed deduplication, making all prior work available to the coverage advisor.

The AI Loop is implemented by `feedback_cycle.py` orchestrating three optimizer nodes:

| Node | Wraps |
|------|-------|
| **InitNode** | `search.context.restructure_context()` -> initial PromptState |
| **GrowFilterNode** | `prompt_optimizer.generate_candidates()` -> N variant PromptStates |
| **AnalysisEvalNode** | `prompt_eval.evaluate_prompt_cached()` + `_select_round_winner()` + `generate_suggestions()` -> scores + generate/stop routing |

Each round generates Layer 1 variants. Stopping: `max_rounds`, `patience`, `next_action == "stop"` (from suggestion analysis), or perfect accuracy.

---

## Service Architecture

Stateless services; all persistence goes through `ProjectStore`. See [CLAUDE.md](../../CLAUDE.md) service table for the full listing.

Key services:

| Service | Responsibility |
|---------|---------------|
| `feedback_cycle.py` | Iterative optimization orchestrator with patience-based stopping, progress callbacks, Langfuse logging |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification |
| `search/grid_core.py` | Grid search evaluation engine with content-addressed caching |
| `search/coverage.py` | Historical index and coverage advisor -- discovers all stored `dataset_runs` for reuse |
| `prompt_eval.py` | Backend evaluation via `/matches`, content-addressed dedup, incremental `.partial.jsonl` crash recovery, `evaluate_prompt_cached()` as single eval gateway |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, winner selection, suggestions |
| `stores/campaign_store.py` | Campaign/trial lifecycle and persistence |
| `backend_client.py` | HTTP client for TermNorm backend |
| `pipeline_discovery.py` | Pipeline schema factory + `compute_pipeline_view()` (dynamic view with TTL cache) |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `llm_client.py` | `_OpenAICompatibleClient` base. Groq (default), OpenAI. Global singleton. |
| `langfuse_client.py` | Per-trial tracing, campaign grouping, graceful fallback when credentials missing |

---

## Data Model

### Core Data Models

**PromptState** defines the prompt being optimized. **PipelineSchema** defines the backend pipeline being targeted. Together they parameterize every optimization service: `f(PromptState, PipelineSchema, eval_data) → scores`.

### PromptState

Immutable, versioned prompt configuration in three optimization layers. See [PRD P0.5](prd.md).

- **Layer 1 (Generate):** persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples
- **Layer 2 (Refine Context):** context, parameters (dict)
- **Layer 3 (Modify Plan):** plan
- **Metadata:** id (uuid.hex), parent_id, created_at, changes_description

`render()` assembles Layer 1 into prompt text. `derive(**changes)` creates children. `diff(a, b)` produces structured comparison.

### PipelineSchema

Backend-agnostic pipeline description — single source of truth for what the backend pipeline looks like. See [PRD P1.14](prd.md), [M6 spec](m6-workflow-migration.md).

- **`PipelineStep`:** name, type (generation/span/event), runtime, short_circuit, param_keys, observation_name, `output_schema` (`StepOutputSchema`), `prompt_meta` (`StepPromptMeta`)
- **`StepOutputSchema`:** frozen model carrying resolved field names, descriptions, and JSON schema from the backend's schema registry
- **`StepPromptMeta`:** frozen model carrying resolved template variables, prompt template, and description from the backend's prompt registry
- **`ObservationMapping`:** obs_name → target_field extraction rules
- **Derivation methods:** `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `langfuse_type_map()`
- **Factory:** `pipeline_discovery.py` — `parse_pipeline_response()` parses `GET /pipeline` and merges `resolved_schemas`/`resolved_prompts` from the live response onto `PipelineStep` objects as `StepOutputSchema`/`StepPromptMeta`. Live always wins. Static `TERMNORM_DEFAULT_SCHEMA` carries structural metadata only (observation_mappings, langfuse_type, param_keys, runtime) — no hardcoded `output_schema` or `prompt_meta`.

**Registry metadata resolution:** LLMGeneration nodes in the pipeline config reference schemas and prompts by family + version (e.g. `schema_family: "entity_profile"`, `schema_version: 1`). The backend's `GET /pipeline` handler resolves these references from on-disk registries and returns them as `resolved_schemas` and `resolved_prompts` top-level dicts. `parse_pipeline_response()` matches these resolved artifacts to their owning steps and attaches them as first-class `StepOutputSchema`/`StepPromptMeta` objects. The scan advisor uses this enriched schema (output fields + prompt metadata) to make informed recommendations.

Provides derivation methods for 6 pipeline-specific constants (M6 Wave 2). Remaining 7 are covered by `ConnectorProtocol` (M7).

### ProjectStore File Layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{exp_id}.json
  executions/{execution_id}.json
  dataset_runs.json                # Index (content_hash -> run_id)
  dataset_runs/{run_id}.json       # Completed eval runs
  dataset_runs/{run_id}.partial.jsonl  # In-progress (crash recovery)
  grid_plans/{plan_id}.json
  smart_search_plans/{plan_id}.json
  campaigns/{campaign_id}.json     # Metadata + trial index
  campaigns/{campaign_id}/trial_NNNN.json
  obs/
    langfuse/events.jsonl          # flat navigation log (START HERE for data exploration)
    langfuse/traces/{trace_id}.json
    langfuse/scores/{trace_id}.jsonl
    experiments/{campaign_id}/     # MLflow FileStore format (mlflow ui compatible)
    prompts/{family}/{version}/    # prompt versioning (prompt.txt + metadata.json)
```

Dataset runs are indexed by content hash. Grid plans and smart search plans use separate identity hashes. Campaign data uses a two-level structure (metadata + trial details).

---

## Architectural Decisions

| Decision | Why |
|----------|-----|
| **No framework dependency** (no DSPy, LangChain, TextGrad) | Avoid lock-in; borrow ideas, build own abstractions |
| **Procedural feedback cycle** (not DAG) | Iterative loop with variable rounds and conditional routing doesn't fit a static DAG. Workflow runner remains available. |
| **Content-addressed deduplication** | Identical prompt+queries+model+temp = identical results. SHA256[:16] hash enables cross-session reuse. |
| **Shared dataset_runs store** | All eval paths (grid, scan, feedback) write to the same store. Coverage advisor discovers all results. This is the "data loop." |
| **Incremental writes** | Backend eval is 10-30s/query. `.partial.jsonl` survives crashes. |
| **Grid plan persistence** | LLM restructure is non-deterministic. Persisted plans resume exact same grid after kernel restart. |
| **Backend evaluation only** | Token matching requires backend's loaded database. No local evaluation fallback. |
| **File-based project store** | No database for MVP; JSON is human-readable. Swappable later. |
| **Optimizer nodes as thin wrappers** | Nodes wrap existing service functions. Service logic is independently testable. |
| **Notebook-first HITL** | Campaign config as editable JSON, manual round control, LLM suggestions -- natural fit for HITL. Feedback cycle is also callable from any Python context. |

> **Cross-reference:** [Observability guide](../obs-guide.md) covers M5 data exploration. Milestone specs [M6](m6-workflow-migration.md) (workflow migration) and [M7](m7-multi-connector.md) (multi-connector) extend this architecture. Each spec includes scope decisions, deliverables, and work packages.

---

## Integration Points

| System | Protocol | Status |
|--------|----------|--------|
| LLM providers (Groq, OpenAI) | OpenAI chat completions API | Implemented |
| Langfuse | Python SDK v2 | Implemented |
| TermNorm backend | HTTP REST (`/matches`, `/pipeline`) | Implemented |
| ProjectStore | JSON files in `.promptpotter/projects/` | Implemented |
| Evaluator framework | Python API (ExactMatchEvaluator) | Scaffold (no consumers) |
| File-based observability | Langfuse trace JSON + MLflow FileStore YAML in `obs/` | Implemented |
| PipelineSchema | Backend-agnostic pipeline description, derivation methods | Implemented ([M6](m6-workflow-migration.md) WP 6.1) |
| CWL workflow engine | `WorkflowRunner` with `runtime_config`, YAML workflow definitions | Planned ([M6](m6-workflow-migration.md)) |
| ConnectorProtocol | `typing.Protocol` abstraction over backend connectors | Planned ([M7](m7-multi-connector.md)) |

---

## Validation Scenario: TermNorm

The pinnacle validation is the **TermNorm pipeline variant comparison** (SC5):

- **Variant A**: Web scrape -> LLM1 -> Table Reranker -> done (skip LLM2)
- **Variant B**: Web scrape -> LLM1 -> Table Reranker -> LLM2 (`llm_ranking`) -> done

**Research question:** Does LLM2 semantic reranking justify the extra cost and latency?

**Benchmark:** BC5CDR 500-term subset (primary), MedMentions 500-term subset (secondary).

**Evaluation constraint:** Each query runs the full pipeline via `/matches` with `ranking_prompt` override (~10-30s/query). A future `/rerank` endpoint would eliminate redundant steps 1-3.
