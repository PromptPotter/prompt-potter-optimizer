# Architecture Design Document: PromptPotter Optimizer

**Version:** 0.4.0
**Date:** 2026-02-20
**Status:** Draft
**Depends on:** [Project Charter v0.4.0](project-charter.md), [PRD v0.4.0](prd.md)

---

## Table of Contents

- [System Context](#system-context)
- [What Exists vs. What Gets Built](#what-exists-vs-what-gets-built)
- [Component Architecture](#component-architecture)
- [The Optimization Loop](#the-optimization-loop)
- [Data Model](#data-model)
- [Architectural Decisions](#architectural-decisions)
- [Deployment Model & Access Control](#deployment-model--access-control)
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
          |  Anthropic)     |          |  /projects/)   |  ← exists (M1)
          +--------+--------+          |  /campaigns/)  |  ← planned (M3)
                                       +----------------+
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

| Component | Role | Since |
|-----------|------|-------|
| **Workflow engine** | DAG execution with topological sort and Langfuse tracing | Pre-M1 |
| **LLM node** | Multi-provider inference with template variables and JSON mode | Pre-M1 |
| **Web search node** | Mock placeholder | Pre-M1 |
| **Ranker node** | LLM-based candidate ranking | Pre-M1 |
| **Exact match evaluator** | String matching with normalization | Pre-M1 |
| **Criteria evaluator** | LLM-as-judge for non-deterministic outputs | Pre-M1 |
| **LLM client** | Multi-provider abstraction (Groq, OpenAI, Anthropic) | Pre-M1 |
| **Optimize router** | Placeholder — not yet a real implementation | Pre-M1 (placeholder; real implementation in M2 via 2.5) |
| **PromptState model** | Immutable, versioned prompt snapshot with `derive()` and structured diff | M1 |
| **ProjectStore** | File-based backend data storage (`.promptpotter/projects/`) with incremental writes | M1 |
| **Backends router** | `/backends/*` — register, sync, execute, compare | M1 |
| **Backend client** | HTTP client for external backend APIs (TermNorm) | M1 |
| **Comparison service** | Statistical comparison with McNemar's test, Wilcoxon signed-rank, hit@k, MRR | M1 |
| **Test suite** | Evaluators, workflow runner, PromptState, incremental writes, API endpoints | M1 |
| **CI pipeline** | GitHub Actions: ruff lint + pytest on push/PR | M1 |
| **Pipeline parameter passthrough** | Controllable knobs for all TermNorm pipeline stages (11 params: search, profiling, ranking, scoring) forwarded via `/matches` payload, echoed in response and training record | M1 |

### Will Be Built

| Component | Milestone | PRD Req |
|-----------|-----------|---------|
| **Initialization node** (context analysis + structured prompt component extraction) | M2 | P0.3 |
| **Grow/Filter node** (prompt state enrichment) | M2 | P0.3 |
| **Analysis + Evaluation node** (scoring + failure analysis + next_action decision) | M2 | P0.1, P0.2 |
| **Feedback router** (Switch: generate / refine context / modify plan) | M2 | P0.4 |
| **Optimization orchestrator** (DAG loop with counter-based stop condition) | M2 | P0.4 |
| **Campaign registry** (file-based persistence) | M3 | P1.1 |
| **Langfuse score integration** | M3 | SC3 |
| **HITL Campaign Notebook** (interactive optimization with config editing, diagnostics, phrase fragment suggestions) | M2 | P1.3, P0.3 |
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
| **Initialization node** | Loads eval dataset + context; AI agent analyzes context and produces structured prompt components (task_intent, instruction, answer_format) | LLM client |
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

### Target Abstraction

The DAG-based optimization loop is parameterized by a **state schema** — the Pydantic model that defines the tunable parameters flowing through the graph. M2-M4 implement the concrete `PromptState` schema (persona, task_intent, problem_description, instruction, thinking_style, answer_format). The architecture is designed so that the core loop (initialize → grow/filter → analyze+evaluate → feedback) operates on any state schema without modification.

Post-M4, new target types (scoring function weights, fuzzy matching thresholds, retrieval query templates, GA parameters) can be added by:
1. Defining a new state schema (Pydantic model)
2. Implementing target-specific initialization and grow/filter logic
3. Reusing the existing evaluation framework and feedback routing

The abstraction boundary is **designed but not implemented** until post-M4. M2-M4 build and validate the concrete prompt case; generalization follows once the loop is proven.

---

## The Optimization Loop

The optimization loop is a **DAG-based iterative workflow** with an initialization phase followed by a main loop with conditional feedback paths. The design is derived from the reference n8n workflow (`docs/design/optimization-workflow.n8n.json`).

### 3-Layer Optimization Architecture

The three feedback paths form **nested optimization layers** with escalation.
The innermost layer runs first; outer layers activate only when inner ones stall:

| Layer | Feedback Path | PromptState Fields | Escalation Trigger |
|-------|--------------|--------------------|--------------------|
| **1 - Generate** (innermost) | `"generate"` → main_data | persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples | Default — always runs |
| **2 - Refine Context** (middle) | `"refine context"` → updated_context → updated_plan → main_data | context, parameters | Layer 1 shows no improvement |
| **3 - Modify Plan** (outermost) | `"modify plan"` → updated_plan → main_data | plan | Layer 2 shows no improvement |

**Layer 1 mutation strategy:** The primary mutation is **substitution** — replacing a building block with a new version. Secondary mutations (elimination, addition) are possible but not preferred. Layer 1 fields are extensible: users can register additional building blocks beyond the 6 defaults.

Layer 3 ships with `OptimizationDefaults` — sensible strategy parameters that should
rarely need changing. The escalation design means most optimization runs complete
using only Layer 1 (generating divergent variants in one pass).

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
                                              - task_intent
                                              - instruction
                                              - answer_format
                                                         |
                                                         v
                                                   [main_data]
```

**Campaign inputs and context lifecycle:** A campaign starts with three required inputs: `training_data`, `test_data`, and `context` (raw user-provided domain description). During initialization, the AI Agent refines the raw context according to the Plan to produce the initial PromptState. This refined context lives on the PromptState (Layer 2) and can be further refined during escalation via the "refine context" feedback path.

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
| **"generate"** | Loop directly back to `main_data` | **Layer 1**: Create new prompt variants — the innermost loop, runs every pass |
| **"refine context"** | `updated_context` --> `updated_plan` --> `main_data` | **Layer 2**: Update context with critiques and metrics, then revise plan — escalation when Layer 1 stalls |
| **"modify plan"** | `updated_plan` --> `main_data` | **Layer 3**: Change optimization strategy — last resort escalation when Layer 2 stalls |

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

- **PROMPT_STATE** — versioned snapshot organized into three optimization layers: **Layer 1 (Generate)** structured prompt components (persona, task_intent, problem_description, instruction, thinking_style, answer_format, few_shot_examples), **Layer 2 (Refine Context)** optimization context and hypervariables (context, parameters), and **Layer 3 (Modify Plan)** optimization strategy (plan). Immutable; each trial creates a new one. Includes `render()` to assemble Layer 1 fields into a prompt string.
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
| **Structured prompt state as DAG data flow** | Prompt components organized into 3 optimization layers (Generate, Refine Context, Modify Plan) flow through the DAG as structured data, not opaque strings; `OptimizationDefaults` provides sensible Layer 3 strategy parameters; enables targeted modification by each node at the appropriate layer | More fields to maintain; adding a new prompt component requires updating the state schema and `LAYER_FIELDS` mapping |
| **AI Agent initialization with structured output** | LLM analyzes raw context and produces typed prompt components (task_intent, instruction, answer_format) via structured output parsing; avoids manual prompt decomposition | Quality of initialization depends on the LLM's ability to parse arbitrary context; may need domain-specific examples |
| **Optimizer built on workflow engine** | Reuse existing DAG execution, tracing, error handling | Optimization steps are nodes -- testable, composable, traceable for free |
| **File-based registry first** | No database for MVP; JSON/JSONL is human-readable and OpenAI Evals compatible | Limited query capability; acceptable at single-user scale. Swappable later behind interface. |
| **PROMPT_STATE as first-class model** | Track all tunable params (not just prompt text); enable structured diffs | Open-ended parameters dict means no schema enforcement on values |
| **LLM-as-judge for evaluation** | Non-deterministic outputs need criteria-based scoring beyond exact match | Judge quality depends on judge prompt; cost scales with dataset x iterations x candidates |
| **Target-agnostic optimization loop** | DAG operates on a pluggable state schema so the same loop can optimize prompts, schemas, scoring functions, fuzzy matchers, and other parameter types post-M4 | Adds abstraction layer between loop and state; mitigated by building the concrete prompt case first (M2-M4) and generalizing only after the loop is proven |
| **Stateless API with pluggable auth** | Public deployment requires credential-based access and data isolation; designing the API stateless from day one avoids costly rewrites | Auth middleware adds latency; mitigated by keeping it as a thin middleware layer that can be toggled off for local use |

---

## Deployment Model & Access Control

PromptPotter is designed for a staged deployment progression, from a local development tool to a publicly accessible optimization service. This section describes the deployment model and the layered access control that enables safe multi-tenant operation.

### Deployment Progression

| Stage | Timeframe | Auth | Users | Hosting |
|-------|-----------|------|-------|---------|
| **Local** | M1–M4 | None | Single user | `localhost` only |
| **Private server** | Post-M4 | API key authentication | Team-level access | Self-hosted |
| **Public service** | Post-M4 | Multi-tenant with user accounts | Any developer | Cloud-hosted with rate limiting |

During local development (M1–M4), no authentication is required. The API runs on `localhost` and serves a single user. Private server deployment adds API key authentication for team-level access within an organization. Public service deployment introduces full multi-tenancy with user accounts, role-based access, and rate limiting.

### Layered Access Model

When deployed publicly, the API enforces three access tiers:

| Tier | Access | Scope |
|------|--------|-------|
| **Anonymous** | Health and readiness endpoints only (`/health`, `/ready`) | No data access |
| **Authenticated (API key)** | Full API access | Scoped to own data — campaigns, backends, executions, project store |
| **Admin** | User management, global configuration, system metrics | Cross-tenant visibility |

Each tier is additive — authenticated users have anonymous access, and admins have authenticated access.

### Data Isolation

Each authenticated user's data is fully isolated:

- **Campaigns** — a user can only list, read, and modify their own campaigns and trials
- **Backends** — backend connections and synced experiments are per-user
- **Project store** — the `.promptpotter/projects/` directory is scoped per user; no cross-user data access
- **Executions** — replay results belong to the user who initiated them

Cross-user data access is not permitted at any tier except admin.

### Design Constraints for Today

The API is already designed with public deployment in mind:

- **Stateless request handling** — no server-side session state between requests. Each request carries all context needed for processing. This is the foundation for horizontal scaling and multi-tenancy.
- **Pluggable auth middleware** — authentication is designed as a thin middleware layer that is disabled for local use and enabled on deployment. No endpoint logic changes are required to add auth.
- **Versioned API contracts** — all endpoints use `/api/v1/` prefixing, ensuring stable contracts for external consumers across deployment stages.

---

## Integration Points

| System | Direction | Protocol | Status |
|--------|-----------|----------|--------|
| **LLM providers** (Groq, OpenAI, Anthropic) | PromptPotter sends inference requests | OpenAI chat completions API | Exists |
| **Langfuse** | PromptPotter sends traces + scores | Langfuse Python SDK | Exists |
| **ProjectStore** (backend data) | Read/write backend connections, synced experiments, executions | JSON files in `.promptpotter/projects/` | Exists (M1) |
| **File system** (campaigns) | Read/write campaigns, trials, lineage | JSON/JSONL in `.promptpotter/campaigns/` | Planned (M3) |
| **Streamlit** | Streamlit calls the API | HTTP | Exists (prototype) |
| **Consuming projects** (e.g., TermNorm) | PromptPotter loads external datasets | File path or URL | Exists (loader) |
| **TermNorm backend API** | PromptPotter syncs experiments, replays pipelines, and forwards pipeline parameter overrides (search depth, LLM temperatures, candidate limits, score weights) | HTTP REST | Exists (M1) |
| **TermNorm prompt registry** | PromptPotter reads current prompts from `logs/prompts/{family}/{version}/prompt.txt` and writes optimized versions back as new version numbers (v2, v3, ...) | File system (TermNorm's `PromptRegistry` with `{{variable}}` templates) | Planned (M4) |
| **MCP clients** (e.g., Claude Code) | Clients invoke optimization tools | MCP | Planned (P2.3) |
| **Public API gateway** | External consumers access PromptPotter as a hosted service | HTTP REST with API key auth | Post-M4 |

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

All pipeline stages expose configurable parameters via the `/matches` payload (see [TermNorm connector docs](../connectors/termnorm.md#pipeline-parameter-overrides) for the full catalog). This enables human-in-the-loop experimentation (manually varying knobs in the notebook) and automated optimization (the DAG loop systematically exploring the parameter space).

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
| 2 | How many websites to scrape for entity profiling? | Ablation study varying scrape count via `pipeline_params` passthrough (infrastructure exists); measuring quality vs. cost/latency | M4 |
