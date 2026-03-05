# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.10.0
**Date:** 2026-03-05
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

See [`api/services/CLAUDE.md`](../../api/services/CLAUDE.md) for the full service catalog, evaluation gateway, and conventions.

---

## Data Model

**SearchPoint** bundles `PromptState` + `model` + `temperature` + `pipeline_params` — the four search-space dimensions for one evaluation. **PipelineSchema** (what pipeline) provides the structural context: `f(SearchPoint, PipelineSchema, eval_data) → scores`. See [`api/models/CLAUDE.md`](../../api/models/CLAUDE.md) for field details, derivation methods, and factory patterns.

**ProjectStore** file layout and store module breakdown: see [`api/services/CLAUDE.md`](../../api/services/CLAUDE.md) § "Store layout".

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
