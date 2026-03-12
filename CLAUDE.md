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
2. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` is the **primary working interface** at this stage. Uses `notebooks/_campaign_lib/`, a package of 6 submodules (`_setup`, `_eval`, `_grid`, `_search`, `_optimize`, `_display`) that wraps services with tqdm progress bars + IPython display.

All core logic lives in `api/services/`. The notebook library (`_campaign_lib/`) never implements business logic — it only wraps service functions with UI output. `tests/test_e2e_optimization.py` is the testable E2E proxy for the notebook workflow.

### Service layer (`api/services/`)

See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for the full service catalog, evaluation gateway, pipeline discovery, store layout, and conventions.

### Data model

**SearchPoint** bundles `PromptState` + `model` + `temperature` + `pipeline_params` — the four dimensions that fully specify one evaluation point. **PipelineSchema** defines the backend pipeline being targeted. Together they parameterize every optimization service: `f(SearchPoint, PipelineSchema, eval_data) → scores`. See [`api/models/CLAUDE.md`](api/models/CLAUDE.md) for field details and API.

### Prompt decomposition & alias groups

Two core mechanisms that work together (both actively evolving):

- **Prompt decomposition** — PromptPotter decomposes a backend's monolithic prompt into internal fields (`persona`, `task_intent`, `thinking_style`, `answer_format`, `problem_description`) via LLM restructure. A variant library (`api/config/prompt_variants.json`) provides per-field alternatives for scan and grid search, including building blocks from published research (e.g. PromptWizard's thinking styles). Each variant is an object with `text` + provenance metadata (`source`, `year`); `load_variant_library()` extracts flat strings for consumers, `load_variant_library_rich()` preserves metadata.
- **Prompt alias groups** — `register_alias` / `resolve_aliases` link semantically equivalent prompt hashes (e.g. original monolithic ↔ restructured decomposed form) so historical evaluations are discoverable across forms. Resolution is transitive. Used by coverage advisor, scan diagnostics, and `evaluate_prompt_cached()`.

### Pipeline discovery

`GET /backends/{id}/pipeline` returns a dynamic view of the backend's pipeline config (via `compute_pipeline_view()`) combined with local workflow nodes. Uses a 30s TTL cache. Falls back to `TERMNORM_DEFAULT_SCHEMA` when the backend is unreachable.

### Pipeline composability

PromptPotter uses **`node_config`** format throughout — the same nested dict shape as `pipeline.json` and the `/matches` wire format (e.g. `{"llm_ranking": {"temperature": 0.5}}`). No flat param names, no translation layer. `run_match()` forwards `node_config` as-is to the backend.

Each LLM node supports `prompt`, `output_schema`, and `model` overrides. See [`docs/connectors/termnorm.md`](docs/connectors/termnorm.md) for the key mapping.

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

0. **Pipeline snapshot** — call `backend_client.fetch_pipeline()` and display the full JSON. This ensures every experiment run has its pipeline parameters recorded inline in the notebook output.
1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan (5 cells: scan advisor → edit variants → prepare scan baseline → sensitivity scan → select winner) and/or grid search map the accuracy landscape; results are persisted as `dataset_runs` keyed by content hash. The scan baseline is created via LLM restructure to decompose the backend's monolithic prompt into PromptPotter's internal fields (persona, task_intent, etc.) for independent perturbation. `sensitivity_scan()` takes a `SearchPoint` + flat `scan_variants` dict and runs OAT over all eval_data. Scan variant coverage diagnostic (`diagnose_scan_variants()`) checks historical data via prompt alias groups before evaluation to report per-axis coverage.
3. **Optimize** — feedback cycle (LLM candidate generation → backend evaluation → winner selection) runs iteratively until the human stops it
4. **Harvest** — the human reviews results. All eval data is already stored in `dataset_runs` via `evaluate_prompt_cached`
5. **Reuse** — coverage advisor discovers all stored `dataset_runs` regardless of source. Fresh optimization starts from higher ground.

**Key principle:** Every backend evaluation writes to the same `dataset_runs` store with content-addressed deduplication. No data is siloed per campaign.

### Evaluation flow

All evaluation paths (grid search, smart search, feedback cycle) converge on `evaluate_prompt_cached()` — the single gateway for eval persistence with content-addressed deduplication. Prompt alias groups link semantically equivalent prompts (original vs restructured) so historical data is discoverable across forms. See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for details.

### Workflow engine scaffold

CWL-inspired workflow engine (`api/core/`, `api/nodes/`). The node base classes and optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode) are actively used by the feedback cycle. The YAML-driven WorkflowRunner and REST endpoints are scaffold for future migration. See [`api/core/CLAUDE.md`](api/core/CLAUDE.md).

### Milestones

Each milestone has an executable spec in `docs/specs/`. See [`docs/specs/CLAUDE.md`](docs/specs/CLAUDE.md) for the process and milestone table.

### TermNorm reference patterns

The TermNorm repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`. See its `CLAUDE.md` for reference implementations (Langfuse, MLflow, prompt registry).

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims, no dual-format readers.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **Version**: Centralized in `api/config/settings.py` as `APP_VERSION`
- **API versioning**: all endpoints under `/api/v1/`
- **`sample_size`**: Universal eval sampling parameter across all services (0 = use all). No synonyms (`max_queries`, `eval_queries_per_point`, etc.).
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields. Surfaces schema violations immediately.
- **Circuit breaker**: Scan evaluation aborts on baseline all-errors or 2 consecutive all-error variants.
- **Session resilience**: `BackendClient` auto-reinits on 400 (backend restart recovery).
- **Pipeline reproducibility**: The notebook MUST display the full pipeline configuration (all node configs, models, temperatures, schemas) via `GET /pipeline` before any evaluation. This is the experiment's parameter manifest — never strip it to just step names. The scan advisor also reads this config to make informed recommendations.

See [`docs/design-principles.md`](docs/design-principles.md) for the full principles catalog with rationale.
