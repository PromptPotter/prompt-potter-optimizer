# Project Charter: PromptPotter Optimizer

**Version:** 0.5.0
**Date:** 2026-02-22
**Status:** Draft

---

## Key Terms

| Term | Definition |
|------|-----------|
| **Campaign** | A single optimization run from start to finish. A campaign starts with a baseline configuration, runs one or more trials, and ends with a recommended best configuration. |
| **Trial** | One iteration within a campaign. Each trial tests a candidate configuration against the evaluation dataset and records the results. |
| **PROMPT_STATE** | The tracked, versioned snapshot of a prompt organized into three optimization layers: **Layer 1 (Generate)** structured prompt components (persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples), **Layer 2 (Refine Context)** optimization context and hypervariables (context, parameters), **Layer 3 (Modify Plan)** optimization strategy (plan). Immutable; includes `render()` to assemble Layer 1 into prompt text, `derive()` for lineage-tracked children, and `OptimizationDefaults` for Layer 3 strategy parameters. Every trial produces a new PROMPT_STATE. |
| **Evaluation dataset** | A labeled set of input/expected-output pairs used to score how well a configuration performs. Datasets are owned by the consuming project, not by PromptPotter. |

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

**Evaluation constraint:** Optimizing the `llm_ranking` prompt requires running the full TermNorm pipeline per query via the backend's `/matches` endpoint. The token matching step (Stage 2) queries a loaded database that cannot be replicated locally. PromptPotter injects candidate prompts via the `ranking_prompt` parameter — the backend runs web search → LLM1 → token matching → LLM2 with the candidate prompt. This is slower (~10-30s/query) but gives accurate end-to-end results. A future `/rerank` endpoint on TermNorm (accepting pre-computed intermediates) would eliminate the redundant steps 1-3.

A future decision point (post-M4) extends this: **how many websites to scrape** for entity profiling -- a quality vs. cost/latency tradeoff that becomes the second ablation study.

The TermNorm use case validates the core loop, but the underlying problem — systematically tuning configuration and measuring impact — applies far beyond prompts. Schemas, scoring functions, fuzzy matching parameters, retrieval queries, and genetic algorithm settings are all structured, non-code parameters that benefit from the same analyze-generate-evaluate cycle. PromptPotter's architecture is designed to generalize to these diverse optimization targets over time.

---

## Vision

An **API-first, framework-agnostic optimization service** that automates the analyze-generate-evaluate loop for **any AI-powered system**. PromptPotter optimizes any tunable non-code configuration — prompts, schemas, scoring functions, fuzzy matching parameters, retrieval queries, genetic algorithm settings, few-shot examples, thresholds, retrieval counts, temperature, and other structured parameters that affect system behavior.

The system keeps humans in control of strategy and priorities through decision gates, while automating the repetitive work of generating candidates, running evaluations, and tracking what improved. PromptPotter serves both as a private development tool and, eventually, as an accessible public optimization service.

### North Star: Accessible Public Service

PromptPotter is designed for eventual deployment as a publicly accessible optimization service — a hosted API where any developer can submit an optimization task and receive back improved configurations with statistical evidence. Near-term milestones (M1-M4) focus on the core optimization loop and single-user workflows; public deployment (authentication, rate limiting, multi-tenancy) is a post-M4 goal.

### North Star: Diverse Optimization Targets

While M2-M4 implement the concrete prompt optimization case, the DAG-based optimization loop is designed to be target-agnostic. The same analyze-generate-evaluate cycle works for any structured parameter: schemas, scoring functions, fuzzy matching thresholds, retrieval queries, GA/DE settings. The architecture separates the optimization loop from the parameter type so that new target types can be added without rewriting the core engine.

**Core principles:**

- **Framework-agnostic** — no runtime dependency on LangChain, DSPy, or any other framework. Borrows ideas from the research literature, builds on its own abstractions.
- **Observable by default** — every optimization run is traced in **Langfuse** (an open-source LLM observability platform) with structured scores, parent-child run hierarchy, and full lineage.
- **Dual-mode delivery** — available as both a FastAPI REST service for automation and a JupyterLab environment for interactive exploration.
- **Target-agnostic** — the optimization loop works on any structured parameter, not just prompt strings. The DAG operates on a pluggable state schema; M2-M4 build the concrete prompt case, post-M4 generalizes.

---

## Scope

### In Scope

- **Parameter optimization** — iterative improvement of any tunable non-code configuration: prompts, schemas, scoring functions, fuzzy matching parameters, retrieval queries, GA settings, few-shot examples, temperature, retrieval counts, thresholds, and other structured parameters through automated failure analysis, candidate generation, and evaluation
- **Workflow-based optimization** — optimization of individual steps within multi-step pipelines (e.g., retrieval followed by ranking followed by classification), using the existing workflow engine
- **API-first delivery** — FastAPI REST service with structured Pydantic input/output contracts
- **Human-in-the-loop gates** — decision points where developers review and approve candidates before promotion
- **Evaluation framework** — automated scoring against labeled datasets with multiple evaluator strategies (exact match, LLM-as-judge, custom)
- **Langfuse integration** — tracing, scoring, and lineage tracking for every campaign and trial (required for MVP, not optional)
- **Campaign and trial tracking** — persistent registry of optimization runs with JSONL export, following the parent-child run hierarchy pattern

### Out of Scope

- **Fine-tuning or model training** — PromptPotter optimizes parameters passed to LLMs, it does not modify model weights
- **Public deployment infrastructure (M1-M4)** — no authentication, billing, or multi-tenancy in M1-M4. The API is designed stateless to enable future public deployment, but hosting infrastructure is post-M4
- **Agent training or reinforcement learning** — no reward-model training or policy gradient methods
- **Production prompt serving** — PromptPotter finds better configurations, it does not serve them at inference time
- **GUI/dashboard beyond prototypes** — Streamlit apps for development use, not a production dashboard
- **Dataset hosting** — evaluation datasets live in the consuming project's repository (e.g., the TermNorm repo), not in PromptPotter

### Future Scope

These items are explicitly deferred, not permanently excluded:

- **Public service deployment** — authentication, rate limiting, multi-tenancy, and hosting infrastructure for making PromptPotter accessible as a public API (north star)
- **Non-prompt optimization targets** — generalizing the optimization loop to schemas, scoring functions, fuzzy matchers, retrieval queries, and GA parameters (north star)
- **Layered access control** — anonymous, API-key-authenticated, and admin tiers with per-user data isolation, enabling safe multi-tenant deployment
- **Benchmarking and publication** — systematic benchmarks against MedMentions, BC5CDR, and LCA datasets for archival publication

**Future direction:** The ablation comparison workflow (upload experiment data, remove a pipeline component, see statistical comparison with p-values) is designed for self-service use across multiple client types (CLI, notebooks, JS frontend). When PromptPotter is deployed as a web service with user credentials, this becomes a first-class UI flow with pipeline visualization.

---

## Success Criteria

| # | Criterion | Measurement | Target |
|---|-----------|-------------|--------|
| 1 | **Measurable improvement** | Score delta between initial and best configuration on the evaluation dataset | **10%+ improvement** on at least one user-defined metric (e.g., accuracy, F1) within a single campaign |
| 2 | **Reproducibility** | Given identical inputs (initial configuration, dataset, campaign settings), the optimizer produces a consistent improvement trajectory | Scores for the same trial vary by **less than 5%** across repeated runs (accounting for LLM non-determinism with temperature 0) |
| 3 | **Langfuse observability** | Optimization campaigns appear in Langfuse with correct parent-child trace hierarchy and per-trial scores | **100%** of trials have associated Langfuse traces and scores |
| 4 | **Time to first optimization** | A developer with API keys configured can run their first optimization campaign | **Under 15 minutes** from `pip install` to completed campaign, using the API or a notebook |
| 5 | **TermNorm validation** | Compare Variant A (table reranker only) vs Variant B (table reranker + LLM2 semantic reranking) on the same evaluation dataset | Produces a clear result showing **which variant is better and by how much**, with full campaign persisted and traceable in Langfuse |
| 6 | **Generalization beyond prompts** | Optimize at least one non-prompt parameter type (e.g., scoring function weights, fuzzy matching thresholds) using the same DAG loop | Successful optimization with measurable improvement, demonstrating target-agnostic architecture. **Post-M4** |

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
| **Framework-agnostic** — no LangChain, DSPy, or similar runtime dependency | Avoids vendor lock-in; users keep their existing architecture |
| **OpenAI-compatible LLM providers** — must work with Groq, OpenAI, Anthropic, or any provider exposing the OpenAI chat completions API | Supports the user's current provider (Groq with Llama 4 Maverick) and allows switching without code changes |
| **Langfuse required for MVP** — not an optional integration | Observability is core to the value proposition, not an afterthought |
| **File-based registry first** — no database dependency for MVP | Keeps deployment simple (Docker + filesystem); can migrate to SQLite or a database later behind the same interface |
| **Datasets are external** — evaluation datasets live in the consuming project, not in PromptPotter | PromptPotter is a tool, not a data store; datasets are domain-specific and versioned with their own project |
| **Docker-deployable** — minimal configuration to run | Single `docker-compose up` with environment variables for API keys |

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
