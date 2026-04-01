# CLAUDE.md

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs L1/L2/L3 optimization campaigns to improve prompt accuracy.

## Commands

```bash
# Run API server
uvicorn api.main:app --port 8001 --reload

# Run tests
pytest -v --tb=short

# Lint
ruff check api/ tests/

# Docker (JupyterLab + API)
cd docker && docker-compose up --build
```

## Mental Model

Two entry points (FastAPI API + Jupyter notebook), one service core in `api/services/`. The sole notebook is `notebooks/optimization_campaign.ipynb` (handles both optimization and evaluation); `campaign_lib` wraps services with display only. (`evaluation.ipynb` was removed — it was never production-used.)

**Two loops:** Human sensitivity scan (explore which axes matter) feeds the AI critique-guided optimization loop (L1 generate → L1 evaluate → L2 refine → L3 replan). All evaluation data shares one `dataset_runs/` store via content-addressed dedup. SearchMemory *(M8)* aggregates all historical evaluation data into a materialized view (parameter impact, query patterns, failure modes) that feeds both loops.

**Two-layer tracing:** Target layer (JobSearchPoint → dataset_runs/) and optimizer layer (OptSearchPoint → campaign trials). Both independently reconstructable from disk.

**Pipeline composability:** `pipeline_params` (nested dicts keyed by node name) throughout PromptPotter. `node_config` only at the TermNorm wire boundary.

**Two parameter namespaces:** Prompt scheme fields (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format` — rendered into a prompt string by `render()`) vs pipeline node params (nested dicts like `{"token_matching": {"thinking_style": "..."}}` — sent to backend nodes). These are orthogonal and may share names. L1 candidates modify pipeline node params via `pipeline_params_override` — they do NOT mutate prompt scheme fields. See [`docs/prompt-scheme.md`](docs/prompt-scheme.md).

See [`docs/architecture.md`](docs/architecture.md) for diagrams, caching, pipeline discovery, and disk layout.

## Data Model Reference

All services follow: `f(SearchPoint, PipelineSchema, eval_data) → scores`.

`SearchPoint` → `PromptTemplate` (8-field scheme) → `OptSearchPoint` (+ lineage, L2/L3, memory). `JobSearchPoint` bundles `pipeline_params` for frozen target eval. `PipelineSchema` describes pipelines. `EvalContext` bundles eval infrastructure.

See [`docs/architecture.md` § Data Models](docs/architecture.md#data-models) for the full class hierarchy, methods, and fields.

## Service Catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts via backend `/matches` — single eval gateway |
| `l1_optimizer.py` | L1 candidate generation (`l1_generate`) and winner selection (`l1_evaluate`) |
| `backend_client.py` | HTTP client for backend APIs (sync, replay, `fetch_pipeline()`) |
| `pipeline_discovery.py` | Parses `GET /pipeline` response into `PipelineSchema` |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `campaign/optimization_loop.py` | L1→L2→L3 optimization loop with patience-based stopping |
| `campaign/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `campaign/campaign_init.py` | Campaign init, `resolve_experiment_id()`, experiment overrides |
| `search/smart_search.py` | Sensitivity scan (OAT), adaptive search |
| `search/scan_advisor.py` | LLM-driven scan recommendations |
| `search/coverage.py` | Historical index, step-sequence coverage matching |
| `search/search_memory.py` | **Planned (M8 Wave 3)** — cross-campaign intelligence materialized view |
| `obs/observability_logger.py` | Langfuse-compatible traces, MLflow |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with exponential backoff |

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure.
- **Type hints**: PEP 604 (`X | None`), lowercase generics (`list[str]`)
- **Logging**: `logging` module (no `print()` in services). Setup in `api/config/logging.py`.
- **`sample_size`**: Universal eval sampling parameter (0 = all). No synonyms.
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **Pipeline reproducibility**: Notebook displays full pipeline config via `GET /pipeline` before any evaluation.
- **EXPERIMENT_ID**: Single source of truth. Config must match stored experiment when set.
- **Display parity**: Cached results display identically to fresh results.
- **Graceful interrupt**: Signal-flag pattern. No completed work is ever discarded.

- **Error handling**: `graceful()` context manager in `campaign/helpers.py` is the standard suppress-and-log pattern. `EscalationError` carries structured `partial_results` for campaign flow control.
- **`api/shared/`**: Leaf-level utilities shared by models and services (hashing, schema mutations). No domain model or service dependencies allowed.
- **`api/shared/constants.py`**: Canonical source for `PROMPT_STRING_FIELDS`, `LAYER_FIELDS`, and `LAYER1_STRING_FIELDS`. All modules must import field lists from here — never define them locally.
- **`api/config/optimizer_pipeline.py`**: Optimizer pipeline schema loader + `llm_call()` primitive. All optimizer nodes use this instead of calling `chat()` directly.

See [`docs/design-principles.md`](docs/design-principles.md) for the full principles catalog.

## Evaluated & Rejected Refactorings

- **PromptDecomposition sub-model on OptSearchPoint**: Evaluated 2026-03-26 and rejected. 15+ `getattr(opt_sp, field)` iteration sites across 7 files (round_execution, coverage, sensitivity_scan, scan_results, search_point, hashing, opt_search_point) depend on flat fields via `PROMPT_STRING_FIELDS`. Extracting to `opt_sp.prompt.field` would add indirection at every site without clarity gain. OptSearchPoint is a legitimate aggregation of the optimizer's full working state, not a god object.
- **OptimizationMemory sub-model on OptSearchPoint**: 9 memory fields accessed from 5 files in fragmented patterns (l1_optimizer reads all as context; escalation.py writes degradation subset; round_execution.py writes critique). No clean seam exists.

## Navigation Guide

1. **This file** — overview, commands, data models, conventions, service catalog
2. [`docs/architecture.md`](docs/architecture.md) — system design, two-loop diagram, two-layer tracing, caching, pipeline discovery, disk layout
3. [`docs/optimization.md`](docs/optimization.md) — L1/L2/L3 optimization loop, critique agent, escalation, configuration
4. [`docs/node-standard.md`](docs/node-standard.md) — node type hierarchy, `llm_call()` primitive (`api/config/optimizer_pipeline.py`), pipeline declaration format
5. [`docs/sensitivity-scan.md`](docs/sensitivity-scan.md) — OAT scan workflow, coverage, circuit breaker
6. [`docs/observability.md`](docs/observability.md) — Langfuse, MLflow, events.jsonl
7. [`docs/setup-guide.md`](docs/setup-guide.md) — installation, quick start, REST API
8. [`docs/specs/`](docs/specs/CLAUDE.md) — active milestone specs (M8, M9) + roadmap; archived specs in `docs/specs/archive/`
9. [`docs/prompt-scheme.md`](docs/prompt-scheme.md) — prompt decomposition (8 fields), rendering, variant library, projection to target pipeline
10. [`docs/information-flow.md`](docs/information-flow.md) — data origins, consumer matrix, information compression chain
