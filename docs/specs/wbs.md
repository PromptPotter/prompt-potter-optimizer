# Work Breakdown Structure: PromptPotter Optimizer

**Version:** 0.6.0
**Date:** 2026-02-23
**Status:** Active
**Depends on:** [PRD v0.6.0](prd.md), [ADD v0.6.0](add.md)

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
| 0.5 | Update CLAUDE.md | 1 | 0.4 | — |
| 0.6 | Spec rewrite v0.6.0 | 1 | — | — |

---

## Phase 1: Foundation (M1) — Complete

### 1.1 Add PROMPT_STATE Model — Complete

- **Scope:** Pydantic model with 3-layer architecture, structured diff, `derive()` for lineage-tracked children.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P0.5
- **Completed:** `06b6635 feat: add PromptState model with diff and derive`

### 1.2 Add Test Fixtures and Dataset Helpers — Complete

- **Scope:** conftest fixtures, sample datasets, factory functions.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** —
- **Completed:** `28833e3`, `7664b52`

### 1.3 Write Tests for Existing Evaluators — Complete

- **Scope:** Unit tests for ExactMatchEvaluator and CriteriaEvaluator.
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1
- **Completed:** `7664b52` — later pruned in `ceb9031` (test suite reduced to core-only: PromptState + ProjectStore)

### 1.4 Write Tests for Workflow Runner — Complete

- **Scope:** Unit tests for workflow execution engine.
- **Sessions:** 1
- **Dependencies:** 1.2
- **PRD Ref:** P0.1
- **Completed:** `7664b52`, `0d2acc1` — later pruned in `ceb9031` (test suite reduced to core-only)

### 1.5 Set Up CI Pipeline — Complete

- **Scope:** GitHub Actions: ruff lint + pytest on push/PR.
- **Sessions:** 1
- **Dependencies:** 1.3, 1.4
- **PRD Ref:** —
- **Completed:** `7664b52`

### 1.6 Update CLAUDE.md with M1 Status — Complete

- **Sessions:** 1
- **Dependencies:** 1.5
- **Completed:** `3cc31f1`

### 1.7 Ablation Comparison — Complete

- **Scope:** Replay + statistical comparison (McNemar's, Wilcoxon). Exceeded scope to include ProjectStore, backends router, and REST API endpoints.
- **Sessions:** 1
- **Dependencies:** 1.1
- **PRD Ref:** P1.6
- **Completed:** `88e3b83`, `ab154d7`, `244714d`, `7bfde52`

### 1.8 Pipeline Parameter Passthrough — Complete

- **Scope:** Forward pipeline knobs to backend `/matches` endpoint.
- **Sessions:** 1
- **Dependencies:** 1.7
- **PRD Ref:** P1.7
- **Completed:** `19b975f`

**Phase 1 exit criteria met:** All tests pass, CI green, PROMPT_STATE model exists, ablation comparison works, pipeline params forwarded.

---

## Phase 2: Core Optimizer (M2) — Complete

Phase 2 delivers the service-based optimization capabilities: grid search, iterative candidate generation, backend evaluation with deduplication, the HITL campaign notebook, and discovery protocol integration.

### 2.1 HITL Campaign Notebook — Complete

- **Scope:** Interactive Jupyter notebook for optimization campaigns with full HITL control. Editable campaign config JSON, candidate coverage diagnostics, baseline eval, iterative optimization with patience-based stopping, LLM-generated suggestions with phrase fragments.
- **Sessions:** 2
- **Dependencies:** 1.1
- **PRD Ref:** P0.3, P0.4
- **Deliverables:**
  - `notebooks/optimization_campaign.ipynb` — full campaign workflow
  - `notebooks/_campaign_lib.py` — thin wrapper delegating to `api/services/`
  - Semi-automatic loop (`run_optimization_loop()`) and manual rounds (`run_manual_round()`)
  - LLM suggestion generation with failure patterns, parameter suggestions, phrase fragments
  - Campaign summary with comparison table, flip tracking, lineage chain, winner save
- **Completed:** Exceeds scope — includes eval caching, incremental writes, crash recovery, rate-limit backoff, training-style progress display, `init_services()`. Key commits: `89d4a2f`, `534fa3e`, `c8c10a1`, `23717e5`, `ad5533d`, `beaf662`.

### 2.2 Grid Search Service — Complete

- **Scope:** Systematic exploration of Layer 1 prompt field variants via cartesian product sweep with LLM-assisted restructuring and analysis.
- **Sessions:** 1
- **Dependencies:** 1.1, 2.1
- **PRD Ref:** P0.6
- **Deliverables:**
  - `api/services/grid_search.py` — default axis library, LLM context restructuring, distance-weighted stratified sampling, grid execution, result analysis, winner selection
  - Grid plan persistence with stable identity hash and automatic resume
  - Per-point query sampling (`eval_queries_per_point`, `shared_queries`)
  - Backend evaluation via `/matches` with `ranking_prompt` override
  - Marginal stats, pairwise heatmaps, LLM-analyzed insights
- **Completed:** Key commits: `534fa3e`, `23717e5`, `ad5533d`, `b0fb375`, `beaf662`.

### 2.3 Prompt Evaluation Service — Complete

- **Scope:** Backend evaluation with content-addressed deduplication and crash recovery.
- **Sessions:** (part of 2.1/2.2 work)
- **Dependencies:** 1.1
- **PRD Ref:** P0.1
- **Deliverables:**
  - `api/services/prompt_eval.py` — `backend_reranker_eval()`, `evaluate_prompt_batch()`, `compute_accuracy()`, `eval_content_hash()`, incremental writes, partial-run resume
- **Completed:** Delivered as part of campaign notebook and grid search work.

### 2.4 Prompt Optimizer Service — Complete

- **Scope:** LLM-driven candidate generation, winner selection, and suggestion generation.
- **Sessions:** (part of 2.1 work)
- **Dependencies:** 1.1
- **PRD Ref:** P0.2, P0.3
- **Deliverables:**
  - `api/services/prompt_optimizer.py` — `generate_candidates()`, `select_round_winner()`, `generate_suggestions()`, `save_campaign_winner()`
- **Completed:** Delivered as part of campaign notebook work.

### 2.5 Backend-Only Evaluation — Complete

- **Scope:** Remove local evaluation fallback. Backend evaluation via `/matches` with `ranking_prompt` is the only evaluation path.
- **Sessions:** (part of ongoing work)
- **Dependencies:** 2.3
- **PRD Ref:** P0.1
- **Completed:** `82157ef feat: remove local evaluation path, backend-only evaluation`

### 2.6 Per-Point Query Sampling — Complete

- **Scope:** Centralize per-point query sampling logic for grid search. `resolve_point_evals()` ensures deterministic query assignment and stable content hashes.
- **Sessions:** 1
- **Dependencies:** 2.2
- **PRD Ref:** P0.6
- **Completed:** `fcd6ae6 feat: centralize per-point query sampling via resolve_point_evals()`

### 2.7 Spec Rewrite v0.6.0 — Complete

- **Scope:** Complete rewrite of all 5 spec documents to match actual codebase state.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** —

### 2.8 TermNorm `GET /pipeline` Endpoint — Complete

- **Scope:** Add `GET /pipeline` endpoint to TermNorm backend returning pipeline topology and tunable parameter schema.
- **Sessions:** 1
- **Dependencies:** —
- **PRD Ref:** P1.5
- **Repo:** `TermNorm-excel/backend-api` (external)
- **Completed:** Endpoint implemented and working in TermNorm backend.

### 2.9 Discovery Protocol Integration — Complete

- **Scope:** Integrate `GET /pipeline` into PromptPotter. Use discovered schema for grid search config validation and parameter passthrough.
- **Sessions:** 1
- **Dependencies:** 2.8, 2.2
- **PRD Ref:** P1.5
- **Completed:** Discovery protocol integrated and working.

**Phase 2 exit criteria:** Grid search and optimization services are functional (prototype quality). HITL campaign notebook runs end-to-end. Code quality audit complete. Services are tested via PromptState and ProjectStore tests.

---

## Phase 3: Workflow Engine Migration (M3)

Phase 3 migrates the service-based optimizer into the existing workflow engine as proper nodes, enabling API-driven orchestration and iterative feedback cycling.

### 3.0 Promote Campaign Library to Services

- **Scope:** Move orchestration logic from `notebooks/_campaign_lib.py` into `api/services/`. Target: `evaluate_prompt()` dedup/resume/finalize → `prompt_eval.py`, grid pre-scan + `resume_or_build_grid()` plan lifecycle → `grid_search.py`, `run_or_load_replay()` execution reuse → `backend_client.py`, `init_services()` → new or existing service module. `_campaign_lib.py` retains only tqdm/IPython/print wrappers that delegate to services.
- **Sessions:** 1
- **Dependencies:** 2.7
- **PRD Ref:** P0.1, P0.6
- **Rationale:** This logic is broadly useful beyond notebooks and should not be gated behind a notebook import. Unblocks clean node wrapping in 3.1.
- **Done when:**
  - All business logic (dedup decisions, partial resume, plan lifecycle) lives in `api/services/`
  - `_campaign_lib.py` contains only UI formatting (tqdm, print, IPython display) + delegation
  - All existing tests still pass
  - Notebook runs identically

### 3.1 Implement Optimizer Nodes

- **Scope:** Create three optimization nodes wrapping existing service logic:
  - **InitNode** — wraps `restructure_context()` to produce initial PROMPT_STATE from context
  - **GrowFilterNode** — wraps `generate_candidates()` to produce N variant PROMPT_STATEs
  - **AnalysisEvalNode** — wraps `evaluate_prompt_batch()` + `generate_suggestions()` to score and analyze. **Note:** AnalysisEvalNode should route evaluation through the existing evaluator framework (`api/evaluators/`) rather than reimplementing scoring inline. This closes the architectural gap between the M2 inline `==` check and the target evaluator registry pattern.
- **Sessions:** 2
- **Dependencies:** 3.0
- **PRD Ref:** P1.1
- **Done when:**
  - All three nodes are registered in `api/nodes/`
  - Each node follows the existing node pattern (typed inputs/outputs, Pydantic models)
  - Nodes are thin wrappers — existing service logic is reused, not reimplemented
  - AnalysisEvalNode uses the evaluator framework for scoring
  - Unit tests verify each node independently with mock inputs

### 3.2 Optimization Workflow Definition

- **Scope:** Create a CWL-style workflow definition wiring optimizer nodes into a linear pipeline: InitNode → GrowFilterNode → AnalysisEvalNode → output.
- **Sessions:** 1
- **Dependencies:** 3.1
- **PRD Ref:** P1.1
- **Done when:**
  - Workflow definition runs end-to-end in the existing workflow runner
  - Produces scored PROMPT_STATEs with full lineage
  - Linear mode (single pass, N independent runs) works

### 3.3 Campaign Registry

- **Scope:** Formal campaign/trial persistence with Langfuse/MLflow-compatible data structure. Campaign directories with metadata, trial JSONL results, lineage tracking, API endpoints for list/detail/export.
- **Sessions:** 2
- **Dependencies:** 3.2
- **PRD Ref:** P1.3
- **Done when:**
  - Campaigns persist to `.promptpotter/campaigns/` directory structure
  - Registry hierarchy: Campaign → Trial → PROMPT_STATE + scores
  - List/detail/export API endpoints work
  - Full lineage is reconstructable from registry files
  - Data format compatible with Langfuse trace IDs and MLflow run IDs

### 3.4 Feedback Cycling

- **Scope:** Add 3-path feedback routing (generate / refine context / modify plan) via workflow engine. Counter-based iteration with configurable limit. Analysis node produces `next_action` decision.
- **Sessions:** 2
- **Dependencies:** 3.2
- **PRD Ref:** P1.2
- **Done when:**
  - Switch node routes to three feedback paths based on `next_action`
  - Counter-based stopping works with configurable threshold
  - Integration test demonstrates multi-cycle optimization

### 3.5 E2E Optimization Test

- **Scope:** Integration test running the optimization workflow against a sample dataset. Verifies workflow execution, prompt state quality, lineage, and campaign registry persistence.
- **Sessions:** 1
- **Dependencies:** 3.2, 3.3
- **PRD Ref:** P1.1, P1.3
- **Done when:**
  - Test runs optimization workflow and verifies output structure
  - Test verifies campaign registry persistence
  - Test completes within CI time limits

### 3.6 Langfuse Integration

- **Scope:** Extend Langfuse wrapper from stubs to full per-trial tracing. Each eval round creates a trace with accuracy scores. Campaign-level grouping.
- **Sessions:** 1
- **Dependencies:** 3.2
- **PRD Ref:** P1.4
- **Done when:**
  - Every evaluation round has a Langfuse trace with scores
  - Campaign traces are grouped with parent-child relationships
  - Tests verify trace emission (mocked)

**Phase 3 exit criteria:** Optimization workflow runs end-to-end in the workflow engine. Campaign registry persists trials with Langfuse/MLflow-compatible structure. Feedback cycling routes correctly. E2E test passes in CI. Langfuse traces are emitted for all trials.

---

## Phase 4: Integration and Polish (M4)

### 4.1 TermNorm Variant Comparison (SC5)

- **Scope:** Run Variant A vs Variant B comparison on BC5CDR 500-term subset. Optimize `llm_ranking` prompt. Produce recommendation.
- **Sessions:** 2
- **Dependencies:** 3.2
- **PRD Ref:** SC5
- **Done when:**
  - Variant A baseline established
  - Optimization campaign improves `llm_ranking` prompt
  - Clear recommendation: is LLM2 worth it?

### 4.2 Streamlit Dashboard

- **Scope:** Campaign browser (score trajectories), trial comparison (structured diffs), dataset explorer (per-item scores).
- **Sessions:** 2
- **Dependencies:** 3.3
- **PRD Ref:** P2.3
- **Done when:**
  - Campaign browser lists campaigns with score trajectory chart
  - Trial comparison shows diffs and score deltas
  - Reads from campaign registry, no extra data store

### 4.3 Docker Compose Update

- **Scope:** Docker Compose for optimizer + Langfuse with env var passthrough and health checks.
- **Sessions:** 1
- **Dependencies:** 3.2
- **PRD Ref:** —

### 4.4 Documentation Update

- **Scope:** README, notebooks, docs updated to reflect complete optimization workflow.
- **Sessions:** 1
- **Dependencies:** 3.5
- **PRD Ref:** —

**Phase 4 exit criteria:** Variant A vs B comparison completes with clear recommendation. Dashboard shows campaigns. Docker works.

---

## Session Summary

| Phase | Packages | Sessions |
|-------|:--------:|:--------:|
| M0: Specifications | 6 | 6 |
| M1: Foundation | 8 | 8 |
| M2: Core Optimizer | 9 | 9 |
| M3: Workflow Engine Migration | 7 | 10 |
| M4: Integration and Polish | 4 | 6 |
| **Total** | **34** | **~38** |

---

## Dependency Graph

```
M0 (0.1-0.6)
 |
 v
M1 Foundation (Complete)
 |-- 1.1 PROMPT_STATE ----+
 |-- 1.2 Fixtures -----+  |
 |   |-- 1.3 Eval Tests |  |
 |   |-- 1.4 WF Tests   |  |
 |       |                  |
 |       +-- 1.5 CI         |
 |            |              |
 |            +-- 1.6 Docs   |
 |-- 1.7 Ablation <-- 1.1   |
 |    |                      |
 |    +-- 1.8 Params         |
 |                           |
 v                           v
M2 Core Optimizer (Complete)
 |-- 2.1 Campaign Notebook <-- 1.1 .......... [Complete]
 |-- 2.2 Grid Search <-- 1.1, 2.1 .......... [Complete]
 |-- 2.3 Prompt Eval <-- 1.1 ............... [Complete]
 |-- 2.4 Prompt Optimizer <-- 1.1 .......... [Complete]
 |-- 2.5 Backend-Only Eval <-- 2.3 ......... [Complete]
 |-- 2.6 Per-Point Sampling <-- 2.2 ........ [Complete]
 |-- 2.7 Spec Rewrite ...................... [Complete]
 |-- 2.8 TermNorm GET /pipeline (external) . [Complete]
 |    |
 |    +-- 2.9 Discovery Integration ........ [Complete]
 |
 v
M3 Workflow Engine Migration
 |-- 3.0 Promote Campaign Lib <-- 2.7
 |    |
 |    +-- 3.1 Optimizer Nodes (x2) <-- 3.0
 |         |
 |         +-- 3.2 Workflow Definition <-- 3.1
 |         |
 |         +-- 3.3 Campaign Registry (x2) <-- 3.2
 |         |    |
 |         |    +-- 3.5 E2E Test <-- 3.2, 3.3
 |         |
 |         +-- 3.4 Feedback Cycling (x2) <-- 3.2
 |         |
 |         +-- 3.6 Langfuse <-- 3.2
 |
 v
M4 Integration and Polish
 |-- 4.1 TermNorm Variant Comparison (x2) <-- 3.2
 |-- 4.2 Streamlit Dashboard (x2) <-- 3.3
 |-- 4.3 Docker <-- 3.2
 |-- 4.4 Docs <-- 3.5
```

---

## PRD Coverage

| PRD Req | Work Packages | Phase |
|---------|--------------|-------|
| P0.1 Backend Evaluation | 2.3, 2.5, 3.0 | M2, M3 |
| P0.2 Failure Analysis | 2.4 | M2 |
| P0.3 Candidate Generation | 2.1, 2.4 | M2 |
| P0.4 Optimization Loop | 2.1 | M2 |
| P0.5 PROMPT_STATE Tracking | 1.1 | M1 |
| P0.6 Grid Search | 2.2, 2.6, 3.0 | M2, M3 |
| P1.1 Workflow Engine Migration | 3.1, 3.2 | M3 |
| P1.2 Feedback Cycling | 3.4 | M3 |
| P1.3 Campaign Registry | 3.3, 3.5 | M3 |
| P1.4 Langfuse Integration | 3.6 | M3 |
| P1.5 Discovery Protocol | 2.8, 2.9 | M2 (Complete) |
| P1.6 Ablation Comparison | 1.7 | M1 |
| P1.7 Parameter Passthrough | 1.8 | M1 |
| P2.3 Streamlit Dashboard | 4.2 | M4 |
| SC5 TermNorm Validation | 4.1 | M4 |
