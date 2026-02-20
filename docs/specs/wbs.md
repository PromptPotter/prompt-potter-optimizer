# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.2.0
**Date:** 2026-02-19
**Status:** Draft
**Depends on:** [PRD](prd.md), [ADD](add.md)

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

## Phase 1: Foundation (M1)

### 1.1 Add PROMPT_STATE Model — Complete

- **Scope:** Create the Pydantic model that snapshots prompt text, few-shot examples, and an open parameters dictionary (temperature, retrieval count, thresholds, etc.). Include structured diff generation between two states.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P0.5
- **Done when:**
  - PROMPT_STATE model is importable with typed fields for prompt text, few-shot examples, and a parameters dict
  - Diff function produces a structured comparison between two PROMPT_STATE instances
- **Completed:** `06b6635 feat: add PromptState model with diff and derive`

### 1.2 Add Test Fixtures and Dataset Helpers

- **Scope:** Create shared test infrastructure: conftest fixtures, sample evaluation datasets, and factory functions for PROMPT_STATE and workflow objects.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** —
- **Done when:**
  - Shared fixtures provide mock LLM client, sample datasets, and PROMPT_STATE factories
  - At least one sample dataset with 10+ input/expected-output pairs is available for tests

### 1.3 Write Tests for Existing Evaluators

- **Scope:** Unit tests for ExactMatchEvaluator and CriteriaEvaluator covering expected passes, expected failures, and edge cases (empty input, special characters, normalization).
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1
- **Done when:**
  - Tests cover both evaluator types with at least 5 test cases each
  - Edge cases (empty strings, Unicode, case sensitivity) are exercised

### 1.4 Write Tests for Workflow Runner

- **Scope:** Unit tests for the workflow execution engine covering single-node workflows, multi-step DAGs, error propagation, and Langfuse trace emission.
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1, P1.2
- **Done when:**
  - Tests verify correct topological execution order for multi-step workflows
  - Error in one node propagates correctly without silent failure
  - Langfuse tracing is invoked (mocked) during execution

### 1.5 Set Up CI Pipeline

- **Scope:** GitHub Actions workflow running lint (ruff) and test (pytest) on every push and PR. Fail-fast on lint errors, report test results.
- **Sessions:** 1
- **Dependencies:** 1.3, 1.4
- **PRD Ref:** —
- **Done when:**
  - CI runs on push to main and on all PRs
  - Lint and test steps both pass on current codebase
  - Failed lint or test blocks merge

### 1.6 Update CLAUDE.md with M1 Status

- **Scope:** Mark M1 complete, update current milestone to M2, document any new conventions or patterns introduced during M1.
- **Sessions:** 1
- **Dependencies:** 1.5
- **PRD Ref:** —
- **Done when:**
  - CLAUDE.md reflects M1 as complete and M2 as current
  - Any new file patterns or test conventions are documented

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

**Phase 1 exit criteria:** All tests pass, CI is green, PROMPT_STATE model exists and is importable, ablation comparison produces statistical report, CLAUDE.md updated.

---

## Phase 2: Core Optimizer (M2)

### 2.1 Implement Analyzer Node

- **Scope:** Build a node that takes evaluation results, identifies failing examples below a configurable threshold, and categorizes failures into structured patterns (wrong format, missing info, hallucination, edge cases). Output is a structured failure report with cited examples.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P0.2
- **Done when:**
  - Produces at least three distinct failure categories with example citations
  - Output is a structured model (not free-text) consumable by the generator node
  - Unit tests with mock evaluation results verify categorization

### 2.2 Implement Generator Node

- **Scope:** Build a node that takes the current configuration, failure analysis, and optimization context, then generates N candidate configurations. Each candidate modifies any tunable parameter (prompt text, few-shot examples, temperature, retrieval count, thresholds) and includes a rationale.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P0.3
- **Done when:**
  - Generates 2-5 candidates (configurable) with distinct changes
  - Each candidate includes a rationale field explaining what changed and why
  - Candidates can modify non-prompt parameters when failure analysis suggests it

### 2.3 Implement Selector Node

- **Scope:** Build a node that picks the best candidate from a scored set. Implement best-of-N strategy first; design the interface to support additional strategies (tournament, weighted) later.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P0.4, P1.5
- **Done when:**
  - Best-of-N selection strategy works correctly on scored candidate sets
  - Strategy is configurable via an enum or string parameter
  - Selection decision includes a rationale in the output

### 2.4 Implement Optimization Orchestrator

- **Scope:** Build the orchestrator that runs the full evaluate-analyze-generate-evaluate-select cycle. Manages stopping criteria (max iterations, target score, convergence), tracks improvement trajectory, and returns the best configuration with full lineage.
- **Sessions:** 2
- **Dependencies:** 2.1, 2.2, 2.3
- **PRD Ref:** P0.4
- **Done when:**
  - Runs the complete loop end-to-end and returns the best configuration plus score trajectory
  - Stops on any configured stopping criterion
  - Each iteration is traceable with parent-child PROMPT_STATE references
  - Handles edge cases: no improvement after first iteration, all candidates score lower

### 2.5 Wire Optimize Router to Orchestrator

- **Scope:** Replace the placeholder optimize endpoint with a real implementation that accepts an optimization request, invokes the orchestrator, and returns structured results.
- **Sessions:** 1
- **Dependencies:** 2.4
- **PRD Ref:** P0.4
- **Done when:**
  - POST optimize endpoint accepts a configuration + dataset and returns optimization results
  - Response includes best configuration, score trajectory, and iteration count
  - Error cases return structured error responses

### 2.6 End-to-End Optimization Test

- **Scope:** Integration test that runs the full optimization loop against a small sample dataset (10-20 items), verifying the API contract, score improvement, and Langfuse trace emission.
- **Sessions:** 1
- **Dependencies:** 2.5
- **PRD Ref:** P0.1, P0.4
- **Done when:**
  - Test runs a multi-iteration optimization and asserts the final score >= baseline
  - Langfuse traces are emitted (mocked) with parent-child structure
  - Test completes within CI time limits

**Phase 2 exit criteria:** POST optimize endpoint runs a real optimization loop and returns an improved configuration with trajectory. E2E test passes in CI.

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
| M2: Core Optimizer | 6 | 7 |
| M3: Registry and Tracking | 6 | 6 |
| M4: Integration and Polish | 6 | 9 |
| **Total** | **30** | **34** |

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
M2 Core Optimizer            |   |
 |-- 2.1 Analyzer -----------+   |
 |-- 2.2 Generator ----------+   |
 |-- 2.3 Selector (no deps)      |
 |       |                        |
 |       +-- 2.4 Orchestrator (x2)
 |            |
 |            +-- 2.5 Router
 |                 |
 |                 +-- 2.6 E2E Test
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
| P0.1 | 1.3, 1.4, 2.6 | M1, M2 |
| P0.2 | 2.1 | M2 |
| P0.3 | 2.2 | M2 |
| P0.4 | 2.3, 2.4, 2.5 | M2 |
| P0.5 | 1.1, 3.3 | M1, M3 |
| P1.1 | 3.1, 3.2, 3.3, 3.5, 3.6 | M3 |
| P1.2 | 1.4 | M1 |
| P1.3 | 4.3 | M4 |
| P1.4 | 4.2 | M4 |
| P1.5 | 2.3 | M2 |
| P1.6 | 1.7 | M1 |
| P2.1-P2.3 | — | Unscheduled |
| P2.4 | 4.4 | M4 |
