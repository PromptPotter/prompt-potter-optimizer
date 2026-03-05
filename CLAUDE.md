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

1. **FastAPI API** (`api/main.py`) — REST endpoints at `/api/v1/`. Routers: `backends`, `campaigns`, `health`, `workflows`.
2. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` is the **primary working interface** at this stage. Uses `notebooks/_campaign_lib.py`, a thin wrapper that delegates to `api/services/` and adds tqdm progress bars + IPython display.

All core logic lives in `api/services/`. The notebook library (`_campaign_lib.py`) never implements business logic — it only wraps service functions with UI output. `tests/test_e2e_optimization.py` is the testable E2E proxy for the notebook workflow.

### Service layer (`api/services/`)

See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for the full service catalog, evaluation gateway, pipeline discovery, store layout, and conventions.

### Data model

**SearchPoint** bundles `PromptState` + `model` + `temperature` + `pipeline_params` — the four dimensions that fully specify one evaluation point. **PipelineSchema** defines the backend pipeline being targeted. Together they parameterize every optimization service: `f(SearchPoint, PipelineSchema, eval_data) → scores`. See [`api/models/CLAUDE.md`](api/models/CLAUDE.md) for field details and API.

### Pipeline discovery

`GET /backends/{id}/pipeline` returns a dynamic view of the backend's pipeline config (via `compute_pipeline_view()`) combined with local workflow nodes. Uses a 30s TTL cache. Falls back to `TERMNORM_DEFAULT_SCHEMA` when the backend is unreachable.

### Pipeline composability

PromptPotter controls backend pipeline behavior through **`node_overrides`** — structured per-node override dicts that mirror the backend's `GET /pipeline` config shape. `run_match()` translates internal flat param names (e.g. `ranking_temperature`) to `node_overrides` format (e.g. `{"llm_ranking": {"temperature": 0.5}}`) at the HTTP boundary. Backends only accept `node_overrides`.

Each LLM node supports `prompt`, `output_schema`, and `model` overrides. See [`docs/connectors/termnorm.md`](docs/connectors/termnorm.md) for the full key mapping.

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

0. **Pipeline snapshot** — call `backend_client.fetch_pipeline()` and display the full JSON. This ensures every experiment run has its pipeline parameters recorded inline in the notebook output.
1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan and/or grid search map the accuracy landscape; results are persisted as `dataset_runs` keyed by content hash
3. **Optimize** — feedback cycle (LLM candidate generation → backend evaluation → winner selection) runs iteratively until the human stops it
4. **Harvest** — the human reviews results. All eval data is already stored in `dataset_runs` via `evaluate_prompt_cached`
5. **Reuse** — coverage advisor discovers all stored `dataset_runs` regardless of source. Fresh optimization starts from higher ground.

**Key principle:** Every backend evaluation writes to the same `dataset_runs` store with content-addressed deduplication. No data is siloed per campaign.

### Evaluation flow

All evaluation paths (grid search, smart search, feedback cycle) converge on `evaluate_prompt_cached()` — the single gateway for eval persistence with content-addressed deduplication. See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for details.

### Workflow engine scaffold

CWL-inspired workflow engine (`api/core/`, `api/nodes/`). The node base classes and optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode) are actively used by the feedback cycle. The YAML-driven WorkflowRunner and REST endpoints are scaffold for future migration. See [`api/core/CLAUDE.md`](api/core/CLAUDE.md).

### Milestones

Each milestone has an executable spec in `docs/specs/`. See [`docs/specs/CLAUDE.md`](docs/specs/CLAUDE.md) for the process and milestone table.

### TermNorm reference patterns

The TermNorm repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`. See its `CLAUDE.md` for reference implementations (Langfuse, MLflow, prompt registry).

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **Version**: Centralized in `api/config/settings.py` as `APP_VERSION`
- **API versioning**: all endpoints under `/api/v1/`
- **Pipeline reproducibility**: The notebook MUST display the full pipeline configuration (all node configs, models, temperatures, schemas) via `GET /pipeline` before any evaluation. This is the experiment's parameter manifest — never strip it to just step names. The scan advisor also reads this config to make informed recommendations.
