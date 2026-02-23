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
| `project_store.py` | File I/O for `.promptpotter/projects/` — backends, synced experiments, executions, dataset runs, grid plans. |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI). Global singleton via `get_llm_client()`. |
| `comparison.py` | Statistical comparison (hit@k, McNemar, Wilcoxon). |
| `langfuse_client.py` | Langfuse observability integration. |

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
  dataset_runs/{run_id}.json       # completed eval runs
  dataset_runs/{run_id}.partial.jsonl  # in-progress (crash recovery)
  dataset_runs.json                # index of all runs
  grid_plans/{plan_id}.json        # persisted grid search plans (resume on restart)
```

### Evaluation flow

Backend evaluation is the primary path: `prompt_eval.backend_reranker_eval()` calls the backend's `POST /matches` with a rendered ranking prompt override, then checks if the top-ranked candidate matches ground truth (exact string match = hit@1).

Grid search (`grid_search.run_grid_search()`) iterates over cartesian products of Layer 1 field variants, evaluating each grid point against the eval dataset via backend calls. Per-point query sampling is supported: `eval_queries_per_point` controls how many queries each point gets, and `shared_queries` controls whether all points share the same query set. Results are deduplicated by content hash.

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **API versioning**: all endpoints under `/api/v1/`
