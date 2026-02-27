# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs optimization campaigns (grid search, iterative candidate generation) to improve prompt accuracy. The primary evaluation mode is **backend evaluation** — calling the backend's `/matches` endpoint with `ranking_prompt` overrides.

## Commands

```bash
# Run API server
uvicorn api.main:app --port 8001 --reload

# Run all tests
pytest -v --tb=short

# Run a single test file
pytest tests/test_project_store_evals.py -v

# Run a single test function
pytest tests/test_prompt_state.py::test_create_and_derive -v

# Lint
ruff check api/ tests/

# Docker (JupyterLab + API)
cd docker && docker-compose up --build
```

## Architecture

### Two entry points, shared core

1. **FastAPI API** (`api/main.py`) — REST endpoints at `/api/v1/`. Routers: `backends` (connect, sync, execute, compare), `health`.
2. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` is the **primary working interface** at this stage. Uses `notebooks/_campaign_lib.py`, a thin wrapper that delegates to `api/services/` and adds tqdm progress bars + IPython display.

All core logic lives in `api/services/`. The notebook library (`_campaign_lib.py`) never implements business logic — it only wraps service functions with UI output.

`tests/test_e2e_optimization.py` is the testable E2E proxy for the notebook workflow — critical because Claude Code can't run Jupyter.

### Service layer (`api/services/`)

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint. Content-addressed deduplication via `eval_content_hash()`. Incremental writes (`.partial.jsonl`) for crash recovery. |
| `search/grid_core.py` | Grid search over Layer 1 prompt fields. Distance-weighted stratified sampling. LLM-assisted context restructuring and result analysis. Grid plan persistence (`grid_plan_identity()`, `serialize_grid_plan()`, `deserialize_grid_plan()`). |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, round winner selection, improvement suggestions. |
| `backend_client.py` | HTTP client for backend APIs (sync experiments, replay queries, init sessions). |
| `project_store.py` | Facade over focused store modules in `stores/`. File I/O for `.promptpotter/projects/`. |
| `campaign/feedback_cycle.py` | Iterative optimization orchestrator: `CycleConfig` → `GrowFilterNode` → `AnalysisEvalNode` loop with patience-based stopping, 3-path routing (`generate`/`refine_context`/`modify_plan`/`stop`). |
| `campaign/campaign_init.py` | Campaign initialization: project store setup, backend sync, baseline evaluation. |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification. |
| `search/grid_core.py` | Grid search evaluation engine. Skips `init_session` when all points are cached. |
| `search/coverage.py` | Historical index (`build_prompt_result_index`) and coverage advisor. Discovers all stored `dataset_runs` for reuse across optimization threads. |
| `obs/observability_logger.py` | File-based observability: Langfuse-compatible traces, MLflow experiments, prompt versioning. `events.jsonl` flat nav log. |
| `obs/langfuse_client.py` | Langfuse v2 cloud integration (singleton). |
| `obs/langfuse_backfill.py` | Backfill local obs data to Langfuse cloud. |
| `stores/` | Focused store modules: `BackendStore`, `ExecutionStore`, `DatasetRunStore`, `GridPlanStore`, `SmartSearchStore`, `CampaignStore`. Shared I/O in `stores/base.py`. |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with `_OpenAICompatibleClient` base. Global singleton via `get_llm_client()`. Exponential backoff for transient 503/429 errors. |
| `query_utils.py` | Shared query-parsing utilities (e.g. `parse_bom_material()`). |
| `comparison.py` | Statistical comparison (hit@k, McNemar, Wilcoxon). |

### Data model

**PromptState** (`api/models/prompt_state.py`) — Immutable, versioned prompt configuration organized into 3 optimization layers:
- **Layer 1 (Generate)**: `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples` — change every optimization pass
- **Layer 2 (Refine Context)**: `context`, `parameters` — adjust when Layer 1 stalls
- **Layer 3 (Modify Plan)**: `plan` — rarely changed (strategy defaults)

PromptState is frozen (`model_config = {"frozen": True}`). Use `derive(**changes)` to create children (sets `parent_id` automatically). Use `render()` to assemble Layer 1 fields into a prompt string. Use `diff(a, b)` for structured diffs.

**ProjectStore** layout on disk:
```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  executions/{id}.json
  dataset_runs/{run_id}.json       # completed eval runs (shared across all eval paths)
  dataset_runs/{run_id}.partial.jsonl  # in-progress (crash recovery)
  dataset_runs.json                # index of all runs (content_hash → run_id)
  grid_plans/{plan_id}.json        # persisted grid search plans (resume on restart)
  smart_search_plans/{plan_id}.json # sensitivity scan plans (axis profiles, scan results)
  campaigns/{campaign_id}.json     # campaign metadata + trial results
  obs/
    langfuse/events.jsonl          # flat navigation log (START HERE for data exploration)
    langfuse/traces/{trace_id}.json
    langfuse/scores/{trace_id}.jsonl
    experiments/{campaign_id}/     # MLflow FileStore format (mlflow ui compatible)
    prompts/{family}/{version}/    # prompt versioning (prompt.txt + metadata.json)
```

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan and/or grid search map the accuracy landscape; results are persisted as `dataset_runs` keyed by content hash
3. **Optimize** — feedback cycle (LLM candidate generation → backend evaluation → winner selection) runs iteratively until the human stops it
4. **Harvest** — the human reviews the optimization results and stops the thread. All eval data from the optimization (every candidate evaluated against every query) is already stored in `dataset_runs` via `evaluate_prompt_cached`
5. **Reuse** — sensitivity scan's coverage advisor and historical index automatically discover all stored `dataset_runs`, regardless of which optimization thread produced them. The human can start a brand new optimization from a freshly calculated starting point, benefiting from all previously collected eval data

**Key principle:** Every backend evaluation — whether from grid search, sensitivity scan, or feedback cycle — writes to the same `dataset_runs` store with content-addressed deduplication. This makes all eval data automatically available for future scans and optimizations. No data is siloed per campaign.

### Evaluation flow

Backend evaluation is the primary path: `prompt_eval.backend_reranker_eval()` calls the backend's `POST /matches` with a rendered ranking prompt override, then checks if the top-ranked candidate matches ground truth (exact string match = hit@1).

All evaluation paths converge on `evaluate_prompt_cached()` which handles content-addressed deduplication, incremental `.partial.jsonl` crash recovery, and final result storage. This is the single gateway for persisting eval results.

Grid search (`grid_search.run_grid_search()`) iterates over cartesian products of Layer 1 field variants, evaluating each grid point against the eval dataset via backend calls. Per-point query sampling is supported: `eval_queries_per_point` controls how many queries each point gets, and `shared_queries` controls whether all points share the same query set. Results are deduplicated by content hash.

Smart search (`smart_search.sensitivity_scan()`) measures one-at-a-time axis perturbations against the baseline, classifying axes by sensitivity. The coverage advisor (`coverage.assess_scan_coverage()`) checks existing `dataset_runs` to determine which variants already have enough cached data to skip backend calls.

Feedback cycle (`feedback_cycle.run_feedback_cycle()`) runs iterative optimization rounds: `GrowFilterNode` generates candidates via LLM, `AnalysisEvalNode` evaluates each candidate via `evaluate_prompt_cached()` — so every candidate evaluation is automatically persisted and deduplicated.

### Scaffold (not yet wired)

The following modules are a CWL-inspired workflow engine scaffold — **future architecture, not dead code**:

- `api/core/workflow_runner.py` — YAML-driven workflow executor
- `api/nodes/` — node base class + implementations (`llm_node`, `ranker_node`, `pipeline_config_node`, `optimizer_nodes`)
- `api/evaluators/` — evaluator base + implementations (`exact_match`, `criteria`)
- `api/routers/workflows.py` — REST endpoints for workflow execution
- `api/models/workflow.py` — workflow data models
- `workflows/*.yaml` — workflow definitions

**Migration intent:** Service-layer functions (eval, grid search, scan, feedback cycle) will be wrapped into workflow nodes. The notebook will then drive optimization via `WorkflowRunner` instead of direct service calls. See `docs/specs/roadmap.md` M6.

### How to start a milestone

Each milestone has an executable spec in `docs/specs/`. One Claude Code session = one WBS work package.

**Steps:**
1. Read the milestone spec (`docs/specs/m{N}-*.md`) — scope decisions, deliverables, API sketches
2. Read `docs/specs/wbs.md` to find your work package ID and dependencies
3. Read the service files listed in the deliverables table
4. Check the "Reading list per work package" table in the milestone spec for WP-specific files

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M5: Observability | [`docs/specs/m5-observability.md`](docs/specs/m5-observability.md) | Read TermNorm utils first (`langfuse_logger.py`, `standards_logger.py`, `prompt_registry.py` at `/c/Users/dsacc/OfficeAddinApps/TermNorm-excel/backend-api/utils/`) |
| M6: Workflow Migration | [`docs/specs/m6-workflow-migration.md`](docs/specs/m6-workflow-migration.md) | Read `api/core/workflow_runner.py` and `workflows/optimizer_single_pass.yaml` |
| M7: Multi-Connector | [`docs/specs/m7-multi-connector.md`](docs/specs/m7-multi-connector.md) | Read `docs/connectors/termnorm.md` and `api/services/backend_client.py` |

### TermNorm reference patterns

The TermNorm-excel backend (`/c/Users/dsacc/OfficeAddinApps/TermNorm-excel/backend-api/utils/`) has proven zero-dependency implementations of:

- **Langfuse logging** (`langfuse_logger.py`) — file-based traces/observations/scores
- **MLflow experiment tracking** (`standards_logger.py`) — file-based experiments/runs
- **Prompt registry** (`prompt_registry.py`) — versioned prompt templates with metadata

These are the target patterns for PromptPotter's observability layer (see roadmap M5).

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **Version**: Centralized in `api/config/settings.py` as `APP_VERSION`
- **API versioning**: all endpoints under `/api/v1/`
