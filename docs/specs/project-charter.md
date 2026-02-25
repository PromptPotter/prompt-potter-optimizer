# Project Charter: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active

---

## Key Terms

| Term | Definition |
|------|-----------|
| **Campaign** | A single optimization run from start to finish. A campaign starts with a baseline configuration, runs one or more trials, and ends with a recommended best configuration. Persisted via `CampaignStore` with full trial lineage. |
| **Trial** | One iteration within a campaign. Each trial tests a candidate configuration against the evaluation dataset and records the results. Persisted as trial detail files under the campaign directory. |
| **PROMPT_STATE** | The tracked, versioned snapshot of a prompt organized into three optimization layers: **Layer 1 (Generate)** structured prompt components (persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples), **Layer 2 (Refine Context)** optimization context and hypervariables (context, parameters), **Layer 3 (Modify Plan)** optimization strategy (plan). Immutable; includes `render()` to assemble Layer 1 into prompt text, `derive()` for lineage-tracked children. |
| **Evaluation dataset** | A labeled set of input/expected-output pairs used to score how well a configuration performs. Datasets are owned by the consuming project, not by PromptPotter. |
| **Grid Search** | Systematic exploration of Layer 1 prompt field variants via cartesian product sweep with distance-weighted stratified sampling, content-addressed deduplication, and LLM-assisted analysis. |
| **Sensitivity Scan** | One-at-a-time (OAT) perturbation scanning that classifies prompt axes by their impact on accuracy. Uses a diagnostic set with stratified sampling for regression guard and improvement signal. |
| **Feedback Cycle** | The automated AI loop: GrowFilterNode generates candidates -> AnalysisEvalNode evaluates and selects winner -> routing determines next action (generate/refine_context/modify_plan/stop). Orchestrated by `run_feedback_cycle()`. |
| **Data Loop** | The principle that every evaluation -- from any optimization path -- writes to the shared `dataset_runs` store, making all prior eval data automatically available to the coverage advisor for future optimizations. |
| **Coverage Advisor** | The system (`search/coverage.py`) that discovers all stored `dataset_runs` and determines which prompt variants already have cached evaluation data, enabling the data loop. |

---

## Problem Statement

Tuning LLM-powered systems today is manual and untracked. Developers adjust prompts, few-shot examples, temperature, retrieval counts, and other parameters through trial and error. There is no systematic record of what changed, why it changed, or whether it actually helped. When something works, the reasoning is lost. When something breaks, there is no way to roll back to a known-good state.

Existing optimization frameworks (DSPy, TextGrad, EvoPrompt) each solve parts of this problem but require adopting their abstractions wholesale. A team using one framework cannot easily switch to another or combine techniques from several.

### Motivating Use Case: TermNorm

**TermNorm** is an AI-powered terminology normalization system that matches free-form text (product names, material codes, process terms) to standardized database identifiers. Its primary domain is **Life Cycle Assessment (LCA)** workflows, where it disambiguates against 11,750+ database entries. Development and testing uses the **BC5CDR 500-term subset** as the primary benchmark (well-known ground truth, scientifically reproducible, suitable for archival publication). MedMentions 500-term subset serves as an additional biomedical benchmark. LCA dataset validation follows when deploying to real-world use.

TermNorm's pipeline uses two LLM calls connected by a non-LLM table reranker:

1. **LLM1 (`entity_profiling`)** -- extracts a structured entity profile from web-scraped research data. Template variables: `query`, `format_string`, `combined_text`.
2. **Table Reranker** -- a non-LLM step that reranks candidates using token/string matching. Fast and cheap, but has no semantic understanding.
3. **LLM2 (`llm_ranking`)** -- semantic reranking of 20 candidates using the entity profile, weighted 70% on core concept match with a 4-tier scoring rubric. Template variables: `core_concept`, `entity_profile_json`, `matches`.

TermNorm already has a versioned prompt registry (`backend-api/logs/prompts/` with `PromptRegistry`) storing both prompt families as `{{variable}}`-templated text with metadata (author, model recommendation, temperature).

The central research question for PromptPotter validation is a **pipeline variant comparison**:

- **Variant A**: Web scrape --> LLM1 (`entity_profiling`) --> Table Reranker --> done (skip LLM2)
- **Variant B**: Web scrape --> LLM1 (`entity_profiling`) --> Table Reranker --> LLM2 (`llm_ranking`) --> done

**Does LLM2 semantic reranking add enough accuracy over the "dumb" table reranker to justify the extra LLM cost and latency?** This is a concrete, testable question. When PromptPotter can run both variants against the same evaluation dataset and produce a clear, traceable recommendation, we know the whole system works.

**Evaluation constraint:** Optimizing the `llm_ranking` prompt requires running the full TermNorm pipeline per query via the backend's `/matches` endpoint. The token matching step (Stage 2) queries a loaded database that cannot be replicated locally. PromptPotter injects candidate prompts via the `ranking_prompt` parameter -- the backend runs web search -> LLM1 -> token matching -> LLM2 with the candidate prompt (~10-30s/query). A future `/rerank` endpoint on TermNorm (accepting pre-computed intermediates) would eliminate the redundant steps 1-3.

A future decision point (post-M4) extends this: **how many websites to scrape** for entity profiling -- a quality vs. cost/latency tradeoff that becomes the second ablation study.

The TermNorm use case validates the core loop, but the underlying problem -- systematically tuning configuration and measuring impact -- applies far beyond prompts. Schemas, scoring functions, fuzzy matching parameters, retrieval queries, and genetic algorithm settings are all structured, non-code parameters that benefit from the same analyze-generate-evaluate cycle. PromptPotter's architecture is designed to generalize to these diverse optimization targets over time.

---

## Vision

An **API-first, framework-agnostic optimization service** that automates the analyze-generate-evaluate loop for **any AI-powered system**. PromptPotter optimizes any tunable non-code configuration -- prompts, schemas, scoring functions, fuzzy matching parameters, retrieval queries, genetic algorithm settings, few-shot examples, thresholds, retrieval counts, temperature, and other structured parameters that affect system behavior.

The system keeps humans in control of strategy and priorities through decision gates, while automating the repetitive work of generating candidates, running evaluations, and tracking what improved. PromptPotter serves both as a private development tool and, eventually, as an accessible public optimization service.

### North Star: Two-Loop Optimization

PromptPotter's core mental model is two nested feedback loops:

- **Human Loop** (strategic): Explore -> Optimize -> Harvest -> Reuse. The human decides what to explore, when to stop, and when to start a fresh optimization with accumulated data.
- **AI Loop** (tactical): Generate -> Evaluate -> Select -> Iterate. The feedback cycle automates candidate generation and evaluation within the boundaries set by the human.

The **data loop** connects them: every evaluation from any optimization path writes to the shared `dataset_runs` store, making all prior work available to the coverage advisor when the human starts a new cycle.

### North Star: Accessible Public Service

PromptPotter is designed for eventual deployment as a publicly accessible optimization service -- a hosted API where any developer can submit an optimization task and receive back improved configurations with statistical evidence. Near-term milestones (M1-M4) focus on the core optimization loop and single-user workflows; public deployment (authentication, rate limiting, multi-tenancy) is a post-M4 goal.

### North Star: Diverse Optimization Targets

While M1-M3 implement the concrete prompt optimization case, the optimization loop is designed to be target-agnostic. The same analyze-generate-evaluate cycle works for any structured parameter: schemas, scoring functions, fuzzy matching thresholds, retrieval queries, GA/DE settings. The architecture separates the optimization loop from the parameter type so that new target types can be added without rewriting the core engine.

**Core principles:**

- **Framework-agnostic** -- no runtime dependency on LangChain, DSPy, or any other framework. Borrows ideas from the research literature, builds on its own abstractions.
- **Observable by default** -- optimization runs are traced via Langfuse with structured scores, campaign-level trace grouping, and per-round spans with accuracy scores.
- **Dual-mode delivery** -- available as both a FastAPI REST service for automation and a JupyterLab environment for interactive exploration.
- **Target-agnostic** -- the optimization loop works on any structured parameter, not just prompt strings. The service layer operates on a pluggable state schema (PROMPT_STATE); M1-M3 build the concrete prompt case, post-M4 generalizes.

---

## Scope

### In Scope

- **Iterative prompt optimization** -- automated failure analysis, LLM-driven candidate generation, and backend evaluation via the feedback cycle orchestrator (`feedback_cycle.py`)
- **Sensitivity scan** -- OAT perturbation scanning with axis classification to identify high-impact prompt fields (`search/smart_search.py`)
- **Grid search exploration** -- systematic exploration of Layer 1 prompt field variants via cartesian product sweep with plan persistence and LLM-assisted analysis (`search/grid_core.py`)
- **Backend evaluation** -- evaluation via the backend's `/matches` endpoint with `ranking_prompt` override. Content-addressed caching, incremental crash recovery, and the `evaluate_prompt_cached()` single gateway. (`prompt_eval.py`)
- **Campaign registry** -- structured campaign/trial persistence with Langfuse/MLflow-compatible data format (`campaign_registry.py`, `stores/campaign_store.py`)
- **Data loop** -- all eval data feeds back via shared `dataset_runs` store; coverage advisor discovers cached results across optimization threads (`search/coverage.py`)
- **Notebook-first HITL** -- the Jupyter notebook (`optimization_campaign.ipynb`) with `_campaign_lib.py` is the primary optimization interface
- **API-first delivery** -- FastAPI REST service with structured Pydantic input/output contracts. Routers: health, backends, workflows.
- **Observability** -- Langfuse integration with per-trial tracing, campaign grouping, accuracy scores (`langfuse_client.py`)
- **File-based project store** -- persistent storage under `.promptpotter/projects/` with focused store modules

### Out of Scope

- **Fine-tuning or model training** -- PromptPotter optimizes parameters passed to LLMs, it does not modify model weights
- **Public deployment infrastructure (M1-M4)** -- no authentication, billing, or multi-tenancy in M1-M4. The API is designed stateless to enable future public deployment, but hosting infrastructure is post-M4
- **Agent training or reinforcement learning** -- no reward-model training or policy gradient methods
- **Production prompt serving** -- PromptPotter finds better configurations, it does not serve them at inference time
- **Dataset hosting** -- evaluation datasets live in the consuming project's repository (e.g., the TermNorm repo), not in PromptPotter

### Future Scope

These items are explicitly deferred, not permanently excluded:

- **Public service deployment** -- authentication, rate limiting, multi-tenancy, and hosting infrastructure
- **Non-prompt optimization targets** -- generalizing to schemas, scoring functions, fuzzy matchers, retrieval queries, and GA parameters
- **Evolutionary operators** -- GA/DE population-based optimization
- **MCP server mode** -- expose optimization as MCP tools
- **Streamlit dashboard** -- visual campaign browser and trial comparison
- **Benchmarking and publication** -- systematic benchmarks against MedMentions, BC5CDR, and LCA datasets for archival publication

---

## Success Criteria

| # | Criterion | Measurement | Target |
|---|-----------|-------------|--------|
| 1 | **Measurable improvement** | Score delta between initial and best configuration on the evaluation dataset | **10%+ improvement** on at least one user-defined metric within a single campaign |
| 2 | **Reproducibility** | Given identical inputs, the optimizer produces consistent results | Content-addressed caching and plan persistence ensure **identical results** for the same inputs. Scores vary by **less than 5%** across repeated runs (accounting for LLM non-determinism at temperature 0) |
| 3 | **Langfuse observability** | Optimization campaigns appear in Langfuse with per-trial scores and campaign grouping | **Implemented** -- per-trial spans with accuracy scores, campaign-level traces, graceful fallback when credentials missing |
| 4 | **Time to first optimization** | A developer with API keys configured can run their first optimization campaign | **Under 15 minutes** from `pip install` to completed campaign, using the notebook |
| 5 | **TermNorm validation** | Compare Variant A vs Variant B on the same evaluation dataset | Produces a clear result showing **which variant is better and by how much**, with full campaign persisted and traceable. **Planned for M4.** |
| 6 | **Generalization beyond prompts** | Optimize at least one non-prompt parameter type using the same optimization loop | Successful optimization with measurable improvement, demonstrating target-agnostic architecture. **Post-M4** |

---

## Stakeholders

| Role | Who | Responsibilities |
|------|-----|-----------------|
| **Project owner / prompt engineer** | Solo developer building LLM-powered applications | Defines the system to optimize, selects evaluation datasets, sets success metrics, reviews optimization candidates at decision gates, and decides which configuration to promote to production |
| **Pipeline operator** | Developer or CI/CD system integrating optimization into automated workflows | Calls the REST API from scripts or pipelines, monitors campaign status, and consumes structured JSON results for downstream use |
| **Consuming-project maintainer** | Owner of the system being optimized (e.g., TermNorm) | Maintains the evaluation datasets in their own repository, defines what "good" looks like for their domain, and validates that optimized configurations work in their production context |

---

## Constraints

| Constraint | Rationale |
|------------|-----------|
| **Framework-agnostic** -- no LangChain, DSPy, or similar runtime dependency | Avoids vendor lock-in; users keep their existing architecture |
| **OpenAI-compatible LLM providers** -- currently Groq and OpenAI; any provider exposing the OpenAI chat completions API | Supports the user's current provider (Groq with Llama 4 Maverick) and allows switching without code changes |
| **File-based registry first** -- no database dependency for MVP | Keeps deployment simple (Docker + filesystem); can migrate to SQLite or a database later behind the same interface |
| **Datasets are external** -- evaluation datasets live in the consuming project, not in PromptPotter | PromptPotter is a tool, not a data store; datasets are domain-specific and versioned with their own project |
| **Docker-deployable** -- minimal configuration to run | Single `docker-compose up` with environment variables for API keys |
| **Backend evaluation only** -- no local evaluation fallback | The backend's `/matches` endpoint is the single source of truth. Local evaluation was removed because the token matching step requires the backend's loaded database. |

---

## References

| Document | Description |
|----------|-------------|
| [Literature Review](../literature-review.md) | Survey of 11+ prompt optimization frameworks and their paradigms |
| [Registry Design](../registry-design.md) | Campaign/trial tracking pattern based on MLflow, DSPy, and OpenAI Evals conventions |
| [PRD](prd.md) | Detailed requirements (P0/P1/P2) with acceptance criteria |
| [ADD](add.md) | Architecture, decision records, data model, and API contracts |
| [WBS](wbs.md) | Work breakdown structure with session estimates and dependencies |
| [Roadmap](roadmap.md) | Milestone timeline, progress tracking, and decision gates |
