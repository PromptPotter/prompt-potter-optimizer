# CLAUDE.md

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs optimization campaigns (grid search, iterative candidate generation) to improve prompt accuracy. The primary evaluation mode is **backend evaluation** — calling the backend's `/matches` endpoint with `ranking_prompt` overrides.

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

## Architecture

### Two entry points, shared core

1. **FastAPI API** (`api/main.py`)
2. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` is the **primary working interface** at this stage. Uses `notebooks/_campaign_lib/`.

`_campaign_lib` never implements business logic — it only wraps service functions with UI output. `tests/test_campaign_registry.py` is the testable E2E proxy for the notebook workflow.

### Service layer (`api/services/`)

All core logic lives here. See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for the service catalog, evaluation gateway, and store layout.

### Data model

All optimization services follow: `f(SearchPoint, PipelineSchema, eval_data) → scores`. See [`api/models/CLAUDE.md`](api/models/CLAUDE.md) for field details.

### Prompt decomposition & alias groups

Two core mechanisms that work together (both actively evolving):

- **Prompt decomposition** — PromptPotter decomposes a backend's monolithic prompt into internal fields (`persona`, `task_intent`, `thinking_style`, `answer_format`, `problem_description`) via LLM restructure. A variant library (`api/config/prompt_variants.json`) provides per-field alternatives for scan and grid search.
- **Prompt alias groups** — `register_alias` / `resolve_aliases` link semantically equivalent prompt hashes so historical evaluations are discoverable across forms. Resolution is transitive.

### Pipeline composability

PromptPotter uses **`node_config`** format throughout — the same nested dict shape as `pipeline.json` and the `/matches` wire format (e.g. `{"llm_ranking": {"temperature": 0.5}}`). No flat param names, no translation layer.

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

0. **Pipeline snapshot** — display full pipeline JSON before any evaluation (experiment parameter manifest)
1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan and/or grid search map the accuracy landscape
3. **Optimize** — critique-guided feedback cycle with L1→L2→L3 escalation
4. **Harvest** — human reviews results
5. **Reuse** — coverage advisor discovers all stored `dataset_runs` regardless of source

**Key principle:** Every backend evaluation writes to the same `dataset_runs` store with content-addressed deduplication. No data is siloed per campaign.

### Workflow engine scaffold

CWL-inspired workflow engine (`api/core/`, `api/nodes/`). The YAML-driven WorkflowRunner and REST endpoints are scaffold for future migration. See [`api/core/CLAUDE.md`](api/core/CLAUDE.md).

**Optimizer-as-pipeline** — The optimizer is a 4-step pipeline (`l1_generate`, `l1_evaluate`, `l2_refine_context`, `l3_modify_plan`), modeled using the same `PipelineSchema` as the target backend. This enables step-level Langfuse tracing and full reproducibility of every meta-optimizer LLM call. Pluggable `EscalationCheck`s (e.g., `DegradationCheck`) can short-circuit evaluation and route directly to L2/L3. Optimizer state (`plan`, `context`, `critique`) is visible in preflight and overridable via `campaign_config`. *Design note: the recursive closure (L4 — optimizing the optimizer's own prompts) was recognized from inception as an inherent property of the architecture.* See M7 spec ([`docs/specs/m7-optimizer-pipeline.md`](docs/specs/m7-optimizer-pipeline.md)).

### Milestones

Each milestone has an executable spec in `docs/specs/`. See [`docs/specs/CLAUDE.md`](docs/specs/CLAUDE.md) for the process and milestone table.

### TermNorm reference patterns

The TermNorm repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`. See its `CLAUDE.md` for reference implementations (Langfuse, MLflow, prompt registry).

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims, no dual-format readers.
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **`sample_size`**: Universal eval sampling parameter across all services (0 = use all). No synonyms (`max_queries`, `eval_queries_per_point`, etc.).
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields. Surfaces schema violations immediately.
- **Pipeline reproducibility**: The notebook MUST display the full pipeline configuration (all node configs, models, temperatures, schemas) via `GET /pipeline` before any evaluation. This is the experiment's parameter manifest.
- **EXPERIMENT_ID is the single source of truth**: Every notebook cell operates within the scope of `EXPERIMENT_ID`. When set, config MUST match the stored experiment — mismatches raise `ValueError` demanding a new ID. When `None`, a new experiment is auto-created from the config hash. This invariant applies to feedback cycle, scan, grid search, and all data surfaces. No silent config drift between runs.
- **Display parity**: Cached results must display identically to fresh results — no visible difference in output format between cached and computed data. The user should not be able to tell whether a result came from cache or a live backend call.

See [`docs/design-principles.md`](docs/design-principles.md) for the full principles catalog with rationale.
