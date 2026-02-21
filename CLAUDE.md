# CLAUDE.md

## Project Overview

PromptPotter Optimizer is an API-first prompt optimization service that connects to backends like TermNorm. It syncs experiment data, replays pipelines with different configurations, and computes statistical comparisons — delivered as both a FastAPI REST service and Jupyter notebooks.

**Core Philosophy**: Framework-agnostic, Pydantic I/O, dual-mode delivery (notebooks + REST API).

## Commands

```bash
# Install
pip install -r requirements.txt

# Run API server
uvicorn api.main:app --reload --port 8001

# Run tests
pytest

# Docker
cd docker && docker-compose up --build
```

## Architecture

```
api/
├── main.py                      # FastAPI entry, router mounting
├── config/settings.py           # Pydantic BaseSettings
├── models/
│   ├── backend.py               # BackendConnection, Execution, ExecutionResultItem
│   ├── prompt_state.py          # PromptState (3-layer, immutable, versioned)
│   └── workflow.py              # WorkflowDefinition, StepDefinition
├── routers/
│   ├── backends.py              # /backends/* — connect, sync, execute, compare
│   ├── workflows.py             # /workflows/* — execute, evaluate
│   └── health.py                # /health, /ready
├── services/
│   ├── prompt_eval.py           # Prompt evaluation (baseline extraction, batch eval)
│   ├── prompt_optimizer.py      # Candidate generation, selection, suggestions, save
│   ├── grid_search.py           # Grid search over prompt component axes
│   ├── project_store.py         # File I/O for .promptpotter/projects/
│   ├── backend_client.py        # HTTP client for backend APIs (TermNorm)
│   ├── comparison.py            # Statistical comparison (hit@k, McNemar, Wilcoxon)
│   ├── llm_client.py            # OpenAI/Anthropic/Groq abstraction
│   └── langfuse_client.py       # Langfuse integration
├── core/
│   └── workflow_runner.py       # DAG execution engine
├── nodes/                       # Composable workflow nodes (LLM, PipelineConfig, Ranker)
└── evaluators/                  # ExactMatch, CriteriaEvaluator (LLM-judge)

notebooks/
├── termnorm_backend.ipynb       # Exploration: register → sync → replay → compare
├── optimization_campaign.ipynb  # Optimization: eval → grid search → optimize → save
└── _campaign_lib.py             # Notebook helper (thin wrapper over api/services/)
docs/
├── specs/                       # Formal specs (project-charter, PRD, ADD, WBS, roadmap)
├── connectors/                  # Backend connector contracts (termnorm.md)
├── user-guide.md                # Setup, workflows, configuration reference
└── *.md                         # Design docs (registry-design, literature-review, etc.)
tests/                           # pytest suite
docker/                          # Dockerfile, docker-compose.yml
├── apps/                        # Streamlit UIs (secrets_manager)
└── launcher/                    # JupyterLab launcher config
scripts/                         # Utilities (sync_termnorm_to_langfuse.py)
workflows/examples/              # CWL-inspired YAML workflow definitions
```

## Project Store Layout

```
.promptpotter/projects/
  {backend_id}/
    backend.json                 # Connection config
    sync/
      experiments.json           # GET /experiments (verbatim)
      experiments/{id}.json      # GET /experiments/{id}/mappings (verbatim)
      optimization/              # Saved PromptState winners from optimization runs
    executions/
      {execution_id}.json        # Replay results (with pipeline_data per query)
```

## Key Endpoints

**Backends (primary workflow):**
- `POST /api/v1/backends` — Register backend connection
- `POST /api/v1/backends/{id}/sync` — Sync experiments from backend
- `GET  /api/v1/backends/{id}/experiments` — List synced experiments
- `POST /api/v1/backends/{id}/execute` — Replay pipeline via backend
- `GET  /api/v1/backends/{id}/compare/{exec_id}` — Statistical comparison

**Other:**
- `GET /api/v1/health` / `GET /api/v1/ready`
- `POST /api/v1/workflows/execute` / `POST /api/v1/workflows/evaluate`

## Key Models

- **`PromptState`** — Immutable, versioned prompt snapshot organized into three optimization layers: **Layer 1 (Generate)** structured prompt components (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples`), **Layer 2 (Refine Context)** optimization context and hypervariables (`context`, `parameters`), **Layer 3 (Modify Plan)** optimization strategy (`plan`). Includes `render()` to assemble Layer 1 fields into a prompt string, and `derive()` for creating children. Forms a lineage chain via `parent_id`. `LAYER_FIELDS` maps layer names to their fields.
- **`OptimizationDefaults`** — Layer 3 strategy defaults (n_variants, creativity, selection_strategy, improvement_threshold, max_iterations, etc.). Should rarely need changing.
- **`ExecutionResultItem`** — Per-query result from a replay. Includes `pipeline_data` dict which stores the full backend response (entity_profile, token_matched_candidates, etc.) for local optimization.
- **`Execution`** — A complete replay run containing a list of `ExecutionResultItem`s.

## Key Services

- **`prompt_eval`** — `extract_baseline_prompt()`, `filter_eval_data()`, `local_reranker_eval()`, `evaluate_prompt_batch()`, `compute_accuracy()`. All LLM calls use `LLMClientBase`.
- **`prompt_optimizer`** — `generate_candidates()`, `select_round_winner()`, `generate_suggestions()`, `save_campaign_winner()`. All LLM calls use `LLMClientBase`.
- **`grid_search`** — `validate_grid_config()`, `build_grid_combinations()`, `restructure_context()`, `run_grid_search()`, `analyze_grid_results()`, `select_grid_winner()`, `load_eval_dataset()`. Constants: `DEFAULT_GRID_AXES`, `GRID_SEARCHABLE_FIELDS`, `REQUIRED_TEMPLATE_VARS`.
- **`_campaign_lib.py`** — Thin notebook-facing wrapper over the services above. Adds tqdm, print, IPython.display. Preserves legacy `(eval_llm, api_key)` signatures via `_make_llm_client()` adapter.

## Configuration

Environment variables via `.env`:
- `GROQ_API_KEY` — Groq API key (primary LLM provider)
- `LLM_PROVIDER` — LLM provider: `groq`, `openai`, or `anthropic` (default: `groq`)
- `LLM_MODEL` — Model identifier (default: `meta-llama/llama-4-maverick-17b-128e-instruct`)
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — Alternative LLM provider keys
- `MAX_ITERATIONS` — Optimization iteration limit (default: 5)

## Conventions

- **Commit style**: Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, etc.)
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Branch**: Active development on `feat/m2-core-optimizer`

## Backend Connectors

Connector contracts are documented in `docs/connectors/`. Currently supported:
- **TermNorm** (`docs/connectors/termnorm.md`) — Terminology normalization pipeline with entity profiling, token matching, and LLM ranking stages.

## Testing

```bash
# Run all tests
pytest -v

# Run with short tracebacks
pytest -v --tb=short

# Lint
ruff check api/ tests/
```

**Test files:**
- `tests/test_api.py` — FastAPI endpoint tests (health, readiness)
- `tests/test_prompt_state.py` — PromptState immutability and lineage
- `tests/test_incremental_writes.py` — ProjectStore append/finalize
- `tests/test_evaluators.py` — ExactMatch, CriteriaEvaluator, registry aliases
- `tests/test_workflow_runner.py` — DAG sort, input resolution, execution
- `tests/test_prompt_eval.py` — Baseline extraction, filter, batch eval, accuracy
- `tests/test_prompt_optimizer.py` — Candidate generation, selection, suggestions, save
- `tests/test_grid_search.py` — Grid validation, combinations (legacy import path)
- `tests/test_grid_search_service.py` — Full grid search service tests (restructure, run, analyze)

**Fixtures** (`tests/conftest.py`): `mock_llm_client`, `tmp_store`, auto-reset Langfuse singleton.

## Milestone Status

**M1 (Foundation)**: Complete — PromptState, ProjectStore, backends, replay, comparison, evaluators, workflow runner, tests, CI.
**M2 (Core Optimizer)**: In progress — 3-layer PromptState restructured, HITL optimization campaign notebook (WP 2.8), `_campaign_lib.py` extraction + refactor into services (prompt_eval, prompt_optimizer, grid_search), grid search for initial condition exploration (WP 2.9). Next: REST API optimization endpoints, automated optimization loop.

## External References

The `external/` directory is gitignored and contains reference clones (like TermNorm-excel) for documentation purposes only.
