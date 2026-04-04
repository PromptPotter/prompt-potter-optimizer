# Product Requirements Document: PromptPotter Optimizer

**Version:** 0.9.0
**Date:** 2026-02-27
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
| P0.6 | Sensitivity Scan Exploration | P0 | Implemented (M2) |
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
| P1.13 | Multi-Connector Support | P1 | Planned (M9) |
| P1.14 | PipelineSchema | P1 | Complete (M6 Wave 2) |
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

P0.1–P0.6: All implemented (M1–M2). Backend evaluation with content-addressed dedup and crash recovery, failure analysis, LLM candidate generation, optimization loop with patience-based stopping, immutable 3-layer PromptState, sensitivity scan with sampling and plan persistence. See `promptpotter/services/` for implementations.

---

## P1 -- Should Have (M3 Optimization Infrastructure)

P1.1–P1.9: All implemented (M1–M3). Optimizer pipeline, feedback cycling with 3-path routing, campaign registry, Langfuse per-trial tracing, discovery protocol, ablation comparison, parameter passthrough, sensitivity scan, shared data loop. See `promptpotter/services/` for implementations.

---

## P1 (continued) -- Should Have (M5–M9 Infrastructure)

| ID | What | Implementation | Milestone |
|----|------|---------------|-----------|
| P1.10 | File-based observability: Langfuse-compatible traces + MLflow-compatible experiments on disk. Prompt versioning. | See [`promptpotter/services/CLAUDE.md`](../../promptpotter/services/CLAUDE.md) | M5 (Complete) |
| P1.11 | LLM retry logic: exponential backoff for transient 503/429 errors | See [`promptpotter/services/CLAUDE.md`](../../promptpotter/services/CLAUDE.md) | M5 (Complete) |
| P1.12 | Workflow-driven optimization: `WorkflowRunner` with `runtime_config`, `FeedbackCycleNode`, `DatasetLoadNode`, YAML campaigns | [M6 spec](m6-pipeline-composability.md) | M6 |
| P1.13 | Multi-connector support: `ConnectorProtocol`, `MockConnector`, `ConnectorRegistry`, backend-agnostic evaluation | [M9 spec](m9-multi-connector.md) | M9 |
| P1.14 | PipelineSchema: backend-agnostic pipeline description as single source of truth. Eliminates 13 backend-specific assumptions. Derived from backend discovery, consumed by all services. | [M6 spec](m6-pipeline-composability.md) — WP 6.1–6.2 | M6 |

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
| P0.6 Sensitivity Scan | x | x | | x | x | | | |
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
| P1.14 PipelineSchema | | | | | | x | | x |
| P2.4 Non-Prompt Targets | x | | | | | x | | |

**SC7: Observability** — File-based Langfuse traces, MLflow experiments, prompt versioning (M5).
**SC8: Workflow** — YAML-defined campaigns, runtime_config DI, PipelineSchema, multi-connector support (M6, M9).
