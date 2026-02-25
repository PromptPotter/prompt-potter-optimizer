# Product Requirements Document: PromptPotter Optimizer

**Version:** 0.7.0
**Date:** 2026-02-25
**Status:** Active
**Depends on:** [Project Charter v0.7.0](project-charter.md)

---

## Requirements Summary

| ID | Name | Priority | Status | Description |
|----|------|----------|--------|-------------|
| P0.1 | Backend Evaluation on Dataset | P0 | Implemented (M2) | Evaluate a prompt against a labeled dataset via the backend's `/matches` endpoint with content-addressed deduplication and crash recovery |
| P0.2 | Failure Analysis | P0 | Implemented (M2) | Analyze evaluation results to categorize failure patterns and generate actionable suggestions |
| P0.3 | Candidate Generation | P0 | Implemented (M2) | Generate improved prompt configurations via LLM meta-prompt informed by failure analysis |
| P0.4 | Optimization Loop | P0 | Implemented (M2) | Semi-automatic or manual optimization rounds with patience-based stopping |
| P0.5 | PROMPT_STATE Tracking | P0 | Implemented (M1) | Immutable, versioned prompt snapshots with structured metadata and lineage |
| P0.6 | Grid Search Exploration | P0 | Implemented (M2) | Systematic exploration of Layer 1 prompt field variants with sampling, deduplication, and LLM analysis |
| P1.1 | Optimizer Nodes | P1 | Implemented (M3) | InitNode, GrowFilterNode, AnalysisEvalNode wrapping existing service logic |
| P1.2 | Iterative Feedback Cycling | P1 | Implemented (M3) | `run_feedback_cycle()` with 3-path routing, patience-based stopping, per-query/candidate progress callbacks |
| P1.3 | Campaign Registry | P1 | Implemented (M3) | `CampaignStore` + `campaign_registry.py` for campaign/trial persistence with Langfuse/MLflow-compatible data format |
| P1.4 | Langfuse Integration | P1 | Implemented (M3) | Per-trial tracing with score attachment, campaign-level grouping, span logging per round |
| P1.5 | Discovery Protocol | P1 | Implemented (M2) | Discover backend pipeline parameters via `GET /pipeline` for dynamic grid search config |
| P1.6 | Ablation Comparison | P1 | Implemented (M1) | Statistical comparison with McNemar's, Wilcoxon, hit@k |
| P1.7 | Pipeline Parameter Passthrough | P1 | Implemented (M1) | Forward parameter overrides to backend execution requests |
| P1.8 | Sensitivity Scan | P1 | Implemented (M3) | OAT perturbation scanning with axis classification and diagnostic set builder |
| P1.9 | Data Loop (Eval Reuse) | P1 | Implemented (M3) | All eval data from any optimization path feeds back via shared `dataset_runs` store and coverage advisor |
| P2.1 | Evolutionary Operators | P2 | Planned | GA/DE population-based optimization |
| P2.2 | MCP Server Mode | P2 | Planned | Expose optimization as MCP tools |
| P2.3 | Streamlit Dashboard | P2 | Planned | Visual campaign browser and trial comparison |
| P2.4 | Non-Prompt Optimization Targets | P2 | Planned | Generalize to schemas, scoring functions, fuzzy matchers |
| P2.5 | Public Deployment Readiness | P2 | Planned | Authentication, rate limiting, multi-tenancy |

---

## User Personas

**Solo Developer ("Prompt Engineer Pat")**
Builds LLM-powered features and needs to iterate on parameters systematically. Defines a dataset, runs an optimization campaign via the notebook, and gets back a better configuration with evidence. Uses JupyterLab as the primary interface. Values reproducibility and the ability to compare runs. The two-loop workflow (explore -> optimize -> harvest -> reuse) is their daily workflow.

**Pipeline Operator ("CI/CD Casey")**
Integrates parameter optimization into automated workflows. Calls the REST API from scripts or CI pipelines. Needs structured responses and clear status reporting. Cares about idempotency and error handling. Uses the backends API for sync, execute, and compare. The feedback cycle orchestrator is callable from any Python context, enabling script-driven optimization.

**Benchmarking Researcher ("Dataset Dana")**
Runs systematic benchmarks against established datasets (MedMentions, BC5CDR, domain-specific LCA corpora). Values reproducibility, statistical rigor, and structured outputs suitable for publication. Uses PromptPotter to compare pipeline variants with p-values and per-query breakdowns, then exports results for inclusion in research papers.

---

## Key Terms

This document uses terms defined in the [Project Charter](project-charter.md): **Campaign**, **Trial**, **PROMPT_STATE**, **Evaluation dataset**, **Grid Search**, **Sensitivity Scan**, and **Feedback Cycle**. Refer to the charter's Key Terms table for definitions.

**Additional terms used in requirements:**

| Term | Definition |
|------|-----------|
| **Configuration** | The complete set of tunable parameters for an optimization target: prompt text, few-shot examples, temperature, retrieval counts, similarity thresholds, and any other non-code values that affect LLM behavior. |
| **Evaluator** | A scoring function that compares actual outputs to expected outputs. Currently: `ExactMatchEvaluator` (hit@1) is the active evaluator used by `prompt_eval.py`. `CriteriaEvaluator` (LLM-as-judge) exists in the evaluator framework and is available for future semantic evaluation. |
| **Coverage Advisor** | The system (`search/coverage.py`) that discovers all stored `dataset_runs` and determines which prompt variants already have cached evaluation data, enabling the reuse loop. |

---

## P0 -- Must Have (Core Optimizer)

All P0 requirements are implemented and working. See the referenced source files for implementation details.

### P0.1: Backend Evaluation on Dataset

Evaluate prompts against labeled datasets via the backend's `POST /matches` with `ranking_prompt` override. Content-addressed deduplication, incremental `.partial.jsonl` writes for crash recovery, partial-run resume. All evaluation paths converge on `evaluate_prompt_cached()`.

**Implementation:** `api/services/prompt_eval.py`

### P0.2: Failure Analysis and Suggestions

Analyze evaluation results to categorize failure patterns and generate structured suggestions: failure categories, parameter suggestions, prompt phrase fragments, suggested config JSON.

**Implementation:** `api/services/prompt_optimizer.py` -- `generate_suggestions()`

### P0.3: Candidate Generation

Generate N variant PromptStates via LLM meta-prompt informed by failure examples. Each candidate is a derived state with lineage tracking.

**Implementation:** `api/services/prompt_optimizer.py` -- `generate_candidates()`

### P0.4: Optimization Loop

Semi-automatic loop with patience-based stopping, or manual per-round control. Configurable via campaign JSON (`n_variants`, `creativity`, `improvement_threshold`, `patience`, `max_rounds`). Now also available as `run_feedback_cycle()` in the service layer.

**Implementation:** `api/services/feedback_cycle.py`, `notebooks/_campaign_lib.py`

### P0.5: PROMPT_STATE Tracking

Immutable, versioned prompt snapshots organized into 3 optimization layers. `render()`, `derive()`, `diff()`.

**Implementation:** `api/models/prompt_state.py`

### P0.6: Grid Search Exploration

Systematic exploration of Layer 1 prompt field variants. Default axis library, LLM context restructuring, distance-weighted stratified sampling, plan persistence with automatic resume, per-point query sampling, content-addressed deduplication, LLM result analysis.

**Implementation:** `api/services/search/grid_core.py`, `api/services/search/context.py`, `api/services/search/plan_persistence.py`

---

## P1 -- Should Have (M3 Optimization Infrastructure)

### P1.1: Optimizer Nodes

Three optimization nodes wrapping existing service logic as typed, composable units:

- **InitNode** -- wraps `restructure_context()` to produce initial PromptState from context
- **GrowFilterNode** -- wraps `generate_candidates()` to produce N variant PromptStates
- **AnalysisEvalNode** -- wraps `evaluate_prompt_cached()` + `select_round_winner()` + `generate_suggestions()` to score, select, and route

**Implementation:** `api/nodes/optimizer_nodes.py`
**Status:** Implemented (M3). All three nodes registered, following `NodeBase` pattern with typed Pydantic inputs/outputs.

**Acceptance criteria (met):**
1. InitNode, GrowFilterNode, and AnalysisEvalNode are registered as workflow nodes
2. Each node follows the existing node pattern (typed inputs/outputs, Pydantic models)
3. Nodes are thin wrappers -- existing service logic is reused, not reimplemented
4. AnalysisEvalNode uses `evaluate_prompt_cached()` which internally uses `ExactMatchEvaluator`

### P1.2: Iterative Feedback Cycling

Automated 3-path routing with patience-based stopping, counter-based iteration, per-query and per-candidate progress callbacks.

**Implementation:** `api/services/feedback_cycle.py` -- `run_feedback_cycle()`
**Status:** Implemented (M3).

**What the system does:**
- Orchestrates InitNode -> GrowFilterNode -> AnalysisEvalNode in a loop
- AnalysisEvalNode produces `next_action` routing: `generate` (Layer 1), `refine_context` (Layer 2), `modify_plan` (Layer 3), `stop`
- Patience-based stopping (configurable consecutive non-improving rounds)
- Per-round Langfuse spans with accuracy scores
- Progress callbacks for per-query and per-candidate evaluation status

**Acceptance criteria (met):**
1. The `next_action` field correctly routes to feedback paths
2. Patience-based stopping ends the loop after N stalls
3. Each feedback path targets the appropriate optimization layer
4. The full cycling workflow runs end-to-end with Langfuse tracing

### P1.3: Campaign Registry

Formal campaign/trial persistence with Langfuse/MLflow-compatible data structure.

**Implementation:** `api/services/campaign_registry.py`, `api/services/stores/campaign_store.py`
**Status:** Implemented (M3).

**What the system does:**
- Campaigns persist to `.promptpotter/projects/{backend_id}/campaigns/` with metadata and trial index
- Registry hierarchy: Campaign -> Trial -> PromptState + scores + lineage
- Functions: `create_campaign()`, `record_trial()`, `record_campaign_rounds()`, `complete_campaign()`, `get_campaign_lineage()`
- `CampaignStore` provides file I/O with trial detail files and campaign export

**Acceptance criteria (met):**
1. Campaigns persist to disk with metadata, trials, and lineage
2. Full lineage is reconstructable via `get_campaign_lineage()`
3. Data format includes `langfuse_trace_id` and `mlflow_run_id` fields

### P1.4: Langfuse Integration

Per-trial tracing with score attachment and campaign-level grouping.

**Implementation:** `api/services/langfuse_client.py`, integrated into `api/services/feedback_cycle.py`
**Status:** Implemented (M3).

**What the system does:**
- `LangfuseLogger` singleton with full implementation: `create_trace()`, `create_span()`, `create_generation()`, `create_score()`, `update_trace()`, `flush()`
- Feedback cycle creates campaign-level trace, per-round spans with accuracy scores, and final best-accuracy score
- Graceful fallback when Langfuse credentials are missing (no crash, just disabled logging)

**Acceptance criteria (met):**
1. Every feedback cycle round has a Langfuse span with accuracy score
2. Campaign traces are grouped with parent-child relationships (trace -> spans)
3. Final campaign trace includes best accuracy and stop reason

### P1.5: Discovery Protocol

Backend pipeline parameter discovery via `GET /pipeline`. TermNorm exposes pipeline topology and tunable parameters; PromptPotter uses the schema for grid search config validation and parameter passthrough.

**Implementation:** `api/services/backend_client.py`
**Status:** Implemented (M2).

### P1.6: Ablation Comparison

Statistical comparison with McNemar's test, Wilcoxon signed-rank, hit@k, MRR. Per-query classification of where each variant won/lost.

**Implementation:** `api/services/comparison.py`
**Status:** Implemented (M1). Endpoint: `POST /api/v1/backends/{id}/compare`.

### P1.7: Pipeline Parameter Passthrough

Forward `pipeline_params` and `ranking_prompt` override to backend `/matches`. Echoed in execution responses.

**Implementation:** `api/services/backend_client.py`
**Status:** Implemented (M1). Endpoint: `POST /api/v1/backends/{id}/execute`.

### P1.8: Sensitivity Scan

OAT (one-at-a-time) perturbation scanning to classify prompt axes by impact on accuracy. Diagnostic set builder with stratified sampling. Adaptive search via coordinate descent on high-impact axes.

**Implementation:** `api/services/search/smart_search.py`
**Status:** Implemented (M3).

**What the system does:**
- `build_diagnostic_set()` creates a stratified query set (~75% hits, ~25% misses)
- `sensitivity_scan()` perturbs one axis at a time, measures accuracy delta
- Axis classification: high/medium/low sensitivity
- Adaptive search: coordinate descent on high-sensitivity axes
- Plan persistence via `SmartSearchStore`

**Acceptance criteria (met):**
1. Diagnostic set is balanced between regression guard and improvement signal
2. OAT scan correctly identifies axis sensitivity
3. Results persist and survive kernel restarts

### P1.9: Data Loop (Eval Reuse Across Optimization Threads)

All evaluation data from any optimization path is automatically available when starting a new optimization.

**Implementation:** `api/services/search/coverage.py` -- `build_prompt_result_index()`, `assess_scan_coverage()`
**Status:** Implemented (M3).

**What the system does:**
- Every evaluation -- grid search, sensitivity scan, or feedback cycle -- writes to the shared `dataset_runs` store via `evaluate_prompt_cached()` with content-addressed deduplication
- `build_prompt_result_index()` discovers all stored `dataset_runs` regardless of which optimization thread produced them
- `assess_scan_coverage()` checks which variants already have sufficient cached data to skip backend calls
- The human workflow is: optimize -> stop -> re-scan -> start fresh from a new calculated starting point

**Acceptance criteria (met):**
1. All eval paths write to `dataset_runs` with content hashes via `evaluate_prompt_cached()`
2. `build_prompt_result_index()` discovers results from grid search, scan, and feedback cycle
3. Coverage advisor correctly identifies cached data as reusable
4. No eval data is siloed per campaign -- all paths write to the same shared store

---

## P2 -- Nice to Have (Advanced Capabilities)

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
| Crash recovery | Incremental `.partial.jsonl` writes; partial-run resume on restart |

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
| P1.1 Optimizer Nodes | | | | | | x |
| P1.2 Feedback Cycling | x | x | x | | | |
| P1.3 Campaign Registry | | x | x | | | |
| P1.4 Langfuse Integration | | | x | | | |
| P1.5 Discovery Protocol | | | | | x | |
| P1.6 Ablation Comparison | x | x | | | x | |
| P1.7 Parameter Passthrough | | | | | x | |
| P1.8 Sensitivity Scan | x | x | | x | x | |
| P1.9 Data Loop | x | x | | x | | |
| P2.4 Non-Prompt Targets | x | | | | | x |

**Charter Success Criteria to Requirements (reverse mapping):**

| Charter Success Criterion | Required By (P0/P1) | Enhanced By (P2) |
|--------------------------|---------------------|------------------|
| SC1: Measurable Improvement | P0.1, P0.2, P0.3, P0.4, P0.6, P1.2, P1.6, P1.8, P1.9 | P2.1, P2.4 |
| SC2: Reproducibility | P0.1, P0.4, P0.5, P0.6, P1.2, P1.3, P1.6, P1.8, P1.9 | -- |
| SC3: Langfuse Observability | P1.2, P1.3, P1.4 | -- |
| SC4: Time to First Optimization | P0.1, P0.4, P0.6, P1.8, P1.9 | P2.2 |
| SC5: TermNorm Validation | P0.1, P0.2, P0.3, P0.4, P0.6, P1.5, P1.6, P1.7, P1.8 | -- |
| SC6: Generalization Beyond Prompts | P1.1 (foundation) | P2.4 |

**Coverage notes:**
- SC3 (Langfuse) is now met -- per-trial tracing with scores, campaign-level grouping, and graceful fallback are implemented via P1.4.
- SC6 is post-M4 and has P1.1 as its foundation -- the optimizer nodes and feedback cycle architecture make the system pluggable for non-prompt targets.
- All six success criteria have at least one P0 or P1 requirement ensuring they are achievable.
