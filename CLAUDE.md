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

Two entry points (FastAPI API + Jupyter notebook), one service core in `api/services/`. The notebook (`notebooks/optimization_campaign.ipynb`) is the primary interface; `_campaign_lib` wraps services with display only.

**Two loops:** Human sensitivity scan (explore which axes matter) feeds the AI critique-guided feedback cycle (L1 generate → L1 evaluate → L2 refine → L3 replan). All evaluation data shares one `dataset_runs/` store via content-addressed dedup.

**Two-layer tracing:** Target layer (JobSearchPoint → dataset_runs/) and optimizer layer (OptSearchPoint → campaign trials). Both independently reconstructable from disk.

**Pipeline composability:** `pipeline_params` (nested dicts keyed by node name) throughout PromptPotter. `node_config` only at the TermNorm wire boundary.

See [`docs/architecture.md`](docs/architecture.md) for diagrams, caching, pipeline discovery, and disk layout.

## Data Model Reference

All services follow: `f(SearchPoint, PipelineSchema, eval_data) → scores`.

### SearchPoint hierarchy (`api/models/`)

```
SearchPoint (base)           — abstract base, "a point in a search space"
    ├── JobSearchPoint       — user's job: model + temp + pipeline_params (frozen)
    └── OptSearchPoint       — optimizer state: prompt fields + L2/L3 + memory (mutable)
```

**SearchPoint** (`api/models/search_point.py`) — abstract base class defining the search space contract.

**JobSearchPoint** (`api/models/search_point.py`) — flat, frozen, content-hashable target evaluation specification. Fields: `model` + `temperature` + `pipeline_params`. The rendered prompt lives inside `pipeline_params` as a node config value (e.g., `{"llm_ranking": {"prompt": "..."}}`). Methods: `render()`, `content_hash(eval_data)`, `sp_hash()`, `derive()`.

**OptSearchPoint** (`api/models/opt_search_point.py`) — inherits from SearchPoint. Full optimizer working state:
- **Lineage**: `id`, `parent_id`, `changes_description`
- **Prompt decomposition** (L1): `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples`
- **L2 state**: `optimizer_params`, `task_context`
- **L3 state**: `plan`
- **Optimization memory**: `critique_text`, `critique`, `thinking_styles`, `escalation_journal`, `warning_inventory`, `l2_directive`, `content_hashes`

Key methods: `render_prompt()` assembles prompt fields into a string. `to_job_search_point()` projects into a JobSearchPoint by injecting the rendered prompt into `pipeline_params`. `derive_candidate()` creates child points. `compile_prompt()` substitutes `{{variables}}`.

### PipelineSchema / PipelineNode (`api/models/pipeline_schema.py`)

Describes a pipeline — target or optimizer. Both TermNorm's `GET /pipeline` response and `optimizer_pipeline.json` parse into PipelineSchema. `PipelineNode` (formerly PipelineStep) carries node type, config, param_keys, override_map.

### EvalContext (`api/services/prompt_eval.py`)

Infrastructure bundle: `backend_client`, `store`, `backend_id`, `pipeline_schema`, `obs`, `source`, `model`, `temperature`, `pipeline_params`, `experiment_id`, `escalation_checks`, `candidate_idx`, `n_total_candidates`.

## Service Catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts via backend `/matches` — single eval gateway |
| `l1_optimizer.py` | L1 candidate generation (`l1_generate`) and winner selection (`l1_evaluate`) |
| `backend_client.py` | HTTP client for backend APIs (sync, replay, `fetch_pipeline()`) |
| `pipeline_discovery.py` | Parses `GET /pipeline` response into `PipelineSchema` |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `campaign/feedback_cycle.py` | L1→L2→L3 escalation loop with patience-based stopping |
| `campaign/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `campaign/campaign_init.py` | Campaign init, `resolve_experiment_id()`, experiment overrides |
| `search/smart_search.py` | Sensitivity scan (OAT), adaptive search |
| `search/scan_advisor.py` | LLM-driven scan recommendations |
| `search/coverage.py` | Historical index, coverage advisor |
| `obs/observability_logger.py` | Langfuse-compatible traces, MLflow |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with exponential backoff |

### Evaluation gateway

`evaluate_prompt_cached()` in `prompt_eval.py` is the **single entry point** for all eval persistence. All evaluation paths converge here.

### Pipeline discovery — ownership principle

**DO:** All target pipeline metadata from `GET /pipeline`. `parse_pipeline_response()` builds `PipelineSchema` from the live response. Optimizer pipeline from `api/config/optimizer_pipeline.json` via `load_pipeline_from_dict()`.

**DON'T:** No hardcoded pipeline schemas, node names, or param keys in PromptPotter code. Tests may use inline test schemas.

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

See [`docs/design-principles.md`](docs/design-principles.md) for the full principles catalog.

## Navigation Guide

1. **This file** — overview, commands, data models, conventions, service catalog
2. [`docs/architecture.md`](docs/architecture.md) — system design, two-loop diagram, two-layer tracing, caching, pipeline discovery, disk layout
3. [`docs/optimization.md`](docs/optimization.md) — L1/L2/L3 feedback cycle, critique agent, escalation, configuration
4. [`docs/node-standard.md`](docs/node-standard.md) — node type hierarchy, `llm_call()` primitive, pipeline declaration format
5. [`docs/sensitivity-scan.md`](docs/sensitivity-scan.md) — OAT scan workflow, coverage, circuit breaker
6. [`docs/observability.md`](docs/observability.md) — Langfuse, MLflow, events.jsonl
7. [`docs/setup-guide.md`](docs/setup-guide.md) — installation, quick start, REST API
8. [`docs/specs/`](docs/specs/CLAUDE.md) — milestone specs
