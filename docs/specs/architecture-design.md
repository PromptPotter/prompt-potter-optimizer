# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.10.0
**Date:** 2026-03-05
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md), [PRD v0.9.0](product-requirements.md)

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
      (M8)     (Groq,           (.pp/)  (api/       traces,
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

The AI Loop is itself a 4-step pipeline, implemented by `feedback_cycle.py`:

| Step | Purpose | Wraps |
|------|---------|-------|
| **l1_generate** | Candidate generation (also init mode) | `l1_generate()` / `restructure_context()` in `prompt_optimizer.py` |
| **l1_evaluate** | Eval + winner selection + critique + styles | `l1_evaluate()` + `CritiqueAgent.run()` + `sample_thinking_styles()` |
| **l2_refine_context** | Context/parameter tuning on L1 stall | `refine_context()` in `layer_transitions.py` |
| **l3_modify_plan** | Strategic replanning on L2 stall | `modify_plan()` in `layer_transitions.py` |

These 4 steps are modeled using the same `PipelineSchema`/`PipelineStep` architecture as the target backend, enabling step-level tracing, full reproducibility, and self-optimization. Each step is a direct service call wrapped with `observed_step()` (in `api/services/obs/step_tracer.py`), configured via `api/config/optimizer_pipeline.json` using the node standard. `OptSearchPoint` (`api/models/opt_search_point.py`) captures the optimizer's configuration at each round — critique, thinking styles, task context, escalation journal, and per-query warning inventory — checkpointed in trial JSON for traceability and resume. See the [M7 spec](m7-optimizer-pipeline.md) for the full design including the warning inventory (§13) and L2 diagnostic probe rounds.

Each round runs l1_generate then l1_evaluate. Stopping: `max_rounds`, `patience`, `next_action == "stop"`, or perfect accuracy. On L1 stall, escalates to l2_refine_context; on L2 stall, to l3_modify_plan. Pluggable `EscalationCheck`s can also trigger L2/L3/abort mid-round (bypassing patience).

---

## Service Architecture

See [`api/services/CLAUDE.md`](../../api/services/CLAUDE.md) for the full service catalog, evaluation gateway, and conventions.

---

## Data Model

**SearchPoint** bundles `PromptState` + `model` + `temperature` + `pipeline_params` — the four search-space dimensions for one evaluation. **PipelineSchema** (what pipeline) provides the structural context: `f(SearchPoint, PipelineSchema, eval_data) → scores`. See `api/models/search_point.py` and `api/models/pipeline_schema.py` for field details, derivation methods, and factory patterns.

**ProjectStore** file layout and store module breakdown: see [`api/services/CLAUDE.md`](../../api/services/CLAUDE.md) § "Store layout".

---

## Architectural Decisions

| Decision | Why |
|----------|-----|
| **No framework dependency** (no DSPy, LangChain, TextGrad) | Avoid lock-in; borrow ideas, build own abstractions |
| **Procedural feedback cycle** (not DAG) | Iterative loop with variable rounds and conditional routing doesn't fit a static DAG. Workflow runner remains available. |
| **Content-addressed deduplication** | Identical prompt+queries+model+temp = identical results. SHA256[:16] hash enables cross-session reuse. |
| **Shared dataset_runs store** | All eval paths (scan, feedback) write to the same store. Coverage advisor discovers all results. This is the "data loop." |
| **Incremental writes** | Backend eval is 10-30s/query. `.partial.jsonl` survives crashes. |
| **Scan plan persistence** | LLM restructure is non-deterministic. Persisted plans resume exact same scan after kernel restart. |
| **Backend evaluation only** | Token matching requires backend's loaded database. No local evaluation fallback. |
| **File-based project store** | No database for MVP; JSON is human-readable. Swappable later. |
| **Optimizer nodes as thin wrappers** | Nodes wrap existing service functions. Service logic is independently testable. |
| **Notebook-first HITL** | Campaign config as editable JSON, manual round control, LLM suggestions -- natural fit for HITL. Feedback cycle is also callable from any Python context. |

> **Cross-reference:** [Observability](../observability.md) covers M5 data exploration. Milestone specs [M6](m6-pipeline-composability.md) (pipeline composability) and [M8](m8-multi-connector.md) (multi-connector) extend this architecture. Each spec includes scope decisions, deliverables, and work packages.

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
| PipelineSchema | Backend-agnostic pipeline description, derivation methods | Implemented ([M6](m6-pipeline-composability.md) WP 6.1) |
| CWL workflow engine | `WorkflowRunner` with `runtime_config`, YAML workflow definitions | Planned ([M6](m6-pipeline-composability.md)) |
| ConnectorProtocol | `typing.Protocol` abstraction over backend connectors | Planned ([M8](m8-multi-connector.md)) |
