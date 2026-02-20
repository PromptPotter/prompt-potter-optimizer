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
│   ├── prompt_state.py          # PromptState (immutable, versioned prompt snapshots)
│   └── workflow.py              # WorkflowDefinition, StepDefinition
├── routers/
│   ├── backends.py              # /backends/* — connect, sync, execute, compare
│   ├── workflows.py             # /workflows/* — execute, evaluate
│   └── health.py                # /health, /ready
├── services/
│   ├── project_store.py         # File I/O for .promptpotter/projects/
│   ├── backend_client.py        # HTTP client for backend APIs (TermNorm)
│   ├── comparison.py            # Statistical comparison (hit@k, McNemar, Wilcoxon)
│   ├── llm_client.py            # OpenAI/Anthropic abstraction
│   └── langfuse_client.py       # Langfuse integration
├── core/
│   └── workflow_runner.py       # DAG execution engine
├── nodes/                       # Composable workflow nodes (LLM, WebSearch, Ranker)
└── evaluators/                  # ExactMatch, CriteriaEvaluator (LLM-judge)

notebooks/
└── termnorm_backend.ipynb       # Full workflow: register → sync → replay → compare → optimize
docs/
├── specs/                       # Formal specs (project-charter, PRD, ADD, WBS, roadmap)
├── connectors/                  # Backend connector contracts (termnorm.md)
└── *.md                         # Design docs (architecture, literature-review, etc.)
tests/                           # pytest suite
docker/                          # Dockerfile, docker-compose.yml
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

- **`PromptState`** — Immutable, versioned prompt snapshot with `derive()` for creating children. Forms a lineage chain via `parent_id`. Used by the DAG-based optimization workflow to track prompt state across iterations.
- **`ExecutionResultItem`** — Per-query result from a replay. Includes `pipeline_data` dict which stores the full backend response (entity_profile, token_matched_candidates, etc.) for local optimization.
- **`Execution`** — A complete replay run containing a list of `ExecutionResultItem`s.

## Configuration

Environment variables via `.env`:
- `GROQ_API_KEY` — Groq API key (primary LLM provider)
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — Alternative LLM provider keys
- `DEFAULT_MODEL` — Fallback model (default: gpt-4)
- `MAX_ITERATIONS` — Optimization iteration limit (default: 5)

## Conventions

- **Commit style**: Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, etc.)
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Branch**: Active development on `feat/m1-foundation`

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

**Fixtures** (`tests/conftest.py`): `mock_llm_client`, `tmp_store`, auto-reset Langfuse singleton.

## Milestone Status

**M1 (Foundation)**: Complete — PromptState, ProjectStore, backends, replay, comparison, evaluators, workflow runner, tests, CI.

## External References

The `external/` directory is gitignored and contains reference clones (like TermNorm-excel) for documentation purposes only.
