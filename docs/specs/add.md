# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md), [PRD v0.7.0](prd.md)

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
| (tqdm, IPython,       |  |  backends / workflows / health |
|  progress display)    |  |  routers                       |
+-----------+-----------+  +--------------------------------+
            |                           |
            +-------------+-------------+
                          |
          +---------------+----------------+
          |         Service Layer           |
          |  feedback_cycle, search/*,      |
          |  prompt_optimizer, prompt_eval, |
          |  campaign_registry/init,        |
          |  backend_client, project_store, |
          |  llm_client, comparison,        |
          |  langfuse_client                |
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

- **Notebooks** are the primary optimization interface. `_campaign_lib.py` wraps services with progress bars; no business logic.
- **The API** provides backend management, experiment sync, pipeline replay, and statistical comparison.
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
| **AnalysisEvalNode** | `prompt_eval.evaluate_prompt_cached()` + `select_round_winner()` + `generate_suggestions()` -> scores + `next_action` routing |

Three feedback paths with escalation: **Layer 1 (Generate)** every pass, **Layer 2 (Refine Context)** when Layer 1 stalls, **Layer 3 (Modify Plan)** rarely. Stopping: `max_rounds`, `patience`, `next_action == "stop"`, or perfect accuracy.

---

## Service Architecture

Stateless services; all persistence goes through `ProjectStore`. See [CLAUDE.md](../../CLAUDE.md) service table for the full listing.

Key services:

| Service | Responsibility |
|---------|---------------|
| `feedback_cycle.py` | Iterative optimization orchestrator with patience-based stopping, 3-path routing, progress callbacks, Langfuse logging |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification |
| `search/grid_core.py` | Grid search evaluation engine with content-addressed caching |
| `search/coverage.py` | Historical index and coverage advisor -- discovers all stored `dataset_runs` for reuse |
| `prompt_eval.py` | Backend evaluation via `/matches`, content-addressed dedup, incremental `.partial.jsonl` crash recovery, `evaluate_prompt_cached()` as single eval gateway |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, winner selection, suggestions |
| `campaign_registry.py` | Campaign/trial lifecycle and persistence |
| `backend_client.py` | HTTP client for TermNorm backend |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `llm_client.py` | `_OpenAICompatibleClient` base. Groq (default), OpenAI. Global singleton. |
| `langfuse_client.py` | Per-trial tracing, campaign grouping, graceful fallback when credentials missing |

---

## Data Model

### PromptState

Immutable, versioned prompt configuration in three optimization layers. See [PRD P0.5](prd.md).

- **Layer 1 (Generate):** persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples
- **Layer 2 (Refine Context):** context, parameters (dict)
- **Layer 3 (Modify Plan):** plan
- **Metadata:** id (uuid.hex), parent_id, created_at, changes_description

`render()` assembles Layer 1 into prompt text. `derive(**changes)` creates children. `diff(a, b)` produces structured comparison.

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

---

## Integration Points

| System | Protocol | Status |
|--------|----------|--------|
| LLM providers (Groq, OpenAI) | OpenAI chat completions API | Implemented |
| Langfuse | Python SDK v2 | Implemented |
| TermNorm backend | HTTP REST (`/matches`, `/pipeline`) | Implemented |
| ProjectStore | JSON files in `.promptpotter/projects/` | Implemented |
| Evaluator framework | Python API (ExactMatch active, CriteriaEvaluator available) | Implemented |

---

## Validation Scenario: TermNorm

The pinnacle validation is the **TermNorm pipeline variant comparison** (SC5):

- **Variant A**: Web scrape -> LLM1 -> Table Reranker -> done (skip LLM2)
- **Variant B**: Web scrape -> LLM1 -> Table Reranker -> LLM2 (`llm_ranking`) -> done

**Research question:** Does LLM2 semantic reranking justify the extra cost and latency?

**Benchmark:** BC5CDR 500-term subset (primary), MedMentions 500-term subset (secondary).

**Evaluation constraint:** Each query runs the full pipeline via `/matches` with `ranking_prompt` override (~10-30s/query). A future `/rerank` endpoint would eliminate redundant steps 1-3.
