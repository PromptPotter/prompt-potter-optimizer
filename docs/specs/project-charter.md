# Project Charter: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active

---

## Key Terms

| Term | Definition |
|------|-----------|
| **Campaign** | A single optimization run: baseline -> trials -> recommended best configuration. Persisted via `CampaignStore`. |
| **Trial** | One iteration within a campaign, testing a candidate against the eval dataset. |
| **PROMPT_STATE** | Immutable, versioned prompt snapshot in three layers: Layer 1 (Generate) structured fields, Layer 2 (Refine Context), Layer 3 (Modify Plan). |
| **Evaluation dataset** | Labeled input/expected-output pairs owned by the consuming project. |
| **Grid Search** | Cartesian product sweep of Layer 1 variants with sampling and dedup. |
| **Sensitivity Scan** | OAT perturbation scanning that classifies axes by accuracy impact. |
| **Feedback Cycle** | The AI loop: generate candidates -> evaluate -> select winner -> route next action. |
| **Data Loop** | Every evaluation writes to shared `dataset_runs`, making all prior data available to the coverage advisor. |
| **Coverage Advisor** | Discovers all stored `dataset_runs` to determine what's already cached. |

---

## Problem Statement

Tuning LLM-powered systems is manual and untracked. Developers adjust prompts and parameters through trial and error with no systematic record of what changed or whether it helped. Existing frameworks (DSPy, TextGrad, EvoPrompt) require adopting their abstractions wholesale.

### Motivating Use Case: TermNorm

TermNorm matches free-form text to standardized database identifiers (11,750+ entries) for Life Cycle Assessment workflows. Its pipeline: **LLM1** (entity profiling) -> **Table Reranker** (token matching) -> **LLM2** (semantic reranking). The central research question: does LLM2 justify the extra cost and latency over the table reranker alone?

Benchmark: BC5CDR 500-term subset (primary), MedMentions (secondary). Evaluation requires running the full pipeline via `/matches` (~10-30s/query) because the token matching step needs the backend's loaded database.

---

## Vision

An **API-first, framework-agnostic optimization service** that automates the analyze-generate-evaluate loop for any AI-powered system. Optimizes any tunable non-code configuration -- prompts, schemas, scoring functions, fuzzy matching parameters, retrieval queries, and other structured parameters.

The core mental model is **two nested feedback loops**: an outer Human Loop (explore -> optimize -> harvest -> reuse) for strategic decisions, and an inner AI Loop (generate -> evaluate -> select -> iterate) for automated optimization. The **data loop** connects them: every evaluation feeds back into the shared store for future use.

Near-term (M1-M4) focuses on core optimization and single-user workflows. Public deployment is post-M4.

---

## Scope

### In Scope

- Iterative prompt optimization via feedback cycle orchestrator
- Sensitivity scan with axis classification
- Grid search exploration with plan persistence
- Backend evaluation via `/matches` with content-addressed caching and crash recovery
- Campaign registry with Langfuse/MLflow-compatible data format
- Data loop: all eval data feeds back via shared `dataset_runs` store
- Notebook-first HITL + FastAPI REST delivery

### Out of Scope

- Fine-tuning or model training
- Public deployment infrastructure (M1-M4)
- Agent training or reinforcement learning
- Production prompt serving
- Dataset hosting

### Future Scope

Public service deployment, non-prompt optimization targets, evolutionary operators (GA/DE), MCP server mode, Streamlit dashboard.

---

## Success Criteria

| # | Criterion | Target |
|---|-----------|--------|
| 1 | **Measurable improvement** | 10%+ improvement on at least one metric within a single campaign |
| 2 | **Reproducibility** | Content-addressed caching ensures identical results for same inputs. < 5% variance across runs. |
| 3 | **Langfuse observability** | **Implemented** -- per-trial spans with scores, campaign traces, graceful fallback |
| 4 | **Time to first optimization** | < 15 minutes from `pip install` to completed campaign |
| 5 | **TermNorm validation** | Clear Variant A vs B recommendation with full campaign trace. **Planned for M4.** |
| 6 | **Generalization beyond prompts** | Successful non-prompt optimization. **Post-M4.** |

---

## Stakeholders

| Role | Responsibilities |
|------|-----------------|
| **Project owner / prompt engineer** | Defines system, selects datasets, reviews candidates, promotes configurations |
| **Pipeline operator** | Calls REST API, monitors campaigns, consumes structured results |
| **Consuming-project maintainer** | Maintains eval datasets, defines "good", validates in production |

---

## Constraints

| Constraint | Rationale |
|------------|-----------|
| Framework-agnostic | No LangChain/DSPy runtime dependency |
| OpenAI-compatible LLM providers | Supports Groq (default) and OpenAI; switchable without code changes |
| File-based registry first | No database for MVP; swappable later |
| Datasets are external | Domain-specific, versioned with consuming project |
| Docker-deployable | Single `docker-compose up` |
| Backend evaluation only | Token matching requires backend's loaded database |

---

## References

| Document | Description |
|----------|-------------|
| [Literature Review](../literature-review.md) | Survey of 11+ prompt optimization frameworks |
| [Registry Design](../registry-design.md) | Campaign/trial tracking pattern |
| [PRD](prd.md) | Requirements (P0/P1/P2) |
| [ADD](add.md) | Architecture and decisions |
| [WBS](wbs.md) | Work breakdown structure |
| [Roadmap](roadmap.md) | Milestone timeline and progress |
