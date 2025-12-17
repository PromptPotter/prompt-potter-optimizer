# Prompt Optimization API - Architecture Spec (Partial)

## Design Principle
Dual-mode: works as **Jupyter notebook** (interactive experimentation) AND **FastAPI REST service** (deployment).

## Pattern
**Pydantic I/O + dependency injection** - no framework lock-in (no LangChain/DSPy required).

Core logic in `core/optimizer.py` must be:
- Framework-agnostic pure Python
- LLM client injected (not hardcoded)
- Returns Pydantic models (serializable for MLflow/Langfuse later)

## Structure
```
api/
├── main.py              # FastAPI app + routers
├── config/settings.py   # Pydantic settings (env vars, keys)
├── routers/
│   ├── health.py        # /health, /ready
│   └── optimize.py      # POST /optimize
├── models/
│   ├── request.py       # OptimizationRequest
│   └── response.py      # OptimizationRun, ErrorResponse
├── core/
│   └── optimizer.py     # PromptOptimizer class
└── services/            # LLM provider integrations (future)
```

## Core Data Model
```python
class OptimizationRun(BaseModel):
    prompt_version: str
    input_vars: dict[str, Any]
    output: str
    scores: dict[str, float] | None = None
    metadata: dict[str, Any] = {}
```

## Usage Modes
- **Notebook:** `optimizer.run_batch(examples)` → iterate, visualize
- **API:** `POST /optimize` → returns `OptimizationRun`

## Future Integrations (not yet implemented)
- MLflow: prompt registry + experiment tracking
- Langfuse: observability
- NVIDIA NIM: inference endpoint (OpenTelemetry metrics)

## References
- brevdev/workshop-build-an-agent (LangGraph + NIM style)
- MLflow Prompt Registry pattern
```