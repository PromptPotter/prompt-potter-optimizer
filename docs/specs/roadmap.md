# Roadmap: PromptPotter Optimizer

**Version:** 0.4.0
**Date:** 2026-02-20
**Status:** Draft
**Depends on:** [WBS v0.4.0](wbs.md)

---

## Milestones at a Glance

| Milestone | Focus | Timeline | Status |
|-----------|-------|----------|--------|
| M0 | Specifications | Week 1 | Complete |
| M1 | Foundation | Weeks 2-3 | Complete |
| M2 | Core Optimizer | Weeks 4-6 | In Progress |
| M3 | Registry and Tracking | Weeks 7-8 | Planned |
| M4 | Integration and Polish | Weeks 9-11 | Planned |

---

## M0: Specifications — Complete

- **Deliverables:** Project charter, PRD, ADD, WBS, roadmap, CLAUDE.md, CHANGELOG.md
- **Entry criteria:** Project repository exists with working workflow engine, evaluators, and LLM client
- **Exit gate:** All five spec documents reviewed and internally consistent; charter goals trace to PRD requirements; PRD requirements trace to WBS work packages
- **Risks:** None (complete)

---

## M1: Foundation — Complete

- **Deliverables:**
  - PROMPT_STATE Pydantic model with parameters dictionary
  - ProjectStore with file-based backend data storage and incremental writes
  - Backends router with sync, execute, and compare endpoints
  - Comparison service with McNemar's test, Wilcoxon signed-rank, hit@k, MRR
  - Test suite for evaluators, workflow runner, PromptState, incremental writes, API endpoints
  - Test fixtures and dataset helpers (conftest.py)
  - GitHub Actions CI (lint + test)
  - Pipeline parameter passthrough: 11 controllable TermNorm pipeline knobs forwarded, echoed, and logged
  - CLAUDE.md updated with M1 status

- **Entry criteria:** M0 specs approved, no open contradictions between charter/PRD/ADD

- **Exit gate:**
  - All tests pass, CI green
  - PROMPT_STATE model importable and unit tested
  - Decision: are there bugs in existing components that must be fixed before building the optimizer on top?
  - **Exit gate result:** All criteria met. No blocking bugs identified.

- **Progress (all 7 work packages complete):**
  - 1.1 PROMPT_STATE Model — **Complete** (`06b6635`)
  - 1.2 Test Fixtures and Dataset Helpers — **Complete** (`28833e3`, `7664b52`)
  - 1.3 Evaluator Tests — **Complete** (`7664b52`)
  - 1.4 Workflow Runner Tests — **Complete** (`7664b52`, `0d2acc1`)
  - 1.5 CI Pipeline — **Complete** (`7664b52`)
  - 1.6 CLAUDE.md Update — **Complete** (`3cc31f1`)
  - 1.7 Ablation Comparison — **Complete** (exceeds original scope: backend storage, notebook exploration, incremental writes, REST API endpoints)

- **Risks:** None (complete)

---

## M2: Core Optimizer (DAG-Based Workflow) — Weeks 4-6

- **Deliverables:**
  - **Phase 1 (linear mode)** of the DAG-based optimization workflow:
    - Initialization node: AI agent with structured output parsing (context --> prompt components)
    - Grow/Filter node: prompt state enrichment
    - Analysis + Evaluation node: scoring + failure analysis + next_action decision
    - Linear mode orchestrator: init --> grow/filter --> evaluate --> output, run N times for breadth
  - Updated optimize endpoint replacing the placeholder (P0.4)
  - E2E test: linear mode optimization on sample dataset produces scored prompt states
  - **HITL Campaign Notebook**: Interactive Jupyter notebook with editable campaign config,
    integrated replay, candidate coverage diagnostics, iterative prompt optimization,
    and LLM-generated phrase fragment suggestions for user-guided iteration
  - **Grid Search**: Default axis library, LLM context restructuring, systematic Layer 1 sweep with ranked results + heatmaps + LLM analysis
  - **Phase 2 (cycling mode)** partially implemented:
    - Feedback router (Switch: generate / refine context / modify plan)
    - Context refinement and plan update nodes
    - Counter-based stop condition with configurable threshold

- **Entry criteria:** M1 exit gate passed; CI green; PROMPT_STATE model finalized. **Entry criteria satisfied.**

- **Exit gate:**
  - Optimize endpoint runs the linear mode DAG end-to-end
  - N independent runs produce diverse prompt states; best is selectable by score
  - E2E test passes in CI
  - HITL campaign notebook produces actionable suggestions after optimization rounds
  - Grid search produces ranked exploration results with LLM-analyzed insights
  - Decision: does the linear mode produce useful prompt states? Is the DAG architecture right for adding cycling later?

- **Progress:**
  - 2.1 Initialization Node — Not started
  - 2.2 Grow/Filter Node — Not started
  - 2.3 Analysis + Evaluation Node — Not started
  - 2.4 Linear Mode Orchestrator — Not started
  - 2.5 Optimize Router — Not started
  - 2.6 E2E Test — Not started
  - 2.7 Cycling Mode — Not started
  - 2.8 HITL Campaign Notebook — **Complete** (exceeds scope: trace parsing, eval caching, incremental writes, crash protection, rate-limit backoff, `_campaign_lib.py` service extraction, training-style progress display, semi-automatic optimization loop with patience-based stopping)
  - 2.9 Grid Search — **Complete** (default axes, LLM restructuring, grid execution/visualization/analysis, winner selection, distance-weighted stratified sampling with `n_combos` + `exploration_rate`, per-combo caching + partial-run resume)

- **Risks:**
  - LLM-based structured output quality may vary (initialization node depends on Groq + Llama 4 Maverick producing valid structured responses)
  - Grow/Filter node may produce insufficiently diverse enrichments without cycling feedback
  - Largest milestone; highest scope creep risk — cycling mode (2.7) should be deferred if linear mode takes longer than expected

---

## M3: Registry and Tracking — Weeks 7-8

- **Deliverables:**
  - File-based CampaignRegistry with campaign/trial persistence (P1.1)
  - Registry integrated into the optimization loop
  - Lineage tracking: trial parent-child tree (P0.5)
  - Langfuse score integration: per-trial traces with scores (SC3)
  - JSONL export and campaign list/detail endpoints

- **Entry criteria:** M2 exit gate passed; optimization loop works end-to-end

- **Exit gate:**
  - Optimization runs automatically persist to `.promptpotter/campaigns/`
  - Campaigns viewable and exportable via API
  - Langfuse shows correct parent-child traces with scores
  - Decision: is the persisted data useful for comparing runs? Is the file layout right?

- **Risks:**
  - File-based storage may have concurrency issues if parallel execution is added later
  - JSONL format choices become a compatibility contract; changing later breaks consumers

---

## M4: Integration and Polish — Weeks 9-11

- **Deliverables:**
  - TermNorm Variant A vs Variant B comparison on BC5CDR 500-term subset (SC5)
  - Optimized `llm_ranking` prompt versions written back to TermNorm's prompt registry
  - Real web search provider replacing mock (P1.4)
  - Human-in-the-loop gates: pause/approve/reject/edit (P1.3)
  - Streamlit dashboard: campaign browser, trial comparison, dataset explorer (P2.4)
  - Docker compose for optimizer + Langfuse
  - Documentation update (README, notebooks)

- **Entry criteria:** M3 exit gate passed; registry and Langfuse working

- **Exit gate:**
  - **Variant A vs Variant B comparison completes** on the BC5CDR 500-term subset: Variant A baseline established, `llm_ranking` prompt optimized (v2+), best Variant B score compared against Variant A, clear recommendation produced (SC5)
  - Results are persisted in the campaign registry and traceable in Langfuse
  - Time to first optimization under 15 minutes (SC4)
  - Docker deployment works with `docker-compose up`
  - Decision: does the optimized LLM2 call justify its cost? Ready for others to use? Which P2 features to prioritize next?

- **Risks:**
  - TermNorm integration depends on external dataset availability and format stability
  - Optimizing `llm_ranking` may not produce enough improvement to justify the LLM2 call -- this is a valid result, not a failure
  - HITL UX is hard to get right for both API and notebook modes
  - Dashboard scope can expand; keep to three core views

---

## Future (Post-M4, Unscheduled)

### Next Validation: Web Scrape Ablation

After the Variant A vs Variant B comparison is settled, the next decision point is: **how many websites to scrape** for `entity_profiling`? The pipeline parameter passthrough infrastructure (M1) already makes this controllable via `max_sites` and `num_results` — the ablation study uses these knobs to vary the scrape count while holding the winning variant's prompts fixed, measuring the quality vs. cost/latency tradeoff. Also extends validation to the LCA dataset for real-world use case confirmation.

### Public Service Deployment

Deploy PromptPotter as an accessible public optimization service. Requires:
- API key authentication and rate limiting middleware (P2.6)
- Multi-tenancy: isolated project stores per user/team
- Hosting infrastructure (cloud deployment, monitoring, uptime)
- Billing and usage tracking (if commercial)
- The API is already designed stateless (no server-side session state), minimizing the architectural changes needed

### Diverse Optimization Targets

Generalize the DAG-based optimization loop beyond prompts (P2.5, SC6). Target types to explore:
- **Scoring function weights** — optimize ranking coefficients for retrieval/matching systems
- **Fuzzy matching parameters** — thresholds, algorithms, and configurations for string matching
- **Retrieval queries** — query templates and expansion strategies
- **GA/DE parameters** — population size, mutation rates, crossover strategies
- **Schemas** — data model configurations that affect pipeline behavior

Each new target type requires a state schema (Pydantic model) and target-specific initialization/grow logic. The core loop, evaluation framework, and feedback routing are reusable.

### Benchmarking and Publication

Systematic benchmarks for archival publication:
- **BC5CDR** — primary development benchmark (500-term subset, well-known ground truth)
- **MedMentions** — additional biomedical benchmark (500-term subset)
- **LCA datasets** — real-world Life Cycle Assessment terminology normalization
- Reproducible experiment runs with full campaign persistence and statistical reports

### Additional Features

| Feature | PRD | Notes |
|---------|-----|-------|
| Web scrape count ablation | -- | Vary number of websites scraped, measure quality vs. cost/latency |
| LCA dataset validation | -- | Real-world use case validation after BC5CDR development is complete |
| Full cycling mode (if not completed in M2) | P0.4, P2.1 | Enable feedback paths with iterative refinement; "refine context" path provides reflection-like capability |
| Evolutionary operators (GA/DE) | P2.2 | Population-based search for multi-parameter optimization |
| MCP server mode | P2.3 | Expose optimization tools to Claude Code and MCP clients |
| Workflow-based optimization | P1.2 | Optimize single steps within multi-step pipelines |

Prioritization decided at the M4 exit gate based on Variant A vs Variant B results.

---

## Progression Rules

- Complete current milestone before starting the next; no skipping
- Each milestone ends with its decision gate; review before proceeding
- Update CLAUDE.md at each milestone boundary
- One Claude Code session = one WBS work package
- Specs can be updated at any milestone boundary (bump version + changelog)
