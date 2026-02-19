# Project Charter: PromptPotter Optimizer

**Version:** 0.2.0
**Date:** 2026-02-19
**Status:** Draft

---

## Key Terms

| Term | Definition |
|------|-----------|
| **Campaign** | A single optimization run from start to finish. A campaign starts with a baseline configuration, runs one or more trials, and ends with a recommended best configuration. |
| **Trial** | One iteration within a campaign. Each trial tests a candidate configuration against the evaluation dataset and records the results. |
| **PROMPT_STATE** | The tracked, versioned snapshot of a prompt and all its tunable parameters (prompt text, few-shot examples, temperature, retrieval settings, etc.). Every trial produces a new PROMPT_STATE. |
| **Evaluation dataset** | A labeled set of input/expected-output pairs used to score how well a configuration performs. Datasets are owned by the consuming project, not by PromptPotter. |

---

## Problem Statement

Tuning LLM-powered systems today is manual and untracked. Developers adjust prompts, few-shot examples, temperature, retrieval counts, and other parameters through trial and error. There is no systematic record of what changed, why it changed, or whether it actually helped. When something works, the reasoning is lost. When something breaks, there is no way to roll back to a known-good state.

Existing optimization frameworks (DSPy, TextGrad, EvoPrompt) each solve parts of this problem but require adopting their abstractions wholesale. A team using one framework cannot easily switch to another or combine techniques from several.

### Motivating Use Case: TermNorm

**TermNorm** is a biomedical entity normalization system that maps free-text medical mentions (e.g., "SJS," "heart attack") to standardized concept identifiers in medical ontologies. Its pipeline has multiple tunable parameters: the system prompt, few-shot examples, candidate retrieval count, similarity thresholds, and temperature.

The first real use case for PromptPotter is optimizing TermNorm's parameters against hard evaluation datasets (500-term subsets from the **MedMentions** and **BC5CDR** biomedical benchmarks). These datasets expose failure modes — rare diseases, ambiguous abbreviations, overlapping concept boundaries — that manual tuning struggles to address systematically.

---

## Vision

An **API-first, framework-agnostic parameter optimization service** that automates the analyze-generate-evaluate loop for any LLM-powered system. PromptPotter optimizes all tunable non-code parameters — not just prompt strings, but also few-shot examples, thresholds, retrieval counts, temperature, and other configuration that affects LLM behavior.

The system keeps humans in control of strategy and priorities through decision gates, while automating the repetitive work of generating candidates, running evaluations, and tracking what improved.

**Core principles:**

- **Framework-agnostic** — no runtime dependency on LangChain, DSPy, or any other framework. Borrows ideas from the research literature, builds on its own abstractions.
- **Observable by default** — every optimization run is traced in **Langfuse** (an open-source LLM observability platform) with structured scores, parent-child run hierarchy, and full lineage.
- **Dual-mode delivery** — available as both a FastAPI REST service for automation and a JupyterLab environment for interactive exploration.

---

## Scope

### In Scope

- **Parameter optimization** — iterative improvement of prompts, few-shot examples, temperature, retrieval counts, thresholds, and other non-code configuration through automated failure analysis, candidate generation, and evaluation
- **Workflow-based optimization** — optimization of individual steps within multi-step pipelines (e.g., retrieval followed by ranking followed by classification), using the existing workflow engine
- **API-first delivery** — FastAPI REST service with structured Pydantic input/output contracts
- **Human-in-the-loop gates** — decision points where developers review and approve candidates before promotion
- **Evaluation framework** — automated scoring against labeled datasets with multiple evaluator strategies (exact match, LLM-as-judge, custom)
- **Langfuse integration** — tracing, scoring, and lineage tracking for every campaign and trial (required for MVP, not optional)
- **Campaign and trial tracking** — persistent registry of optimization runs with JSONL export, following the parent-child run hierarchy pattern

### Out of Scope

- **Fine-tuning or model training** — PromptPotter optimizes parameters passed to LLMs, it does not modify model weights
- **SaaS hosting or multi-tenant deployment** — single-user/single-team tool
- **Agent training or reinforcement learning** — no reward-model training or policy gradient methods
- **Production prompt serving** — PromptPotter finds better configurations, it does not serve them at inference time
- **GUI/dashboard beyond prototypes** — Streamlit apps for development use, not a production dashboard
- **Dataset hosting** — evaluation datasets live in the consuming project's repository (e.g., the TermNorm repo), not in PromptPotter

---

## Success Criteria

| # | Criterion | Measurement | Target |
|---|-----------|-------------|--------|
| 1 | **Measurable improvement** | Score delta between initial and best configuration on the evaluation dataset | **10%+ improvement** on at least one user-defined metric (e.g., accuracy, F1) within a single campaign |
| 2 | **Reproducibility** | Given identical inputs (initial configuration, dataset, campaign settings), the optimizer produces a consistent improvement trajectory | Scores for the same trial vary by **less than 5%** across repeated runs (accounting for LLM non-determinism with temperature 0) |
| 3 | **Langfuse observability** | Optimization campaigns appear in Langfuse with correct parent-child trace hierarchy and per-trial scores | **100%** of trials have associated Langfuse traces and scores |
| 4 | **Time to first optimization** | A developer with API keys configured can run their first optimization campaign | **Under 15 minutes** from `pip install` to completed campaign, using the API or a notebook |
| 5 | **TermNorm validation** | Run a full campaign against a TermNorm hard-test dataset | Produces a configuration that **outperforms the hand-tuned baseline** on the MedMentions or BC5CDR 500-term subset |

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
