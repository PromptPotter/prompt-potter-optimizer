# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PromptPotter Optimizer is an API-first prompt optimization service that connects to backends like TermNorm. It syncs experiment data, replays pipelines with different configurations, and computes statistical comparisons — delivered as both a FastAPI REST service and Jupyter notebooks.

**Core Philosophy**: Framework-agnostic, Pydantic I/O, dual-mode delivery (notebooks + REST API).

## Commands

```bash
# Development
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8001

# Testing
pytest

# Docker
cd docker && docker-compose up --build

# Streamlit
streamlit run apps/optimizer_client.py
```

## Architecture

```
api/
├── main.py                     # FastAPI entry, router mounting
├── config/settings.py          # Pydantic BaseSettings
├── models/
│   ├── backend.py              # BackendConnection, Execution, ExecutionResultItem
│   ├── prompt_state.py         # PromptState (immutable optimization state)
│   └── workflow.py             # WorkflowDefinition, StepDefinition
├── routers/
│   ├── backends.py             # /backends/* — connect, sync, execute, compare
│   ├── workflows.py            # /workflows/* — execute, evaluate
│   └── health.py, optimize.py
├── services/
│   ├── project_store.py        # File I/O for .promptpotter/projects/
│   ├── backend_client.py       # HTTP client for backend APIs (TermNorm)
│   ├── comparison.py           # Statistical comparison (hit@k, McNemar, Wilcoxon)
│   └── llm_client.py           # OpenAI/Anthropic abstraction
├── core/
│   ├── workflow_runner.py      # DAG execution engine
│   └── optimizer.py            # Legacy optimizer (placeholder, replaced in M2)
├── nodes/                      # Composable workflow nodes (LLM, WebSearch, Ranker)
└── evaluators/                 # ExactMatch, CriteriaEvaluator (LLM-judge)

notebooks/
├── termnorm_backend.ipynb      # Register → sync → replay → compare (no server needed)

workflows/examples/             # CWL-inspired YAML workflow definitions
apps/                           # Streamlit UIs
docker/                         # Dockerfile, docker-compose.yml
tests/                          # pytest suite
docs/                           # Design docs + specs
```

## Project Store Layout

```
.promptpotter/projects/
  {backend_id}/
    backend.json                # Connection config
    sync/
      experiments.json          # GET /experiments (verbatim)
      experiments/{id}.json     # GET /experiments/{id}/mappings (verbatim)
    executions/
      {execution_id}.json       # Replay results
```

## Key Endpoints

**Backends (primary workflow):**
- `POST /api/v1/backends` — Register backend connection
- `POST /api/v1/backends/{id}/sync` — Sync experiments from backend
- `GET /api/v1/backends/{id}/experiments` — List synced experiments
- `POST /api/v1/backends/{id}/execute` — Replay pipeline via backend
- `GET /api/v1/backends/{id}/compare/{exec_id}` — Statistical comparison

**Other:**
- `GET /api/v1/health` / `GET /api/v1/ready`
- `POST /api/v1/workflows/execute` / `POST /api/v1/workflows/evaluate`

## Configuration

Environment variables via `.env`:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — LLM provider keys
- `DEFAULT_MODEL` — Fallback model (default: gpt-4)
- `MAX_ITERATIONS` — Optimization iteration limit (default: 5)

## Specifications

Formal specs in `docs/specs/`: project-charter, PRD, ADD, WBS, roadmap.
Design docs in `docs/`: literature-review, registry-design, architecture.

## Current State

- **Backend storage**: Working end-to-end — register, sync, replay, compare
- **PromptState model**: Implemented (`api/models/prompt_state.py`)
- **Workflow system**: LLMNode, WebSearchNode (mock), RankerNode
- **Evaluators**: ExactMatchEvaluator, CriteriaEvaluator (LLM-judge)

## External References

The `external/` directory is gitignored and contains reference clones (like TermNorm-excel) for documentation purposes only.
