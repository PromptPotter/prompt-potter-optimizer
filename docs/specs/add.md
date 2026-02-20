# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.3.0
**Date:** 2026-02-20
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

**Multi-client architecture:** PromptPotter is consumed by multiple client types — CLI / Python scripts, Jupyter notebooks, and lightweight JS frontends. All share the same REST API; no client gets special treatment. Every API response is structured JSON so any client can render it.

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
| **Test suite** (evaluators + workflow runner) | M1 | -- |
| **CI pipeline** | M1 | -- |
| **Initialization node** (context analysis + structured prompt component extraction) | M2 | P0.3 |
| **Grow/Filter node** (prompt state enrichment) | M2 | P0.3 |
| **Analysis + Evaluation node** (scoring + failure analysis + next_action decision) | M2 | P0.1, P0.2 |
| **Feedback router** (Switch: generate / refine context / modify plan) | M2 | P0.4 |
| **Optimization orchestrator** (DAG loop with counter-based stop condition) | M2 | P0.4 |
| **Campaign registry** (file-based persistence) | M3 | P1.1 |
| **Langfuse score integration** | M3 | SC3 |
| **Cycling mode** (enable feedback paths for iterative refinement) | Post-M2 | P0.4, P2.1 |
| **Human-in-the-loop gates** | M4 | P1.3 |
| **Real web search provider** | M4 | P1.4 |
| **Streamlit dashboard** | M4 | P2.4 |

---

## Component Architecture

The optimizer implements a **DAG-based iterative workflow** derived from the reference design (`docs/design/optimization-workflow.n8n.json`). Rather than a rigid linear pipeline, the optimization loop is a directed graph with conditional feedback paths that allow the system to adapt its strategy (generate new variants, refine context, or modify the plan) based on evaluation results.

| Component | Responsibility | Depends On |
|-----------|---------------|------------|
| **Optimization orchestrator** | Manages the DAG-based optimization loop: initialization, iteration cycling, stop condition checking, and feedback routing | Workflow engine, all optimizer nodes |
| **Workflow engine** | DAG execution with tracing; used for both user workflows and the optimization loop | LLM client, node registry |
| **Initialization node** | Loads eval dataset + context; AI agent analyzes context and produces structured prompt components (task_description, base_instruction, answer_format) | LLM client |
| **Grow/Filter node** | Enriches the current prompt state — expands, refines, or constrains prompt components based on the current plan | LLM client |
| **Analysis + Evaluation node** | Evaluates the current prompt state against the dataset, produces scores, and decides the next action (generate, refine context, or modify plan) | LLM client, Evaluation framework |
| **Feedback router (Switch)** | Routes to one of three feedback paths based on `next_action` from the analysis report | None |
| **Context refinement node** | Updates optimization context with critiques and applied metrics, then updates the plan | LLM client |
| **Plan update node** | Modifies the optimization plan based on feedback from analysis | LLM client |
| **Evaluation framework** | Scores configurations against datasets; logs to Langfuse | LLM client, Langfuse |
| **LLM client** | OpenAI-compatible inference with provider auto-detection (Groq with Llama 4 Maverick as default) | External providers |
| **Campaign registry** | Persists campaigns, trials, lineage, progress events | File system |

### Node Pattern

All nodes follow the same pattern:
- **Typed inputs and outputs** — structured Pydantic models in, structured models out
- **Single responsibility** — one node does one thing
- **Composable** — wire together in the DAG or call individually
- **Testable** — unit test with mock inputs
- **Stateless** — prompt state flows through the DAG; nodes do not hold state

---

## The Optimization Loop

The optimization loop is a **DAG-based iterative workflow** with an initialization phase followed by a main loop with conditional feedback paths. The design is derived from the reference n8n workflow (`docs/design/optimization-workflow.n8n.json`).

### Initialization Phase

```
  +----------------+     +----------------+     +------------------+
  | context_input  | --> | Load eval      | --> | AI Agent:        |
  | (user context) |     | dataset        |     | Analyze context, |
  |                |     | (training/test)|     | produce prompt   |
  +----------------+     +----------------+     | components       |
                                                +--------+---------+
                                                         |
                                              Structured output:
                                              - task_description
                                              - base_instruction
                                              - answer_format
                                                         |
                                                         v
                                                   [main_data]
```

The AI Agent uses **structured output parsing** (Groq + Llama 4 Maverick) to transform raw context into prompt components that seed the main loop.

### Main Loop

```
                +-----------------------------------------------+
                |                                               |
                v                                               |
  +-------------+-----------+                                   |
  |       main_data         |  Prompt state:                    |
  |  persona, task_intent,  |  - persona                        |
  |  problem_description,   |  - task_intent                    |
  |  instruction,           |  - problem_description            |
  |  thinking_style, plan   |  - instruction                    |
  |  + counter              |  - thinking_style                 |
  +-------------+-----------+  - plan                           |
                |                                               |
                v                                               |
  +-------------+-----------+                                   |
  |       Grow/Filter       |  Enrich prompt state              |
  +-------------+-----------+                                   |
                |                                               |
                v                                               |
  +-------------+-----------+                                   |
  | Analysis + Evaluation   |  Evaluate, produce scores,        |
  |                         |  decide next_action               |
  +-------------+-----------+                                   |
                |                                               |
                v                                               |
  +-------------+-----------+                                   |
  |     count_plus_one      |  Increment iteration counter      |
  +-------------+-----------+                                   |
                |                                               |
                v                                               |
  +-------------+-----------+                                   |
  |   counter >= N ?        |                                   |
  +---yes---+---no----------+                                   |
      |               |                                         |
      v               v                                         |
  [output]    +-------+---------+                               |
              |     Switch      |  Route on next_action:        |
              +--+------+---+---+                               |
                 |      |   |                                   |
     "generate"  | "refine  | "modify                           |
                 | context" |  plan"                             |
                 |      |   |                                   |
                 |      v   +---+                               |
                 |  [updated_   |                               |
                 |   context]   v                               |
                 |      |   [updated_                           |
                 |      +-->  plan]                             |
                 |             |                                |
                 +------+------+                                |
                        |                                       |
                        +---------------------------------------+
```

### Feedback Paths

The Switch node routes based on the `next_action` field from the analysis report:

| next_action | Path | Effect |
|-------------|------|--------|
| **"generate"** | Loop directly back to `main_data` | Create new prompt variants from scratch — used when the current approach is fundamentally off |
| **"refine context"** | `updated_context` --> `updated_plan` --> `main_data` | Update optimization context with critiques and applied metrics, then revise the plan — used when the approach is right but needs fine-tuning |
| **"modify plan"** | `updated_plan` --> `main_data` | Change the optimization strategy/plan without changing context — used when the evaluation suggests a different approach |

### Phased Rollout

The DAG supports two operational modes:

**Phase 1 — Linear Mode (0 cycles):**
- Initialization --> Grow/Filter --> Analysis + Evaluation --> Output
- No looping. The counter starts at 0 and the stop condition (counter >= 1) triggers immediately after the first pass.
- Run N independent times with **breadth** (multiple parallel linear runs) instead of depth (iterative cycling).
- This is the MVP implementation for M2.

**Phase 2 — Cycling Mode (1+ cycles):**
- Full DAG with the Switch feedback paths enabled.
- Counter threshold set to N (configurable, default 5).
- Each cycle refines the prompt state based on the feedback path chosen by the analysis.
- This is the target for post-M2 enhancement.

### Step-to-PRD Mapping

| DAG Node | PRD | Summary |
|----------|-----|---------|
| Initialization (AI Agent) | P0.3 | Analyze context, produce structured prompt components |
| main_data | P0.5 | Prompt state snapshot (maps to PromptState model) |
| Grow/Filter | P0.3 | Enrich and expand prompt candidates |
| Analysis + Evaluation | P0.1, P0.2 | Score against dataset, analyze failures, decide next action |
| Switch (feedback router) | P0.4 | Route to appropriate feedback path |
| count_plus_one + stop condition | P0.4 | Manage iteration counter and stopping criteria |
| updated_context | P2.1 | Context refinement with critiques and metrics (reflection-like) |
| updated_plan | P0.4 | Adaptive strategy modification |

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
| **DAG-based optimization loop with conditional feedback** | Reference n8n workflow proves the pattern works; three feedback paths (generate, refine context, modify plan) let the system adapt its strategy per iteration instead of rigidly repeating the same cycle | More complex than a linear loop; requires routing logic and state management across feedback paths |
| **Phased rollout: linear first, cycling later** | Phase 1 (linear mode, 0 cycles) is simpler to build and test; breadth-first (N parallel runs) provides value without the complexity of feedback cycling | Phase 1 may miss optimization opportunities that require iterative refinement; Phase 2 adds that capability |
| **Structured prompt state as DAG data flow** | Prompt components (persona, task_intent, problem_description, instruction, thinking_style, plan) flow through the DAG as structured data, not opaque strings; enables targeted modification by each node | More fields to maintain; adding a new prompt component requires updating the state schema |
| **AI Agent initialization with structured output** | LLM analyzes raw context and produces typed prompt components (task_description, base_instruction, answer_format) via structured output parsing; avoids manual prompt decomposition | Quality of initialization depends on the LLM's ability to parse arbitrary context; may need domain-specific examples |
| **Optimizer built on workflow engine** | Reuse existing DAG execution, tracing, error handling | Optimization steps are nodes -- testable, composable, traceable for free |
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

### Ablation Comparison (Generalized Pattern)

The Variant A vs B comparison above is an instance of a general pattern: **pipeline ablation**. Any linear pipeline can be evaluated with a node removed to measure its marginal value. The system accepts prior results, replays with a node skipped, and produces a statistical comparison with p-values (McNemar's test for accuracy, Wilcoxon signed-rank for latency).

Pipeline nodes are typed (`LLMGeneration`, `DeterministicFunction`, `WebSearch`) with visible input/output schemas. Clients auto-detect node capabilities and surface relevant parameters (prompt text, temperature, threshold, etc.). This pattern becomes a self-service feature when PromptPotter is deployed as a web service — users upload experiment data, select which component to remove, and see the comparison.

### Decision Points

| # | Decision | Method | Milestone |
|---|----------|--------|-----------|
| 1 | LLM2 on/off -- can an optimized `llm_ranking` prompt make Variant B beat Variant A? | Optimize `llm_ranking` prompt, compare best Variant B score against Variant A baseline, results persisted and traceable in Langfuse | M4 |
| 2 | How many websites to scrape for entity profiling? | Ablation study varying scrape count, measuring quality vs. cost/latency | Post-M4 |
