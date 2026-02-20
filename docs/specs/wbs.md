# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.4.0
**Date:** 2026-02-20
**Status:** Draft
**Depends on:** [PRD v0.4.0](prd.md), [ADD v0.4.0](add.md)

---

## Estimation Approach

- **1 session = 1 work package** — each package is scoped for a single Claude Code session
- Multi-session packages are split into separate packages
- Dependencies are explicit; a package cannot start until all listed predecessors are complete

---

## Phase 0: Specifications (M0) — Complete

| ID | Work Package | Sessions | Dependencies | PRD Ref |
|----|-------------|:--------:|:------------:|---------|
| 0.1 | Write project charter | 1 | — | — |
| 0.2 | Write PRD | 1 | 0.1 | — |
| 0.3 | Write ADD | 1 | 0.2 | — |
| 0.4 | Write WBS and roadmap | 1 | 0.2, 0.3 | — |
| 0.5 | Update CLAUDE.md and create CHANGELOG | 1 | 0.4 | — |

---

## Phase 1: Foundation (M1) — Complete

### 1.1 Add PROMPT_STATE Model — Complete

- **Scope:** Create the Pydantic model that snapshots prompt text, few-shot examples, and an open parameters dictionary (temperature, retrieval count, thresholds, etc.). Include structured diff generation between two states.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P0.5
- **Done when:**
  - PROMPT_STATE model is importable with typed fields for prompt text, few-shot examples, and a parameters dict
  - Diff function produces a structured comparison between two PROMPT_STATE instances
- **Completed:** `06b6635 feat: add PromptState model with diff and derive`

### 1.2 Add Test Fixtures and Dataset Helpers — Complete

- **Scope:** Create shared test infrastructure: conftest fixtures, sample evaluation datasets, and factory functions for PROMPT_STATE and workflow objects.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** —
- **Done when:**
  - Shared fixtures provide mock LLM client, sample datasets, and PROMPT_STATE factories
  - At least one sample dataset with 10+ input/expected-output pairs is available for tests
- **Completed:** `28833e3`, `7664b52` — conftest.py with mock_llm_client, tmp_store, auto-reset Langfuse singleton

### 1.3 Write Tests for Existing Evaluators — Complete

- **Scope:** Unit tests for ExactMatchEvaluator and CriteriaEvaluator covering expected passes, expected failures, and edge cases (empty input, special characters, normalization).
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1
- **Done when:**
  - Tests cover both evaluator types with at least 5 test cases each
  - Edge cases (empty strings, Unicode, case sensitivity) are exercised
- **Completed:** `7664b52` — test_evaluators.py with ExactMatch and CriteriaEvaluator tests, registry alias coverage

### 1.4 Write Tests for Workflow Runner — Complete

- **Scope:** Unit tests for the workflow execution engine covering single-node workflows, multi-step DAGs, error propagation, and Langfuse trace emission.
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1, P1.2
- **Done when:**
  - Tests verify correct topological execution order for multi-step workflows
  - Error in one node propagates correctly without silent failure
  - Langfuse tracing is invoked (mocked) during execution
- **Completed:** `7664b52`, `0d2acc1` — test_workflow_runner.py with DAG sort, input resolution, execution tests

### 1.5 Set Up CI Pipeline — Complete

- **Scope:** GitHub Actions workflow running lint (ruff) and test (pytest) on every push and PR. Fail-fast on lint errors, report test results.
- **Sessions:** 1
- **Dependencies:** 1.3, 1.4
- **PRD Ref:** —
- **Done when:**
  - CI runs on push to main and on all PRs
  - Lint and test steps both pass on current codebase
  - Failed lint or test blocks merge
- **Completed:** `7664b52` — .github/workflows/ci.yml with ruff + pytest steps

### 1.6 Update CLAUDE.md with M1 Status — Complete

- **Scope:** Mark M1 complete, update current milestone to M2, document any new conventions or patterns introduced during M1.
- **Sessions:** 1
- **Dependencies:** 1.5
- **PRD Ref:** —
- **Done when:**
  - CLAUDE.md reflects M1 as complete and M2 as current
  - Any new file patterns or test conventions are documented
- **Completed:** `3cc31f1` — CLAUDE.md cleaned up to reflect current project state

### 1.7 Ablation Comparison Scripts — Complete

- **Scope:** Replay script (calls external pipeline API with component skipped) + comparison script (offline statistical analysis with McNemar's and Wilcoxon p-values, structured JSON output). Validates against TermNorm experiment fixture.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P1.6
- **Done when:**
  - Replay script produces Variant A results from TermNorm API with `skip_llm_ranking=true`
  - Comparison script outputs structured JSON with hit@k, MRR, p-values, and per-query classification
  - Both scripts work offline from saved fixture files (replay only needs API for initial run)
- **Completed:** Exceeds original scope — now includes project-based backend storage (`ProjectStore`), notebook exploration workflow, incremental writes with `on_result` callback, and REST API endpoints (`/backends/*`). Key commits: `88e3b83`, `ab154d7`, `244714d`, `7bfde52`.

**Phase 1 exit criteria:** All tests pass, CI is green, PROMPT_STATE model exists and is importable, ablation comparison produces statistical report, CLAUDE.md updated. **All exit criteria met.**

---

## Phase 2: Core Optimizer — DAG-Based Workflow (M2)

Phase 2 implements the **Phase 1 (linear mode)** of the DAG-based optimization workflow. The full cycling mode with feedback paths is deferred to post-M2.

### 2.1 Implement Initialization Node

- **Scope:** Build the initialization node that loads evaluation dataset + context, then uses an AI Agent with structured output parsing to produce typed prompt components (task_description, base_instruction, answer_format). Uses Groq + Llama 4 Maverick with structured output.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P0.3
- **Done when:**
  - Accepts arbitrary context input and produces structured prompt components
  - Structured output parser validates the AI Agent's response against the schema
  - Eval dataset is loaded and available for downstream nodes
  - Unit tests with mock context verify component extraction

### 2.2 Implement Grow/Filter Node

- **Scope:** Build the Grow/Filter node that takes the current prompt state (persona, task_intent, problem_description, instruction, thinking_style, plan) and enriches it. The node expands, refines, or constrains prompt components based on the current plan and context.
- **Sessions:** 1
- **Dependencies:** 1.1, 2.1
- **PRD Ref:** P0.3
- **Done when:**
  - Accepts a prompt state and produces an enriched prompt state (`main_data_plus`)
  - All prompt component fields are populated and meaningful
  - Output is a valid PromptState with `parent_id` linking to the input state
  - Unit tests verify enrichment produces meaningfully different output

### 2.3 Implement Analysis + Evaluation Node

- **Scope:** Build the combined analysis and evaluation node. Evaluates the current prompt state against the dataset, produces scores, identifies failure patterns, and decides the `next_action` (one of: "generate", "refine context", "modify plan"). For Phase 1 (linear mode), `next_action` is informational only; for Phase 2 it drives the feedback router.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P0.1, P0.2
- **Done when:**
  - Evaluates a prompt state against the dataset and returns aggregate + per-item scores
  - Produces a structured report with failure categories and cited examples
  - Report includes a `next_action` field with one of the three valid values
  - At least two evaluator types supported: exact match and LLM-as-judge

### 2.4 Implement Linear Mode Orchestrator (Phase 1)

- **Scope:** Build the orchestrator for Phase 1 (linear mode): initialization --> grow/filter --> analysis+evaluation --> output. No feedback cycling. Supports running N independent linear passes to generate diverse candidates, then selects the best by score. Manages the counter-based stop condition (counter >= 1 for linear mode).
- **Sessions:** 2
- **Dependencies:** 2.1, 2.2, 2.3
- **PRD Ref:** P0.4
- **Done when:**
  - Runs a single linear pass: init --> grow/filter --> evaluate --> output
  - Supports N parallel linear runs producing diverse prompt states
  - Selects the best prompt state by score from the N runs
  - Each run is traceable with parent-child PromptState references
  - Returns the best prompt state with lineage and score trajectory

### 2.5 Wire Optimize Router to Orchestrator

- **Scope:** Replace the placeholder optimize endpoint with a real implementation that accepts an optimization request (context + dataset), invokes the linear mode orchestrator, and returns structured results.
- **Sessions:** 1
- **Dependencies:** 2.4
- **PRD Ref:** P0.4
- **Done when:**
  - POST optimize endpoint accepts context + dataset and returns optimization results
  - Response includes best prompt state, score, and run metadata
  - Error cases return structured error responses

### 2.6 End-to-End Optimization Test

- **Scope:** Integration test that runs the linear mode optimization against a small sample dataset (10-20 items), verifying the API contract, prompt state quality, and Langfuse trace emission.
- **Sessions:** 1
- **Dependencies:** 2.5
- **PRD Ref:** P0.1, P0.4
- **Done when:**
  - Test runs N linear optimization passes and selects the best result
  - Langfuse traces are emitted (mocked) with parent-child structure
  - Test completes within CI time limits

### 2.7 Implement Feedback Router and Cycling Mode (Phase 2)

- **Scope:** Add the Switch-based feedback router and the three feedback paths (generate, refine context, modify plan). Enable counter thresholds > 1 for iterative cycling. Implement the `updated_context` and `updated_plan` nodes.
- **Sessions:** 2
- **Dependencies:** 2.4
- **PRD Ref:** P0.4, P2.1
- **Done when:**
  - Switch correctly routes to the three feedback paths based on `next_action`
  - "refine context" path updates context with critiques and metrics, then updates plan
  - "modify plan" path updates the plan directly
  - "generate" path loops back to main_data for new variant generation
  - Counter-based stop condition works with configurable threshold
  - Integration test demonstrates multi-cycle optimization with feedback

**Phase 2 exit criteria:** POST optimize endpoint runs the linear mode DAG and returns scored prompt states. E2E test passes in CI. Cycling mode (2.7) is implemented and tested but not required for the M2 exit gate.

---

## Phase 3: Registry and Tracking (M3)

### 3.1 Implement Campaign Registry

- **Scope:** Build the file-based registry that persists campaigns and trials to disk. Campaigns contain metadata and trial references; trials contain PROMPT_STATE snapshots and per-item results in JSONL format.
- **Sessions:** 1
- **Dependencies:** 2.4
- **PRD Ref:** P1.1
- **Done when:**
  - Creating a campaign writes metadata to the expected directory structure
  - Trials write metadata and JSONL results
  - Campaigns and trials can be listed and retrieved by ID

### 3.2 Integrate Registry into Optimization Loop

- **Scope:** Wire the orchestrator to write campaign and trial records during optimization. Every iteration persists its PROMPT_STATE, scores, and analysis.
- **Sessions:** 1
- **Dependencies:** 3.1, 2.4
- **PRD Ref:** P1.1
- **Done when:**
  - Running an optimization creates a campaign directory with trials
  - Progress events are appended during execution
  - After optimization, the full run is reconstructable from registry files

### 3.3 Add Lineage Tracking

- **Scope:** Record parent-child relationships between trials so the full tree from baseline to best configuration can be reconstructed.
- **Sessions:** 1
- **Dependencies:** 3.1
- **PRD Ref:** P0.5, P1.1
- **Done when:**
  - Lineage file records which trial spawned which
  - Given any trial, the full chain back to baseline is traversable
  - Lineage survives process restart (persisted, not in-memory only)

### 3.4 Add Langfuse Score Logging for Trials

- **Scope:** Ensure every trial's evaluation scores are logged to Langfuse with correct parent-child trace hierarchy.
- **Sessions:** 1
- **Dependencies:** 3.2
- **PRD Ref:** P0.1, SC3
- **Done when:**
  - 100% of trials have associated Langfuse traces
  - Scores are attached to the correct trial spans
  - Campaign-level trace groups all child trial traces

### 3.5 JSONL Export Endpoint

- **Scope:** Add an API endpoint that exports a campaign's trial results in JSONL format compatible with OpenAI Evals conventions.
- **Sessions:** 1
- **Dependencies:** 3.1
- **PRD Ref:** P1.1
- **Done when:**
  - GET export endpoint returns JSONL with per-item results for a given campaign
  - Format follows OpenAI Evals conventions
  - Returns 404 for nonexistent campaign IDs

### 3.6 Campaign List and Detail Endpoints

- **Scope:** Add API endpoints to list all campaigns and retrieve details for a single campaign including trial summaries and score trajectory.
- **Sessions:** 1
- **Dependencies:** 3.1
- **PRD Ref:** P1.1
- **Done when:**
  - GET campaigns endpoint returns a list of all campaigns with metadata
  - GET campaign detail returns full info including trial summaries
  - Responses are bounded for large registries

**Phase 3 exit criteria:** Optimization runs persist to the file registry, campaigns are viewable via API, Langfuse scores are populated for all trials.

---

## Phase 4: Integration and Polish (M4)

### 4.1 TermNorm Variant Comparison (Pinnacle Validation)

- **Scope:** Run the **Variant A vs Variant B** comparison that proves the whole system works (SC5). Evaluate Variant A (`entity_profiling` v1 + table reranker, no LLM2) once to establish the baseline score. Then run the optimization loop on Variant B's `llm_ranking` prompt -- generating v2, v3, etc. -- to find the best LLM2 prompt. Compare the best Variant B score against the Variant A baseline and produce a clear recommendation on whether the LLM2 call is worth the extra cost/latency. Development and testing uses the **BC5CDR 500-term subset** as the primary benchmark (well-known ground truth). LCA dataset validation follows when deploying to real-world use.
- **Sessions:** 2
- **Dependencies:** 2.6, 3.2
- **PRD Ref:** SC5
- **Done when:**
  - Variant A baseline score is established on the BC5CDR 500-term subset
  - Optimization campaign runs on `llm_ranking` prompt (Variant B), producing at least one improved version
  - Variant A and best Variant B scores are compared, with a clear recommendation produced
  - Full campaign is persisted and traceable in Langfuse
  - Optimized prompt versions are written back to TermNorm's prompt registry

### 4.2 Real Web Search Provider

- **Scope:** Replace the mock web search node with a real search API (Brave Search or SearxNG). Configurable via environment variables with fallback to mock.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P1.4
- **Done when:**
  - At least one real search provider is functional
  - Provider selected via env var, not code changes
  - Missing API key falls back to mock with a warning

### 4.3 Human-in-the-Loop Gates

- **Scope:** Pause the optimization loop after candidate generation for review. Supports approve/reject/edit via API (polling) and notebooks (interactive).
- **Sessions:** 2
- **Dependencies:** 2.5
- **PRD Ref:** P1.3
- **Done when:**
  - Optimization configurable to require human approval before evaluating candidates
  - API reports candidates awaiting review
  - Approve/reject/edit decisions respected by the loop
  - Rejected candidates are not evaluated

### 4.4 Streamlit Optimization Dashboard

- **Scope:** Campaign browser (list + score trajectories), trial comparison (side-by-side diffs), dataset explorer (per-item scores with filtering).
- **Sessions:** 2
- **Dependencies:** 3.6
- **PRD Ref:** P2.4
- **Done when:**
  - Campaign browser lists campaigns with score trajectory visualization
  - Trial comparison shows structured diffs between two trials
  - Dataset explorer supports filtering and sorting
  - Reads from the file-based registry, no additional data store

### 4.5 Docker Compose Update

- **Scope:** Update Docker Compose for optimizer + Langfuse with correct env var passthrough and health checks.
- **Sessions:** 1
- **Dependencies:** 2.5
- **PRD Ref:** —
- **Done when:**
  - `docker-compose up` starts optimizer and Langfuse
  - API keys and model config pass through
  - Health check confirms readiness

### 4.6 Documentation Update

- **Scope:** Update README, notebooks, and stale docs to reflect the complete optimization workflow.
- **Sessions:** 1
- **Dependencies:** 2.6
- **PRD Ref:** —
- **Done when:**
  - README documents optimization workflow and quickstart
  - At least one notebook demonstrates a full campaign
  - No references to removed or renamed components

**Phase 4 exit criteria:** Variant A vs Variant B comparison completes on BC5CDR 500-term subset with clear recommendation, optimized `llm_ranking` prompt versions written to TermNorm registry, dashboard shows campaign results, Docker works, HITL gates functional.

---

## Session Summary

| Phase | Packages | Sessions |
|-------|:--------:|:--------:|
| M0: Specifications | 5 | 5 |
| M1: Foundation | 7 | 7 |
| M2: Core Optimizer (DAG Workflow) | 7 | 9 |
| M3: Registry and Tracking | 6 | 6 |
| M4: Integration and Polish | 6 | 9 |
| **Total** | **31** | **36** |

---

## Dependency Graph

```
M0 (0.1-0.5)
 |
 v
M1 Foundation
 |-- 1.1 PROMPT_STATE ---+---+
 |-- 1.2 Fixtures -----+  |   |
 |   |-- 1.3 Eval Tests |  |   |
 |   |-- 1.4 WF Tests   |  |   |
 |       |                  |   |
 |       +-- 1.5 CI         |   |
 |            |              |   |
 |            +-- 1.6 Docs   |   |
 |                           |   |
 v                           v   v
M2 Core Optimizer (DAG)      |   |
 |-- 2.1 Initialization -----+   |
 |-- 2.2 Grow/Filter <-- 2.1     |
 |-- 2.3 Analysis+Eval ----------+
 |       |                        |
 |       +-- 2.4 Linear Orchestrator (x2) <-- 2.1, 2.2, 2.3
 |            |
 |            +-- 2.5 Router
 |            |    |
 |            |    +-- 2.6 E2E Test
 |            |         |
 |            +-- 2.7 Cycling Mode (x2) [Phase 2]
 |                      |
 v                      v
M3 Registry              |
 |-- 3.1 Registry <----- 2.4
 |    |-- 3.2 Integration
 |    |    |-- 3.4 Langfuse
 |    |-- 3.3 Lineage
 |    |-- 3.5 Export
 |    |-- 3.6 Endpoints
 |         |
 v         v
M4 Integration
 |-- 4.1 TermNorm E2E (x2) <-- 2.6, 3.2
 |-- 4.2 Web Search (independent)
 |-- 4.3 HITL (x2) <-- 2.5
 |-- 4.4 Dashboard (x2) <-- 3.6
 |-- 4.5 Docker <-- 2.5
 |-- 4.6 Docs <-- 2.6
```

---

## PRD Coverage

| PRD Req | Work Packages | Phase |
|---------|--------------|-------|
| P0.1 | 1.3, 1.4, 2.3, 2.6 | M1, M2 |
| P0.2 | 2.3 | M2 |
| P0.3 | 2.1, 2.2 | M2 |
| P0.4 | 2.4, 2.5, 2.7 | M2 |
| P0.5 | 1.1, 3.3 | M1, M3 |
| P1.1 | 3.1, 3.2, 3.3, 3.5, 3.6 | M3 |
| P1.2 | 1.4 | M1 |
| P1.3 | 4.3 | M4 |
| P1.4 | 4.2 | M4 |
| P1.5 | 2.4 | M2 |
| P1.6 | 1.7 | M1 |
| P2.1 | 2.7 | M2 (Phase 2) |
| P2.2-P2.3 | -- | Unscheduled |
| P2.4 | 4.4 | M4 |
| P2.5 | -- | Unscheduled (post-M4) |
| P2.6 | -- | Unscheduled (post-M4) |
