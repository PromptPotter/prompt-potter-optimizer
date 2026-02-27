# Product Requirements Document: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md)

---

## Requirements Summary

| ID | Name | Priority | Status |
|----|------|----------|--------|
| P0.1 | Backend Evaluation on Dataset | P0 | Implemented (M2) |
| P0.2 | Failure Analysis | P0 | Implemented (M2) |
| P0.3 | Candidate Generation | P0 | Implemented (M2) |
| P0.4 | Optimization Loop | P0 | Implemented (M2) |
| P0.5 | PROMPT_STATE Tracking | P0 | Implemented (M1) |
| P0.6 | Grid Search Exploration | P0 | Implemented (M2) |
| P1.1 | Optimizer Nodes | P1 | Implemented (M3) |
| P1.2 | Iterative Feedback Cycling | P1 | Implemented (M3) |
| P1.3 | Campaign Registry | P1 | Implemented (M3) |
| P1.4 | Langfuse Integration | P1 | Implemented (M3) |
| P1.5 | Discovery Protocol | P1 | Implemented (M2) |
| P1.6 | Ablation Comparison | P1 | Implemented (M1) |
| P1.7 | Pipeline Parameter Passthrough | P1 | Implemented (M1) |
| P1.8 | Sensitivity Scan | P1 | Implemented (M3) |
| P1.9 | Data Loop (Eval Reuse) | P1 | Implemented (M3) |
| P1.10 | File-Based Observability | P1 | Complete (M5) |
| P1.11 | LLM Retry Logic | P1 | Complete (M5) |
| P1.12 | Workflow-Driven Optimization | P1 | Planned (M6) |
| P1.13 | Multi-Connector Support | P1 | Planned (M7) |
| P2.1 | Evolutionary Operators | P2 | Planned |
| P2.2 | MCP Server Mode | P2 | Planned |
| P2.3 | Streamlit Dashboard | P2 | Planned |
| P2.4 | Non-Prompt Optimization Targets | P2 | Planned |
| P2.5 | Public Deployment Readiness | P2 | Planned |

---

## User Personas

- **Prompt Engineer Pat** -- solo developer optimizing LLM-powered features via notebooks. Daily workflow: explore -> optimize -> harvest -> reuse.
- **CI/CD Casey** -- pipeline operator calling the REST API from scripts. Needs structured JSON, idempotency, error handling.
- **Dataset Dana** -- benchmarking researcher running systematic comparisons with statistical rigor for publication.

---

## P0 -- Must Have (Core Optimizer)

All P0 requirements are implemented. Implementation references point to source files.

| ID | What | Implementation |
|----|------|---------------|
| P0.1 | Backend eval via `/matches` with content-addressed dedup and crash recovery | `prompt_eval.py` |
| P0.2 | Failure analysis with categorized patterns and suggestions | `prompt_optimizer.py` -- `generate_suggestions()` |
| P0.3 | LLM meta-prompt candidate generation with lineage | `prompt_optimizer.py` -- `generate_candidates()` |
| P0.4 | Optimization loop with patience-based stopping | `feedback_cycle.py`, `_campaign_lib.py` |
| P0.5 | Immutable 3-layer PromptState with `render()`, `derive()`, `diff()` | `api/models/prompt_state.py` |
| P0.6 | Grid search with sampling, plan persistence, LLM analysis | `search/grid_core.py`, `search/context.py`, `search/plan_persistence.py` |

---

## P1 -- Should Have (M3 Optimization Infrastructure)

All P1 requirements are implemented.

| ID | What | Implementation |
|----|------|---------------|
| P1.1 | InitNode, GrowFilterNode, AnalysisEvalNode wrapping service logic | `api/nodes/optimizer_nodes.py` |
| P1.2 | `run_feedback_cycle()` with 3-path routing, patience, progress callbacks | `feedback_cycle.py` |
| P1.3 | CampaignStore + campaign_registry for campaign/trial persistence | `campaign_registry.py`, `stores/campaign_store.py` |
| P1.4 | Per-trial Langfuse tracing with scores and campaign grouping | `langfuse_client.py`, integrated in `feedback_cycle.py` |
| P1.5 | Backend pipeline discovery via `GET /pipeline` | `backend_client.py` |
| P1.6 | Statistical comparison (McNemar, Wilcoxon, hit@k) | `comparison.py` |
| P1.7 | Pipeline parameter passthrough to backend | `backend_client.py` |
| P1.8 | OAT sensitivity scan with axis classification and diagnostic set | `search/smart_search.py` |
| P1.9 | Shared dataset_runs store + coverage advisor for eval reuse | `search/coverage.py` |

---

## P1 (continued) -- Should Have (M5–M7 Infrastructure)

Requirements added for milestones M5–M7. See individual milestone specs for full details.

| ID | What | Implementation | Milestone |
|----|------|---------------|-----------|
| P1.10 | File-based observability: Langfuse-compatible traces + MLflow-compatible experiments on disk. Prompt versioning. | [M5 spec](m5-observability.md) — `observability_logger.py` | M5 |
| P1.11 | LLM retry logic: exponential backoff for transient 503/429 errors in `llm_client.py` | [M5 spec](m5-observability.md) — WP 5.3 | M5 |
| P1.12 | Workflow-driven optimization: `WorkflowRunner` with `runtime_config`, `FeedbackCycleNode`, `DatasetLoadNode`, YAML campaigns | [M6 spec](m6-workflow-migration.md) | M6 |
| P1.13 | Multi-connector support: `ConnectorProtocol`, `MockConnector`, `ConnectorRegistry`, backend-agnostic evaluation | [M7 spec](m7-multi-connector.md) | M7 |

---

## P2 -- Nice to Have (Advanced Capabilities)

| ID | What |
|----|------|
| P2.1 | GA/DE population-based optimization (EvoPrompt-inspired) |
| P2.2 | MCP server mode for Claude Code and MCP clients |
| P2.3 | Streamlit dashboard: campaign browser, trial comparison, dataset explorer |
| P2.4 | Non-prompt optimization targets (schemas, scoring functions, fuzzy matchers) |
| P2.5 | Public deployment (auth, rate limiting, multi-tenancy) |

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Single evaluation (500-item dataset) | < 10 minutes |
| Full optimization run (5 iterations, 500 items) | < 60 minutes |
| Project store per campaign | < 10 MB |
| LLM providers | Groq and OpenAI (any OpenAI-compatible) |
| Python | 3.13 |
| Evaluation mode | Backend via `/matches` (no local fallback) |
| Crash recovery | Incremental `.partial.jsonl` with partial-run resume |

---

## Traceability Matrix

| Requirement | SC1: Improvement | SC2: Reproducibility | SC3: Langfuse | SC4: Time to First | SC5: TermNorm | SC6: Generalization | SC7: Observability | SC8: Workflow |
|-------------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| P0.1 Backend Evaluation | x | x | | x | x | | | |
| P0.2 Failure Analysis | x | | | | x | | | |
| P0.3 Candidate Generation | x | | | | x | | | |
| P0.4 Optimization Loop | x | x | | x | x | | | |
| P0.5 PROMPT_STATE | | x | | | | | | |
| P0.6 Grid Search | x | x | | x | x | | | |
| P1.1 Optimizer Nodes | | | | | | x | | |
| P1.2 Feedback Cycling | x | x | x | | | | | |
| P1.3 Campaign Registry | | x | x | | | | | |
| P1.4 Langfuse | | | x | | | | | |
| P1.5 Discovery Protocol | | | | | x | | | |
| P1.6 Ablation Comparison | x | x | | | x | | | |
| P1.7 Parameter Passthrough | | | | | x | | | |
| P1.8 Sensitivity Scan | x | x | | x | x | | | |
| P1.9 Data Loop | x | x | | x | | | | |
| P1.10 File-Based Observability | | x | x | | | | x | |
| P1.11 LLM Retry Logic | x | | | x | | | x | |
| P1.12 Workflow-Driven Optimization | | x | | x | | x | | x |
| P1.13 Multi-Connector | | | | | | x | | x |
| P2.4 Non-Prompt Targets | x | | | | | x | | |

**SC7: Observability** — File-based Langfuse traces, MLflow experiments, prompt versioning (M5).
**SC8: Workflow** — YAML-defined campaigns, runtime_config DI, multi-connector support (M6, M7).
