# Product Requirements Document: PromptPotter Optimizer

**Version:** 0.6.0
**Date:** 2026-02-23
**Status:** Active
**Depends on:** [Project Charter v0.6.0](project-charter.md)

---

## Requirements Summary

| ID | Name | Priority | Status | Description |
|----|------|----------|--------|-------------|
| P0.1 | Backend Evaluation on Dataset | P0 | Implemented | Evaluate a prompt against a labeled dataset via the backend's `/matches` endpoint with content-addressed deduplication and crash recovery |
| P0.2 | Failure Analysis | P0 | Implemented | Analyze evaluation results to categorize failure patterns and generate actionable suggestions |
| P0.3 | Candidate Generation | P0 | Implemented | Generate improved prompt configurations via LLM meta-prompt informed by failure analysis |
| P0.4 | Optimization Loop | P0 | Implemented | Semi-automatic or manual optimization rounds with patience-based stopping |
| P0.5 | PROMPT_STATE Tracking | P0 | Implemented | Immutable, versioned prompt snapshots with structured metadata and lineage |
| P0.6 | Grid Search Exploration | P0 | Implemented | Systematic exploration of Layer 1 prompt field variants with sampling, deduplication, and LLM analysis |
| P1.1 | Workflow Engine Migration | P1 | Implemented (M3) | Optimizer nodes (Init, GrowFilter, AnalysisEval) running in feedback cycle |
| P1.2 | Iterative Feedback Cycling | P1 | Implemented (M3) | 3-path routing with patience-based stopping and per-query progress callbacks |
| P1.3 | Campaign Registry | P1 | Implemented (M3) | CampaignStore for campaign/trial persistence |
| P1.4 | Full Langfuse Integration | P1 | Implemented (M3) | Per-trial tracing with score attachment and campaign-level grouping |
| P1.8 | Data Loop (Eval Reuse) | P1 | In Progress (M3) | All eval data from any optimization path feeds back into sensitivity scan for fresh starting points |
| P1.5 | Discovery Protocol | P1 | Implemented | Discover backend pipeline parameters via `GET /pipeline` for dynamic grid search config |
| P1.6 | Ablation Comparison | P1 | Implemented | Statistical comparison with McNemar's, Wilcoxon, hit@k |
| P1.7 | Pipeline Parameter Passthrough | P1 | Implemented | Forward parameter overrides to backend execution requests |
| P2.1 | Evolutionary Operators | P2 | Planned | GA/DE population-based optimization |
| P2.2 | MCP Server Mode | P2 | Planned | Expose optimization as MCP tools |
| P2.3 | Streamlit Dashboard | P2 | Planned | Visual campaign browser and trial comparison |
| P2.4 | Non-Prompt Optimization Targets | P2 | Planned | Generalize to schemas, scoring functions, fuzzy matchers |
| P2.5 | Public Deployment Readiness | P2 | Planned | Authentication, rate limiting, multi-tenancy |

---

## User Personas

**Solo Developer ("Prompt Engineer Pat")**
Builds LLM-powered features and needs to iterate on parameters systematically. Defines a dataset, runs an optimization campaign via the notebook, and gets back a better configuration with evidence. Uses JupyterLab as the primary interface. Values reproducibility and the ability to compare runs.

**Pipeline Operator ("CI/CD Casey")**
Integrates parameter optimization into automated workflows. Calls the REST API from scripts or CI pipelines. Needs structured responses and clear status reporting. Cares about idempotency and error handling. Currently uses the backends API for sync, execute, and compare; API-driven optimization becomes available after the workflow engine migration (P1.1).

**Benchmarking Researcher ("Dataset Dana")**
Runs systematic benchmarks against established datasets (MedMentions, BC5CDR, domain-specific LCA corpora). Values reproducibility, statistical rigor, and structured outputs suitable for publication. Uses PromptPotter to compare pipeline variants with p-values and per-query breakdowns, then exports results for inclusion in research papers.

---

## Key Terms

This document uses terms defined in the [Project Charter](project-charter.md): **Campaign**, **Trial**, **PROMPT_STATE**, **Evaluation dataset**, and **Grid Search**. Refer to the charter's Key Terms table for definitions.

**Additional terms used in requirements:**

| Term | Definition |
|------|-----------|
| **Configuration** | The complete set of tunable parameters for an optimization target: prompt text, few-shot examples, temperature, retrieval counts, similarity thresholds, and any other non-code values that affect LLM behavior. |
| **Evaluator** | A scoring function that compares actual outputs to expected outputs. Currently: exact string match (hit@1). LLM-as-judge evaluator exists in code but is not wired into the backend evaluation path. This is an architectural gap: M3 should wire backend evaluation through the evaluator framework (`api/evaluators/`), replacing the inline `==` check in `prompt_eval.py` with `ExactMatchEvaluator` and enabling future use of `CriteriaEvaluator` for semantic evaluation. |

---

## P0 — Must Have (Core Optimizer)

All P0 requirements are implemented and working. See the referenced source files for implementation details.

### P0.1: Backend Evaluation on Dataset

Evaluate prompts against labeled datasets via the backend's `POST /matches` with `ranking_prompt` override. Content-addressed deduplication, incremental `.partial.jsonl` writes for crash recovery, partial-run resume.

**Implementation:** `api/services/prompt_eval.py`

### P0.2: Failure Analysis and Suggestions

Analyze evaluation results to categorize failure patterns and generate structured suggestions: failure categories, parameter suggestions, prompt phrase fragments, suggested config JSON.

**Implementation:** `api/services/prompt_optimizer.py` — `generate_suggestions()`

### P0.3: Candidate Generation

Generate N variant PROMPT_STATEs via LLM meta-prompt informed by failure examples. Each candidate is a derived state with lineage tracking.

**Implementation:** `api/services/prompt_optimizer.py` — `generate_candidates()`

### P0.4: Optimization Loop

Semi-automatic loop with patience-based stopping, or manual per-round control. Configurable via campaign JSON (`n_variants`, `creativity`, `improvement_threshold`, `patience`, `max_rounds`).

**Implementation:** `notebooks/_campaign_lib.py` — `run_optimization_loop()`, `run_manual_round()`

### P0.5: PROMPT_STATE Tracking

Immutable, versioned prompt snapshots organized into 3 optimization layers. `render()`, `derive()`, `diff()`.

**Implementation:** `api/models/prompt_state.py`

### P0.6: Grid Search Exploration

Systematic exploration of Layer 1 prompt field variants. Default axis library, LLM context restructuring, distance-weighted stratified sampling, plan persistence with automatic resume, per-point query sampling, content-addressed deduplication, LLM result analysis.

**Implementation:** `api/services/grid_search.py`

---

## P1 — Should Have (Workflow Engine Migration and Integration)

### P1.1: Workflow Engine Migration

**As a** developer, **I want** the optimizer migrated into the existing workflow engine **so that** optimization runs as a proper DAG with reusable nodes and API-driven orchestration.

**What the system does:**
- Creates optimization nodes wrapping existing service logic:
  - **InitNode** — wraps `restructure_context()` to produce initial PROMPT_STATE from context
  - **GrowFilterNode** — wraps `generate_candidates()` to produce N variant PROMPT_STATEs
  - **AnalysisEvalNode** — wraps `evaluate_prompt_batch()` + `generate_suggestions()` to score and analyze
- Nodes run inside the existing workflow engine (`api/core/workflow_runner.py`)
- Optimization workflow definition (CWL-style) wires nodes into a linear pipeline

**Target architecture:** The existing workflow engine provides DAG execution, node reuse, and context passing. The optimizer currently works as standalone services orchestrated by the notebook. This migration formalizes the architecture without changing the underlying optimization logic.

**Acceptance criteria:**
1. InitNode, GrowFilterNode, and AnalysisEvalNode are registered as workflow nodes
2. An optimization workflow definition runs end-to-end in the workflow runner
3. The workflow produces scored PROMPT_STATEs with full lineage
4. Existing service logic is reused (nodes are thin wrappers, not reimplementations)

### P1.2: Iterative Feedback Cycling

**As a** developer, **I want** the optimizer to automatically route between three feedback paths **so that** it can adapt its strategy based on analysis results.

**What the system does:**
- Analysis node produces a `next_action` decision routing to one of three feedback paths:
  - `"generate"` — **Layer 1**: create new prompt variants (vary persona, instruction, etc.)
  - `"refine context"` — **Layer 2**: update context and parameters
  - `"modify plan"` — **Layer 3**: change the optimization strategy
- Counter-based iteration with configurable limit (N iterations)
- Switch-based routing via workflow engine

**Acceptance criteria:**
1. The `next_action` field correctly routes to the three feedback paths
2. Counter-based stopping ends the loop after N iterations
3. Each feedback path modifies the appropriate optimization layer
4. The full cycling workflow runs end-to-end

### P1.3: Campaign Registry

**As a** developer, **I want** formal campaign/trial persistence **so that** optimization data is structured for Langfuse/MLflow compatibility from the start.

**What the system does:**
- Campaigns persist to `.promptpotter/campaigns/` with structured metadata, trial JSONL results, and lineage tracking
- Registry hierarchy: Campaign → Trial → PROMPT_STATE + scores
- API endpoints for listing, detail, and export
- Data format designed for Langfuse trace correlation and MLflow experiment mapping

**Acceptance criteria:**
1. Campaigns persist to disk with metadata, trials, and lineage
2. List/detail/export API endpoints work
3. Full lineage is reconstructable from registry files
4. Data format is compatible with Langfuse trace IDs and MLflow run IDs

### P1.4: Full Langfuse Integration

**As a** developer, **I want** every optimization trial traced in Langfuse **so that** I have full observability of campaign progress.

**What the system does:**
- Each evaluation round creates a Langfuse trace with scores attached
- Campaign-level trace grouping links all trials
- The Langfuse wrapper (`langfuse_client.py`) is extended from stub to full integration

**Acceptance criteria:**
1. Every evaluation round has a Langfuse trace with accuracy scores
2. Campaign traces are grouped with parent-child relationships
3. Langfuse dashboard shows optimization progress over rounds

### P1.5: Discovery Protocol

Backend pipeline parameter discovery via `GET /pipeline`. TermNorm exposes pipeline topology and tunable parameters; PromptPotter uses the schema for grid search config validation and parameter passthrough.

**Implementation:** `api/services/backend_client.py` — `GET /pipeline` integration. **Status:** Implemented (M2).

### P1.6: Ablation Comparison

Statistical comparison with McNemar's test, Wilcoxon signed-rank, hit@k, MRR. Per-query classification of where each variant won/lost.

**Implementation:** `api/services/comparison.py`. **Status:** Implemented (M1). Endpoint: `POST /api/v1/backends/{id}/compare`.

### P1.7: Pipeline Parameter Passthrough

Forward `pipeline_params` and `ranking_prompt` override to backend `/matches`. Echoed in execution responses.

**Implementation:** `api/services/backend_client.py`. **Status:** Implemented (M1). Endpoint: `POST /api/v1/backends/{id}/execute`.

### P1.8: Data Loop (Eval Reuse Across Optimization Threads)

**As a** prompt engineer, **I want** all evaluation data from completed optimization runs to be automatically available when starting a new optimization **so that** I build on prior work instead of re-evaluating from scratch.

**What the system does:**
- Every evaluation — grid search, sensitivity scan, or feedback cycle — writes to the shared `dataset_runs` store via `evaluate_prompt_cached()` with content-addressed deduplication
- The coverage advisor (`search/coverage.py`) discovers all stored `dataset_runs` regardless of which optimization thread produced them
- When starting a new optimization, the sensitivity scan skips axes/variants that already have sufficient cached data
- The human workflow is: optimize → stop → re-scan → start fresh from a new calculated starting point

**Implementation status:** The data persistence path already exists (`evaluate_prompt_cached` → `DatasetRunStore`). The coverage advisor and historical index (`build_prompt_result_index`) already discover stored runs. Remaining work: verify end-to-end that feedback cycle eval data is indexed by the coverage advisor, and clean up any duplication in the eval wrappers.

**Acceptance criteria:**
1. Feedback cycle candidate evaluations are stored in `dataset_runs` with content hashes
2. Running sensitivity scan after a completed feedback cycle shows cached results from that cycle in the data inventory
3. Coverage advisor correctly identifies feedback cycle data as reusable
4. No eval data is siloed per campaign — all paths write to the same shared store

---

## P2 — Nice to Have (Advanced Capabilities)

### P2.1: Evolutionary Operators

GA/DE population-based optimization: maintain a population of N configurations, apply crossover and mutation, selection pressure per generation. Inspired by EvoPrompt.

### P2.2: MCP Server Mode

Expose optimization as MCP tools for Claude Code and other MCP-capable clients. At minimum: start campaign, check status, get results.

### P2.3: Streamlit Dashboard

Visual interface: campaign browser with score trajectories, trial comparison with structured diffs, dataset explorer with per-item scores.

### P2.4: Non-Prompt Optimization Targets

Generalize the optimization loop to schemas, scoring functions, fuzzy matching thresholds, retrieval queries, GA settings. Pluggable state schema replacing PROMPT_STATE.

### P2.5: Public Deployment Readiness

Stateless API, API key authentication, rate limiting, multi-tenancy, per-user data isolation.

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Single evaluation (500-item dataset) | Completes within 10 minutes |
| Full optimization run (5 iterations, 500 items) | Completes within 60 minutes |
| Project store storage per campaign | Less than 10 MB |
| Concurrent optimizations | 1 for MVP; 3+ in future |
| LLM provider support | Groq and OpenAI (any provider exposing the OpenAI chat completions API) |
| Python version | 3.13 |
| Primary evaluation mode | Backend via `/matches` endpoint (no local evaluation fallback) |
| Dataset location | External (consuming project's repository, not PromptPotter) |
| API design | Stateless request handling; no server-side session state between requests |

---

## Traceability Matrix

**Requirements to Charter Success Criteria:**

| Requirement | SC1: Measurable Improvement | SC2: Reproducibility | SC3: Langfuse Observability | SC4: Time to First Optimization | SC5: TermNorm Validation | SC6: Generalization Beyond Prompts |
|-------------|:---:|:---:|:---:|:---:|:---:|:---:|
| P0.1 Backend Evaluation | x | x | | x | x | |
| P0.2 Failure Analysis | x | | | | x | |
| P0.3 Candidate Generation | x | | | | x | |
| P0.4 Optimization Loop | x | x | | x | x | |
| P0.5 PROMPT_STATE Tracking | | x | | | | |
| P0.6 Grid Search | x | x | | x | x | |
| P1.1 Workflow Engine Migration | | | | | | x |
| P1.2 Feedback Cycling | x | | | | | |
| P1.3 Campaign Registry | | x | x | | | |
| P1.4 Langfuse Integration | | | x | | | |
| P1.5 Discovery Protocol | | | | | x | |
| P1.6 Ablation Comparison | x | x | | | x | |
| P1.7 Parameter Passthrough | | | | | x | |
| P2.4 Non-Prompt Targets | x | | | | | x |

**Charter Success Criteria to Requirements (reverse mapping):**

| Charter Success Criterion | Required By (P0/P1) | Enhanced By (P2) |
|--------------------------|---------------------|------------------|
| SC1: Measurable Improvement | P0.1, P0.2, P0.3, P0.4, P0.6, P1.2, P1.6 | P2.1, P2.4 |
| SC2: Reproducibility | P0.1, P0.4, P0.5, P0.6, P1.3, P1.6 | — |
| SC3: Langfuse Observability | P1.3, P1.4 | — |
| SC4: Time to First Optimization | P0.1, P0.4, P0.6 | P2.2 |
| SC5: TermNorm Validation | P0.1, P0.2, P0.3, P0.4, P0.6, P1.5, P1.6, P1.7 | — |
| SC6: Generalization Beyond Prompts | P1.1 (foundation) | P2.4 |

**Coverage notes:**
- SC3 (Langfuse) is currently unmet — Langfuse wrapper exists but full per-trial tracing is P1.4 (planned for M3). P1.3 (Campaign Registry) ensures the data layer is Langfuse-compatible from the start.
- SC6 is post-M4 and has P1.1 as its foundation — the workflow engine migration makes the architecture pluggable for non-prompt targets.
- All six success criteria have at least one P0 or P1 requirement ensuring they are achievable.
