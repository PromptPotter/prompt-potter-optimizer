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

1. **FastAPI API** (`api/main.py`) — REST endpoints at `/api/v1/`. Routers: `backends` (connect, sync, execute, compare), `workflows`, `health`.
2. **Jupyter notebooks** — `notebooks/termnorm_backend.ipynb` (exploration) and `notebooks/optimization_campaign.ipynb` (optimization). Both use `notebooks/_campaign_lib.py`, a thin wrapper that delegates to `api/services/` and adds tqdm progress bars + IPython display.

All core logic lives in `api/services/`. The notebook library (`_campaign_lib.py`) never implements business logic — it only wraps service functions with UI output.

### Service layer (`api/services/`)

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint. Content-addressed deduplication via `eval_content_hash()`. Incremental writes (`.partial.jsonl`) for crash recovery. |
| `grid_search.py` | Grid search over Layer 1 prompt fields. Distance-weighted stratified sampling. LLM-assisted context restructuring and result analysis. Grid plan persistence (`grid_plan_identity()`, `serialize_grid_plan()`, `deserialize_grid_plan()`). |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, round winner selection, improvement suggestions. |
| `backend_client.py` | HTTP client for backend APIs (sync experiments, replay queries, init sessions). |
| `project_store.py` | Facade over focused store modules in `stores/`. File I/O for `.promptpotter/projects/`. |
| `feedback_cycle.py` | Iterative optimization orchestrator: `CycleConfig` → `GrowFilterNode` → `AnalysisEvalNode` loop with patience-based stopping, 3-path routing (`generate`/`refine_context`/`modify_plan`/`stop`). |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification. |
| `search/grid_core.py` | Grid search evaluation engine. Skips `init_session` when all points are cached. |
| `search/coverage.py` | Historical index (`build_prompt_result_index`) and coverage advisor. Discovers all stored `dataset_runs` for reuse across optimization threads. |
| `stores/` | Focused store modules: `BackendStore`, `ExecutionStore`, `DatasetRunStore`, `GridPlanStore`, `SmartSearchStore`, `CampaignStore`. Shared I/O in `stores/base.py`. |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with `_OpenAICompatibleClient` base. Global singleton via `get_llm_client()`. |
| `query_utils.py` | Shared query-parsing utilities (e.g. `parse_bom_material()`). |
| `comparison.py` | Statistical comparison (hit@k, McNemar, Wilcoxon). |
| `langfuse_client.py` | Langfuse v2 observability integration. |

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

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **Version**: Centralized in `api/config/settings.py` as `APP_VERSION`
- **API versioning**: all endpoints under `/api/v1/`
