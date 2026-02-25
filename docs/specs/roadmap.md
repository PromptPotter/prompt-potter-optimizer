# Roadmap: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [WBS v0.7.0](wbs.md)

---

## Milestones at a Glance

| Milestone | Focus | Status |
|-----------|-------|--------|
| M0 | Specifications | Complete |
| M1 | Foundation | Complete |
| M2 | Core Optimizer (Services) | Complete |
| M3 | Optimization Infrastructure | Nearly Complete |
| M4 | Integration and Polish | Planned |

---

## M0: Specifications -- Complete

- **Deliverables:** Project charter, PRD, ADD, WBS, roadmap, CLAUDE.md
- **Exit gate:** All spec documents reviewed and internally consistent

---

## M1: Foundation -- Complete

- **Deliverables:**
  - PROMPT_STATE Pydantic model with 3-layer architecture, `render()`, `derive()`, `diff()`
  - ProjectStore with file-based backend data storage
  - Backends router with sync, execute, and compare endpoints
  - Comparison service with McNemar's test, Wilcoxon signed-rank, hit@k, MRR
  - Test fixtures (conftest.py)
  - GitHub Actions CI (ruff lint + pytest)
  - Pipeline parameter passthrough: controllable TermNorm pipeline knobs forwarded and echoed

- **Exit gate result:** All criteria met. No blocking bugs identified.

---

## M2: Core Optimizer -- Complete

M2 delivers the optimization capabilities as standalone services, with the notebook as the primary interface.

- **Deliverables:**
  - **HITL Campaign Notebook** (`optimization_campaign.ipynb` + `_campaign_lib.py`): full campaign workflow
  - **Grid Search Service**: default axis library, LLM context restructuring, distance-weighted stratified sampling, plan persistence, content-addressed deduplication
  - **Prompt Evaluation Service**: backend evaluation via `/matches`, content-addressed deduplication, incremental crash recovery
  - **Prompt Optimizer Service**: LLM meta-prompt candidate generation, winner selection, suggestions
  - **Backend-Only Evaluation**: local evaluation fallback removed; backend is the single source of truth
  - **Discovery Protocol**: `GET /pipeline` integrated for grid search config validation

- **Exit gate result:** All criteria met. Services functional and tested. Notebook runs end-to-end.

---

## North Star: The Two-Loop Architecture

The core human workflow is two nested feedback loops:

### Human Loop (Explore - Optimize - Harvest - Reuse)

```
Generate data --> Explore (scan/grid) --> Optimize (feedback cycle)
      ^                                         |
      |         Human stops, reviews results     |
      |                                         v
      +<<<<< All eval data feeds back into <<<<<+
              coverage advisor & historical
              index for the next cycle
```

### AI Loop (Generate - Evaluate - Select - Iterate)

```
  InitNode --> GrowFilterNode --> AnalysisEvalNode
  (baseline)   (N candidates)    (eval + select)
                     ^                  |
                     |            next_action
                     |                  |
                     +------------------+
                     (generate / refine_context / modify_plan / stop)
```

**Key principle:** Every backend evaluation -- whether from grid search, sensitivity scan, or feedback cycle -- writes to the same `dataset_runs` store with content-addressed deduplication. The coverage advisor automatically discovers all stored results. No data is siloed per campaign.

---

## M3: Optimization Infrastructure -- Nearly Complete

M3 builds the optimization infrastructure: optimizer nodes, feedback cycle orchestrator, sensitivity scan, campaign registry, Langfuse integration, and the data loop.

- **Deliverables (Complete):**
  - **Optimizer Nodes**: InitNode, GrowFilterNode, AnalysisEvalNode (`api/nodes/optimizer_nodes.py`)
  - **Feedback Cycle**: `run_feedback_cycle()` with 3-path routing, patience-based stopping, per-query/candidate progress callbacks (`api/services/feedback_cycle.py`)
  - **Campaign Registry**: `CampaignStore` + `campaign_registry.py` for campaign/trial persistence with Langfuse/MLflow-compatible data format
  - **Campaign Init**: `init_services()` for campaign setup with auto-sync (`api/services/campaign_init.py`)
  - **Langfuse Integration**: per-trial spans with accuracy scores, campaign-level traces, graceful fallback (`api/services/langfuse_client.py`)
  - **Sensitivity Scan**: OAT perturbation scanning, axis classification, diagnostic set builder (`api/services/search/smart_search.py`)
  - **Coverage Advisor**: historical index and coverage assessment for eval reuse (`api/services/search/coverage.py`)
  - **Grid Search Refactor**: search module with `grid_core.py`, `context.py`, `plan_persistence.py`, `synthesis.py`
  - **Notebook Integration**: `run_feedback_cycle_notebook()` wires callbacks to display progress, handles session init
  - **Data Loop**: all eval paths write to shared `dataset_runs` store; coverage advisor discovers results across threads

- **Deliverables (Remaining):**
  - **Service Layer Cleanup**: reduce duplication between `_campaign_lib.py` eval wrappers and service-layer functions. `_campaign_lib.py` should be a genuinely thin wrapper (progress display only, no business logic).

- **Exit gate:**
  - Optimization workflow runs end-to-end with per-query progress output
  - Feedback cycle eval data feeds back into sensitivity scan's historical index
  - Langfuse shows traces with scores for all optimization trials
  - Decision: ready for TermNorm variant comparison (M4)?

- **Risks:**
  - Groq rate limits / capacity: long optimization runs may hit 503 errors (needs retry with backoff in `llm_client.py`)
  - Langfuse SDK version compatibility

---

## M4: Integration and Polish -- Planned

M4 validates the system end-to-end with the TermNorm use case and adds supporting infrastructure.

- **Deliverables:**
  - **TermNorm Variant Comparison** (SC5): Variant A vs Variant B on BC5CDR 500-term subset. Use the full two-loop workflow: sensitivity scan -> grid search -> feedback cycle optimization -> comparison with statistical significance.
  - **Streamlit Dashboard**: campaign browser, trial comparison, dataset explorer
  - **Docker Compose**: optimizer + Langfuse with health checks
  - **Documentation**: README, notebooks, stale docs cleaned up

- **Entry criteria:** M3 exit gate passed; feedback cycle and campaign registry working; Langfuse integrated

- **Exit gate:**
  - Variant A vs B comparison completes on BC5CDR with clear recommendation (SC5)
  - Results persisted in campaign registry and traceable via Langfuse
  - Docker deployment works with `docker-compose up`
  - Decision: does the optimized LLM2 call justify its cost? Which post-M4 features to prioritize?

- **Risks:**
  - TermNorm dataset format stability
  - Optimization may not produce enough improvement to justify LLM2 (valid result, not a failure)
  - Streamlit scope creep -- keep to three core views

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
| Cached intermediates in grid search | -- | Skip steps 1-3 with pre-computed data; ~10x speedup |
| Benchmarking and publication | -- | Systematic benchmarks on BC5CDR, MedMentions, LCA |
| LLM client retry logic | -- | Groq 503 handling with exponential backoff |

Prioritization decided at M4 exit gate.

---

## Progression Rules

- Complete current milestone before starting the next; no skipping
- Each milestone ends with its decision gate; review before proceeding
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
- Specs can be updated at any milestone boundary (bump version)
