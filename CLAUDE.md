# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local Development:**
```bash
# Setup
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Edit to add API keys

# Run server
uvicorn api.main:app --reload     # Dev with auto-reload
uvicorn api.main:app              # Production mode

# Testing
pytest tests/                     # Run all tests
pytest tests/test_api.py          # Single test file
pytest tests/test_api.py::test_health_check  # Single test
```

**Docker:**
```bash
# Development with hot reload
docker-compose -f docker/docker-compose.yml up -d
docker-compose -f docker/docker-compose.yml logs -f
docker-compose -f docker/docker-compose.yml down

# Production build
docker build -f docker/Dockerfile -t promptpotter-optimizer .
docker run -d -p 8000:8000 -e OPENAI_API_KEY=key promptpotter-optimizer
```

## Architecture

**Design Philosophy:** API-first microservice
- Language-agnostic REST API for prompt optimization
- Designed for consumption by Google Colab notebooks (primary), JavaScript (future)
- Stateless service - no database, all state in request/response
- Docker-first deployment supporting both self-hosted and cloud hosting

### API Structure (FastAPI)

```
api/
├── main.py                       # FastAPI app initialization + router registration
├── config/
│   └── settings.py               # Pydantic settings (env vars, API keys, limits)
├── routers/
│   ├── health.py                 # /health and /ready endpoints
│   └── optimize.py               # POST /optimize (core endpoint)
├── models/
│   ├── request.py                # OptimizationRequest (Pydantic)
│   └── response.py               # OptimizationResponse + ErrorResponse
├── core/
│   └── optimizer.py              # PromptOptimizer class (main logic)
└── services/                     # Future: LLM provider integrations
```

**Key Concepts:**

- **Optimization Pipeline**: Iterative prompt improvement
  1. Evaluate initial prompt on dataset
  2. Analyze failures/edge cases (TODO: implement with LLM)
  3. Generate improved variant (TODO: implement with LLM)
  4. Repeat until max iterations or convergence (early stopping after 2 iterations without improvement)

- **API Endpoint** (`POST /api/v1/optimize`):
  - Input: `OptimizationRequest` with initial_prompt, dataset, target_metric, optional model/max_iterations
  - Dataset validation: max size checked against `settings.MAX_DATASET_SIZE`
  - Returns: `OptimizationResponse` with optimized_prompt, scores, improvement %, iteration history

- **Current Implementation Status**:
  - Skeleton complete with working API endpoints
  - `PromptOptimizer._evaluate_prompt()` and `._improve_prompt()` are **placeholder stubs** returning mock data
  - TODO: Implement actual LLM integration in `api/core/optimizer.py`

### Configuration

Single `.env` file with Pydantic settings:
- LLM API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- Limits: `MAX_DATASET_SIZE` (default 1000), `MAX_ITERATIONS` (default 5)
- Runtime: `ENVIRONMENT`, `DEBUG`, `API_HOST`, `API_PORT`
- CORS: `ALLOWED_ORIGINS` (default `*` for development)

All settings loaded via `api/config/settings.py` using `pydantic-settings`.

### Client Usage Pattern

Designed for Google Colab notebooks (`examples/` directory):
```python
# Colab notebook pattern
import requests

response = requests.post(
    "http://localhost:8000/api/v1/optimize",  # or hosted API URL
    json={
        "initial_prompt": "Classify sentiment:",
        "dataset": [{"text": "...", "expected": "positive"}],
        "target_metric": "accuracy",
        "max_iterations": 5
    }
)
result = response.json()
```

## Development

**Adding LLM Integration:**
1. Add provider SDK to `requirements.txt`
2. Create provider client in `api/services/` (e.g., `openai_client.py`)
3. Update `api/core/optimizer.py`:
   - Implement `_evaluate_prompt()`: run dataset examples through LLM, compute metric
   - Implement `_improve_prompt()`: use LLM to analyze failures and generate better prompt

**API Endpoint Pattern:**
- All endpoints in `api/routers/`
- Register in `api/main.py` with `app.include_router()`
- Use Pydantic models from `api/models/` for request/response validation
- Raise `HTTPException` for errors (400 for validation, 500 for server errors)

**Testing:**
- Use FastAPI `TestClient` for API endpoint tests
- Tests in `tests/` with pytest
- Mock LLM calls in tests to avoid API costs

## Deployment

**Docker Multi-stage Build:**
- Builder stage: installs dependencies with gcc
- Final stage: slim Python image with non-root user
- Health check on `/api/v1/health` endpoint

**API Documentation:**
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Auto-generated from Pydantic models

**Important:**
- API keys must be set via environment variables (never commit)
- For Colab notebooks accessing local API, use ngrok tunnel
- All endpoints prefixed with `/api/v1` for versioning
