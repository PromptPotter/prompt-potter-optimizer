# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.2.0
**Date:** 2026-02-19
**Status:** Draft
**Depends on:** [Project Charter](project-charter.md), [PRD](prd.md)

---

## Table of Contents

- [System Context](#system-context)
- [What Exists vs. What Gets Built](#what-exists-vs-what-gets-built)
- [Component Architecture](#component-architecture)
- [The Optimization Loop](#the-optimization-loop)
- [Data Model](#data-model)
- [Architectural Decisions](#architectural-decisions)
- [Integration Points](#integration-points)
- [Validation Scenario](#validation-scenario)

---

## System Context

```
+-----------------------------------------------------------+
|                   Developer / CI Pipeline                  |
|           (REST API, notebooks, Streamlit)                 |
+--------------+----------------------------+---------------+
               |                            |
               | HTTP (REST)                | Direct (Python)
               v                            v
+-----------------------------------------------------------+
|                 PromptPotter Optimizer                     |
|                                                           |
|  +-------------+  +---------------+  +----------------+   |
|  | Optimization |  |   Workflow    |  |  Evaluation    |   |
|  | Orchestrator |  |   Engine      |  |  Framework     |   |
|  +------+------+  +-------+-------+  +--------+-------+   |
|         |                 |                    |           |
|         +--------+--------+--------------------+           |
|                  |                                         |
|                  v                                         |
|          +-------+-------+          +----------------+     |
|          |  LLM Client   |          |    Registry     |    |
|          +-------+-------+          |  (file-based)   |    |
|                  |                  +--------+---------+    |
+------------------+---------------------------+-------------+
                   |                           |
          +--------+--------+          +-------+--------+
          |  LLM Providers  |          |  File System   |
          | (Groq, OpenAI,  |          | (.promptpotter |
          |  Anthropic)     |          |  /campaigns/)  |
          +--------+--------+          +----------------+
                   |
          +--------+--------+
          |    Langfuse      |
          | (traces, scores) |
          +------------------+
```

- **Developers** send optimization requests via REST API or run notebooks interactively
- **CI pipelines** call the API programmatically for automated runs
- **LLM providers** handle inference through an OpenAI-compatible client
- **Langfuse** receives traces and scores for every trial
- **File system** stores campaign/trial records

---

## What Exists vs. What Gets Built

### Exists Today

| Component | Role |
|-----------|------|
| **Workflow engine** | DAG execution with topological sort and Langfuse tracing |
| **LLM node** | Multi-provider inference with template variables and JSON mode |
| **Web search node** | Mock placeholder |
| **Ranker node** | LLM-based candidate ranking |
| **Exact match evaluator** | String matching with normalization |
| **Criteria evaluator** | LLM-as-judge for non-deterministic outputs |
| **LLM client** | Multi-provider abstraction (Groq, OpenAI, Anthropic) |
| **Optimize router** | Placeholder — not yet a real implementation |

### Will Be Built

| Component | Milestone | PRD Req |
|-----------|-----------|---------|
| **PROMPT_STATE model** | M1 | P0.5 |
| **Test suite** (evaluators + workflow runner) | M1 | — |
| **CI pipeline** | M1 | — |
| **Analyzer node** (failure pattern analysis) | M2 | P0.2 |
| **Generator node** (candidate generation) | M2 | P0.3 |
| **Selector node** (best-candidate selection) | M2 | P0.4, P1.5 |
| **Optimization orchestrator** (full loop) | M2 | P0.4 |
| **Campaign registry** (file-based persistence) | M3 | P1.1 |
| **Langfuse score integration** | M3 | SC3 |
| **Human-in-the-loop gates** | M4 | P1.3 |
| **Real web search provider** | M4 | P1.4 |
| **Streamlit dashboard** | M4 | P2.4 |

---

## Component Architecture

The optimizer is built **on top of** the existing workflow engine. Each optimization step (evaluate, analyze, generate, select) is a node following the same typed-input/typed-output pattern.

| Component | Responsibility | Depends On |
|-----------|---------------|------------|
| **Optimization orchestrator** | Runs the evaluate-analyze-generate-select cycle; manages stopping criteria | Workflow engine, all optimizer nodes |
| **Workflow engine** | DAG execution with tracing; used for both user workflows and the optimization loop | LLM client, node registry |
| **Analyzer node** | Produces structured failure report from evaluation results | LLM client |
| **Generator node** | Produces N candidate configurations with rationales | LLM client |
| **Selector node** | Picks best candidate using configurable strategy (best-of-N, tournament, weighted) | None or LLM client |
| **Evaluation framework** | Scores configurations against datasets; logs to Langfuse | LLM client, Langfuse |
| **LLM client** | OpenAI-compatible inference with provider auto-detection | External providers |
| **Campaign registry** | Persists campaigns, trials, lineage, progress events | File system |

### Node Pattern

All nodes follow the same pattern:
- **Typed inputs and outputs** — structured models in, structured models out
- **Single responsibility** — one node does one thing
- **Composable** — wire together in workflows or call individually
- **Testable** — unit test with mock inputs

---

## The Optimization Loop

```
          +-----------------------+
          |   Initial config      |
          |   + dataset           |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   EVALUATE            |
          |   Score against       |
          |   dataset             |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   ANALYZE             |
          |   Identify failure    |
          |   patterns            |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   GENERATE            |
          |   Produce N           |
          |   candidates          |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   EVALUATE            |
          |   Score candidates    |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   SELECT best         |
          +-----------+-----------+
                      |
                      v
          +-----------+-----------+
          |   STOP?               |
          |   - Target score?     +--yes--> Return best
          |   - Max iterations?   +--yes--> Return best
          |   - No improvement?   +--yes--> Return best
          +-----------+-----------+
                      |
                      no --> loop back to EVALUATE
```

**Step-to-PRD mapping:**

| Step | PRD | Summary |
|------|-----|---------|
| Evaluate | P0.1 | Run config against dataset, return scores, log to Langfuse |
| Analyze | P0.2 | Categorize failures with cited examples |
| Generate | P0.3 | Produce 2-5 candidates with rationales |
| Select | P0.4, P1.5 | Apply selection strategy (best-of-N, tournament, weighted) |
| Loop | P0.4 | Manage iterations, stopping criteria, improvement trajectory |

**Workflow-based optimization (P1.2):** For multi-step pipelines like TermNorm, the optimizer targets one step — the full workflow runs end-to-end for scoring, but only the target step's parameters change between iterations.

---

## Data Model

Three models are central. See [Charter Key Terms](project-charter.md) for definitions and [PRD P0.5](prd.md) for PROMPT_STATE acceptance criteria.

- **PROMPT_STATE** — versioned snapshot of prompt text + few-shot examples + parameters dictionary (temperature, retrieval count, thresholds, etc.). Immutable; each trial creates a new one.
- **Campaign** — one optimization run: config, initial state, status, best trial reference.
- **Trial** — one iteration: PROMPT_STATE snapshot, scores, analysis, rationale, parent reference.

### File Layout

```
.promptpotter/
+-- campaigns/
    +-- {campaign-id}/
        +-- metadata.json      Campaign config + status
        +-- lineage.json       Trial parent-child tree
        +-- progress.jsonl     Event stream
        +-- trials/
            +-- {trial-id}/
                +-- metadata.json  Trial details + scores
                +-- results.jsonl  Per-item results
```

JSON for metadata, JSONL for results (OpenAI Evals compatible). See [Registry Design](../registry-design.md) for full spec.

---

## Architectural Decisions

| Decision | Why | Tradeoff |
|----------|-----|----------|
| **No framework dependency** (no DSPy, LangChain, TextGrad) | Avoid lock-in; borrow ideas from [literature review](../literature-review.md), build on own abstractions | More initial work, but strategies are swappable |
| **Optimizer built on workflow engine** | Reuse existing DAG execution, tracing, error handling | Optimization steps are nodes — testable, composable, traceable for free |
| **File-based registry first** | No database for MVP; JSON/JSONL is human-readable and OpenAI Evals compatible | Limited query capability; acceptable at single-user scale. Swappable later behind interface. |
| **PROMPT_STATE as first-class model** | Track all tunable params (not just prompt text); enable structured diffs | Open-ended parameters dict means no schema enforcement on values |
| **LLM-as-judge for evaluation** | Non-deterministic outputs need criteria-based scoring beyond exact match | Judge quality depends on judge prompt; cost scales with dataset x iterations x candidates |

---

## Integration Points

| System | Direction | Protocol | Status |
|--------|-----------|----------|--------|
| **LLM providers** (Groq, OpenAI, Anthropic) | PromptPotter sends inference requests | OpenAI chat completions API | Exists |
| **Langfuse** | PromptPotter sends traces + scores | Langfuse Python SDK | Exists |
| **File system** | Read/write campaigns, trials, lineage | JSON/JSONL | Planned (M3) |
| **Streamlit** | Streamlit calls the API | HTTP | Exists (prototype) |
| **Consuming projects** (e.g., TermNorm) | PromptPotter loads external datasets | File path or URL | Exists (loader) |
| **TermNorm prompt registry** | PromptPotter reads current prompts from `logs/prompts/{family}/{version}/prompt.txt` and writes optimized versions back as new version numbers (v2, v3, ...) | File system (TermNorm's `PromptRegistry` with `{{variable}}` templates) | Planned (M4) |
| **MCP clients** (e.g., Claude Code) | Clients invoke optimization tools | MCP | Planned (P2.3) |

---

## Validation Scenario

The pinnacle validation for PromptPotter is the **TermNorm pipeline variant comparison** (SC5). This section describes how the architecture supports the concrete validation that proves the system works.

### TermNorm Pipeline

TermNorm is a terminology normalization system (primary domain: LCA -- Life Cycle Assessment) that matches free-form text to standardized database identifiers. Its pipeline has three stages, two of which are LLM calls with prompts managed by TermNorm's versioned prompt registry:

| Stage | Component | Type | Prompt Family |
|-------|-----------|------|---------------|
| 1 | Web scrape | External | -- |
| 2 | Entity profiling (LLM1) | LLM call | `entity_profiling` (vars: `query`, `format_string`, `combined_text`) |
| 3 | Table Reranker | Non-LLM | -- (token/string matching, no semantic understanding) |
| 4 | Semantic reranking (LLM2) | LLM call | `llm_ranking` (vars: `core_concept`, `entity_profile_json`, `matches`) |

### Variant Comparison

The two pipeline variants under comparison:

- **Variant A**: Web scrape --> LLM1 (`entity_profiling` v1) --> Table Reranker --> done (skip LLM2)
- **Variant B**: Web scrape --> LLM1 (`entity_profiling` v1) --> Table Reranker --> LLM2 (`llm_ranking`) --> done

**Research question:** Does LLM2 semantic reranking add enough accuracy over the table reranker to justify the extra LLM cost and latency?

### Optimization Strategy

Variant A serves as the **fixed baseline** -- `entity_profiling` v1 plus the table reranker, with no LLM2 call. Its score on the evaluation dataset is the bar that Variant B must clear.

PromptPotter's job is to **optimize the `llm_ranking` prompt** (generating v2, v3, ...) so that Variant B beats Variant A. The `entity_profiling` prompt stays at v1 throughout; only the `llm_ranking` prompt changes between trials. This is the concrete test of whether an optimized LLM2 call adds enough value to justify its cost.

The optimization loop:
1. Evaluate Variant A (once) to establish the baseline score
2. Evaluate Variant B with `llm_ranking` v1 as the initial candidate
3. Run the optimize cycle on `llm_ranking`: analyze failures, generate candidate prompts (v2, v3, ...), evaluate each, select best
4. Compare the best Variant B score against the Variant A baseline
5. Produce a clear recommendation: is the optimized LLM2 call worth it?

Optimized prompt versions are written back to TermNorm's prompt registry as new version numbers.

Development and testing uses the **BC5CDR 500-term subset** as the primary benchmark (well-known ground truth, scientifically reproducible, suitable for archival publication). LCA dataset validation follows when deploying to real-world use. MedMentions 500-term subset serves as an additional biomedical benchmark.

### Decision Points

| # | Decision | Method | Milestone |
|---|----------|--------|-----------|
| 1 | LLM2 on/off -- can an optimized `llm_ranking` prompt make Variant B beat Variant A? | Optimize `llm_ranking` prompt, compare best Variant B score against Variant A baseline, results persisted and traceable in Langfuse | M4 |
| 2 | How many websites to scrape for entity profiling? | Ablation study varying scrape count, measuring quality vs. cost/latency | Post-M4 |
