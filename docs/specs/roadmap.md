# Roadmap: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [WBS v0.7.0](wbs.md)

---

## Milestones

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Specifications | Complete |
| M1 | Foundation (PromptState, ProjectStore, comparison, CI) | Complete |
| M2 | Core Optimizer (eval, grid search, prompt optimizer, notebook) | Complete |
| M3 | Optimization Infrastructure | Complete |
| M4 | Integration and Polish | Planned |
| M5 | Observability Layer | Nearly Complete |
| M6 | CWL Workflow Migration | Future |
| M7 | Multi-Connector Architecture | Future |

---

## M3: Optimization Infrastructure -- Complete

**Complete:**
- Optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode)
- Feedback cycle with 3-path routing, patience-based stopping, progress callbacks
- Campaign registry + CampaignStore
- Langfuse per-trial tracing with scores
- Sensitivity scan + coverage advisor
- Grid search refactor (search module)
- Campaign init service
- Notebook integration with progress display
- Data loop (all eval paths -> shared dataset_runs)
- Service layer cleanup (campaign_lib dedup, ~1,300 LOC removed across 4 passes)

**Exit gate:** End-to-end optimization with progress output, eval data feeds back into scans, Langfuse traces with scores.

---

## M4: Integration and Polish -- Planned

- **TermNorm Variant Comparison** (SC5): Variant A vs B on BC5CDR 500-term subset using the full two-loop workflow
- **Streamlit Dashboard**: campaign browser, trial comparison, dataset explorer
- **Docker Compose**: optimizer + Langfuse with health checks
- **Documentation**: README, notebooks, cleanup

**Entry criteria:** M3 exit gate passed.

**Exit gate:** Variant A vs B comparison completes with clear recommendation. Docker deployment works. Decision: does optimized LLM2 justify its cost?

---

## M5: Observability Layer -- Nearly Complete

Adopt TermNorm-excel's zero-dependency file-based patterns for production-grade logging. Three layers of human-accessible data: `events.jsonl` (flat nav log), Langfuse traces (structured detail), MLflow experiments (metrics viewer).

- **`events.jsonl`** — flat human-readable event log as starting point for data exploration. Custom extension (not Langfuse/MLflow spec), each line has `trace_id`/`campaign_id` links to detailed files. Adapted from TermNorm `langfuse_logger._log_event()`.
- **Langfuse file format** — Langfuse-compatible traces/observations/scores on disk. Reference: `TermNorm-excel/backend-api/utils/langfuse_logger.py`
- **MLflow experiment format** — feedback cycle campaigns as experiments, rounds as runs. PromptPotter has zero MLflow dependency; viewer runs in a separate throwaway venv (`mlflow ui --backend-store-uri file:...`). Reference: `TermNorm-excel/backend-api/utils/standards_logger.py` and `FILE_ORGANIZATION_STRATEGY.md`.
- **Prompt registry** — version PromptState Layer 1 fields with metadata. Write-only audit trail. Reference: `TermNorm-excel/backend-api/utils/prompt_registry.py`
- **LLM retry logic** — exponential backoff for Groq 503s in `llm_client.py`

**Entry criteria:** M4 exit gate passed (or waived).

**Exit gate:** Eval runs produce Langfuse-compatible trace files. Campaign runs produce MLflow-compatible experiment files. `events.jsonl` written for every significant action. `mlflow ui` can visualize optimization history. Full spec: [`docs/specs/m5-observability.md`](m5-observability.md).

---

## M6: CWL Workflow Migration -- Future

Wire existing service functions into the workflow engine scaffold (`api/core/`, `api/nodes/`).

- **Wrap services as nodes** — `prompt_eval.evaluate_prompt_cached` → EvalNode, `search/smart_search.sensitivity_scan` → ScanNode, `search/grid_core.run_grid_search` → GridSearchNode, `feedback_cycle.run_feedback_cycle` → FeedbackCycleNode
- **Pipeline parameter discovery** — auto-detect tunable parameters from workflow definition (Layer 1/2/3 fields, grid axes, scan axes)
- **Workflow-driven optimization** — replace direct service calls in `_campaign_lib.py` with workflow execution via `WorkflowRunner`
- **YAML-defined campaigns** — optimization campaigns as workflow YAML, not Python code

**Entry criteria:** M5 exit gate passed (observability integrated).

**Exit gate:** `optimization_campaign.ipynb` runs entirely through `WorkflowRunner`. Campaign YAML defines the full optimization pipeline.

---

## M7: Multi-Connector Architecture -- Future

Generalize beyond TermNorm to support arbitrary LLM application backends.

- **Connector interface** — abstract `BackendClient` into a connector protocol. Reference: `docs/connectors/termnorm.md` (already started)
- **Connector registry** — discover and configure connectors at runtime
- **Backend-agnostic evaluation** — `evaluate_prompt_cached()` works with any connector, not just TermNorm `/matches`

**Entry criteria:** M6 exit gate passed (workflow engine active).

**Exit gate:** A second backend connector exists and runs through the same optimization workflow.

---

## Backlog (unscheduled)

| Feature | Notes |
|---------|-------|
| Web scrape ablation | How many websites to scrape? Quality vs cost/latency tradeoff. |
| Public service deployment | Auth, rate limiting, multi-tenancy. API already stateless. |
| Non-prompt targets (P2.4, SC6) | Scoring functions, fuzzy matchers, retrieval queries, GA settings. |
| Evolutionary operators (P2.1) | GA/DE population-based search |
| MCP server mode (P2.2) | Expose tools to Claude Code |

Prioritization decided at milestone exit gates.

---

## Progression Rules

- Complete current milestone before starting the next
- Each milestone ends with a decision gate
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
