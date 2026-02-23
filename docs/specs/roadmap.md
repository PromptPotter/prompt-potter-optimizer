# Roadmap: PromptPotter Optimizer

**Version:** 0.6.0
**Date:** 2026-02-23
**Status:** Active
**Depends on:** [WBS v0.6.0](wbs.md)

---

## Milestones at a Glance

| Milestone | Focus | Timeline | Status |
|-----------|-------|----------|--------|
| M0 | Specifications | Week 1 | Complete |
| M1 | Foundation | Weeks 2-3 | Complete |
| M2 | Core Optimizer (Services) | Weeks 4-6 | Largely Complete |
| M3 | Workflow Engine Migration | Weeks 7-9 | Planned |
| M4 | Integration and Polish | Weeks 10-12 | Planned |

---

## M0: Specifications — Complete

- **Deliverables:** Project charter, PRD, ADD, WBS, roadmap, CLAUDE.md
- **Exit gate:** All spec documents reviewed and internally consistent
- **Notes:** Specs rewritten in v0.6.0 (WP 0.6) to reflect actual codebase state

---

## M1: Foundation — Complete

- **Deliverables:**
  - PROMPT_STATE Pydantic model with 3-layer architecture, `render()`, `derive()`, `diff()`
  - ProjectStore with file-based backend data storage
  - Backends router with sync, execute, and compare endpoints
  - Comparison service with McNemar's test, Wilcoxon signed-rank, hit@k, MRR
  - Test fixtures (conftest.py)
  - GitHub Actions CI (ruff lint + pytest)
  - Pipeline parameter passthrough: controllable TermNorm pipeline knobs forwarded and echoed
  - CLAUDE.md updated

- **Exit gate result:** All criteria met. No blocking bugs identified.

- **Notes:** Tests for evaluators and workflow runner were later pruned in `ceb9031` (reduced to core-only: PromptState + ProjectStore tests). CI remains green.

---

## M2: Core Optimizer — Largely Complete

M2 delivers the optimization capabilities as standalone services, with the notebook as the primary interface.

- **Deliverables (Complete):**
  - **HITL Campaign Notebook** (`optimization_campaign.ipynb` + `_campaign_lib.py`): full campaign workflow with config editing, candidate coverage diagnostics, baseline evaluation, iterative optimization with patience-based stopping, LLM-generated suggestions with phrase fragments, campaign summary with lineage
  - **Grid Search Service** (`grid_search.py`): default axis library, LLM context restructuring, distance-weighted stratified sampling, plan persistence with automatic resume, per-point query sampling, content-addressed deduplication, crash recovery, LLM result analysis with marginal stats and interaction heatmaps
  - **Prompt Evaluation Service** (`prompt_eval.py`): backend evaluation via `/matches` with `ranking_prompt` override, content-addressed deduplication, incremental `.partial.jsonl` writes, partial-run resume
  - **Prompt Optimizer Service** (`prompt_optimizer.py`): LLM meta-prompt candidate generation, round winner selection, improvement suggestions, campaign winner save
  - **Backend-Only Evaluation**: local evaluation fallback removed (`82157ef`); backend is the single source of truth
  - **Per-Point Query Sampling**: `resolve_point_evals()` centralizes deterministic query assignment for grid search
  - **Discovery Protocol**: `GET /pipeline` endpoint implemented in TermNorm; integrated into PromptPotter for grid search config validation

- **Deliverables (Remaining):**
  - Spec rewrite v0.6.0 (WP 2.7) — in progress

- **Exit gate:**
  - Grid search and optimization loop produce measurably improved prompts
  - HITL campaign notebook runs end-to-end with config editing, grid search, optimization, and LLM suggestions
  - Services are tested via PromptState and ProjectStore tests
  - Decision: are services ready for migration into workflow engine nodes?

- **Risks:**
  - Backend availability: every evaluation requires a running TermNorm backend instance
  - Groq rate limits: long grid search runs may hit 429 errors (mitigated by backoff)

---

## M3: Workflow Engine Migration — Planned

M3 migrates the service-based optimizer into the existing workflow engine (`api/core/workflow_runner.py`) as proper nodes. This gives DAG execution, node reuse, and API-driven orchestration.

- **Deliverables:**
  - **Service Layer Promotion**: Move orchestration logic from `_campaign_lib.py` into `api/services/` — dedup-aware evaluation (`evaluate_prompt()` logic → `prompt_eval.py`), grid pre-scan and plan lifecycle (`resume_or_build_grid()` → `grid_search.py`), replay execution reuse (`run_or_load_replay()` → `backend_client.py`). `_campaign_lib.py` becomes a genuinely thin wrapper (tqdm, IPython display, print formatting only). Prerequisite for node wrapping.
  - **Optimizer Nodes**: InitNode (wraps `restructure_context()`), GrowFilterNode (wraps `generate_candidates()`), AnalysisEvalNode (wraps `evaluate_prompt_batch()` + `generate_suggestions()`)
  - **Optimization Workflow Definition**: CWL-style wiring of nodes into a linear pipeline
  - **Campaign Registry**: formal campaign/trial persistence with Langfuse/MLflow-compatible data structure
  - **Feedback Cycling**: 3-path routing (generate / refine context / modify plan) with counter-based iteration
  - **E2E Optimization Test**: integration test verifying workflow execution and campaign persistence
  - **Langfuse Integration**: per-trial tracing with score attachment, campaign-level grouping

- **Entry criteria:** M2 exit gate passed; services are stable and tested

- **Exit gate:**
  - Optimization workflow runs end-to-end in the workflow engine
  - Campaign registry persists trials with Langfuse/MLflow-compatible structure
  - Feedback cycling routes correctly between three paths
  - E2E test passes in CI
  - Langfuse shows traces with scores for all optimization trials
  - Decision: is the workflow architecture right? Ready for TermNorm variant comparison?

- **Risks:**
  - Node wrapping may surface service interfaces that don't map cleanly to the workflow engine's input/output model
  - Feedback cycling adds state management complexity
  - Langfuse SDK version compatibility

---

## M4: Integration and Polish — Planned

M4 validates the system end-to-end with the TermNorm use case and adds supporting infrastructure.

- **Deliverables:**
  - **TermNorm Variant Comparison** (SC5): Variant A vs Variant B on BC5CDR 500-term subset. Optimize `llm_ranking` prompt, produce clear recommendation.
  - **Streamlit Dashboard**: campaign browser, trial comparison, dataset explorer
  - **Docker Compose**: optimizer + Langfuse with health checks
  - **Documentation**: README, notebooks, stale docs cleaned up

- **Entry criteria:** M3 exit gate passed; workflow engine migration working; campaign registry and Langfuse integrated

- **Exit gate:**
  - Variant A vs B comparison completes on BC5CDR with clear recommendation (SC5)
  - Results persisted in campaign registry and traceable via Langfuse
  - Docker deployment works with `docker-compose up`
  - Decision: does the optimized LLM2 call justify its cost? Which post-M4 features to prioritize?

- **Risks:**
  - TermNorm dataset format stability
  - Optimization may not produce enough improvement to justify LLM2 (valid result, not a failure)
  - Streamlit scope creep — keep to three core views

---

## Future (Post-M4, Unscheduled)

### Web Scrape Ablation

After Variant A vs B is settled: **how many websites to scrape** for entity profiling? Pipeline parameter passthrough (M1) makes this controllable via `max_sites` and `num_results`. Ablation study measures quality vs. cost/latency. Extends to LCA dataset for real-world validation.

### Public Service Deployment

Deploy as accessible public optimization service. Requires API key auth, rate limiting, multi-tenancy, hosting, monitoring. API is already stateless.

### Diverse Optimization Targets

Generalize beyond prompts (P2.4, SC6). Targets: scoring function weights, fuzzy matching parameters, retrieval queries, GA/DE settings, schemas. Each needs a state schema and target-specific initialization/grow logic. Core loop and evaluation framework are reusable.

### Additional Features

| Feature | PRD | Notes |
|---------|-----|-------|
| Evolutionary operators (GA/DE) | P2.1 | Population-based search |
| MCP server mode | P2.2 | Expose tools to Claude Code |
| Cached intermediates in grid search | — | Skip steps 1-3 with pre-computed data; ~10x speedup |
| Benchmarking and publication | — | Systematic benchmarks on BC5CDR, MedMentions, LCA |

Prioritization decided at M4 exit gate.

---

## Progression Rules

- Complete current milestone before starting the next; no skipping
- Each milestone ends with its decision gate; review before proceeding
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
- Specs can be updated at any milestone boundary (bump version)
