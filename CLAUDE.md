# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PromptPotter Optimizer is an API-first prompt optimization service that connects to Langfuse-compatible backends. It iteratively improves prompts through automated analysis and evaluation, delivered as both a FastAPI REST service and JupyterLab interactive environment.

**Core Philosophy**: Framework-agnostic (no LangChain/DSPy lock-in), Pydantic I/O with dependency injection, dual-mode delivery (notebooks + REST API).

## Commands

### Development
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

### Docker
```bash
cd docker
docker-compose up --build
```

### Testing
```bash
pytest                      # Run all tests
pytest tests/test_api.py    # Run specific test file
```

### Streamlit Apps
```bash
streamlit run apps/optimizer_client.py   # Optimization UI
streamlit run apps/secrets_manager.py    # API key configuration
```

## Architecture

```
api/                    # FastAPI application (core optimization engine)
├── main.py            # App entry, CORS, router mounting
├── config/settings.py # Pydantic BaseSettings (env config)
├── models/            # Pydantic request/response schemas
├── routers/           # Endpoint handlers (health, optimize)
├── core/optimizer.py  # PromptOptimizer class (core algorithm)
└── services/          # Future: LLM provider integrations

apps/                   # Streamlit interactive UIs
docker/                 # Dockerfile, docker-compose.yml, entrypoint.sh
examples/               # Jupyter notebooks (quickstart, advanced)
launcher/               # JupyterLab app launcher config
tests/                  # pytest test suite
docs/                   # Architecture and design documentation
external/               # GITIGNORED reference clones (not runtime dependencies)
```

## Key Endpoints

- `GET /api/v1/health` - Service status
- `GET /api/v1/ready` - Readiness check
- `POST /api/v1/optimize` - Main optimization endpoint

## Configuration

Environment variables via `.env` (see `.env.example`):
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` - LLM provider keys
- `DEFAULT_MODEL` - Fallback model (default: gpt-4)
- `MAX_ITERATIONS` - Optimization iteration limit (default: 5)
- `MAX_DATASET_SIZE` - Dataset size constraint (default: 1000)

## Design Patterns

- **Registry pattern** for optimization tracking (see `docs/registry-design.md`)
- **Parent-child run hierarchy** (MLflow/DSPy style) for campaign/trial tracking
- **JSONL format** for results (OpenAI Evals standard)
- Core logic in `api/core/optimizer.py` must remain framework-agnostic

## Current State

The optimizer has placeholder implementations for `_evaluate_prompt()` and `_improve_prompt()` methods in `api/core/optimizer.py`. These are marked TODO and need actual LLM integration.

## External References

The `external/` directory is gitignored and contains reference clones (like TermNorm-excel) for documentation purposes only - no runtime dependency.
