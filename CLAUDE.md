# CLAUDE.md

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs optimization campaigns (sensitivity scan, iterative candidate generation) to improve prompt accuracy. The primary evaluation mode is **backend evaluation** — calling the backend's `/matches` endpoint with `ranking_prompt` overrides.

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

All core logic lives here.

**Two layers must both be traced:**
- **Target layer**: `SearchPoint` → `evaluate_prompt_cached()` → `dataset_runs/` (content-addressed, shared across all eval paths)
- **Optimizer layer**: `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint). Captures critique, thinking_styles, task_context, escalation_journal, warning_inventory, plan, optimizer_params.

`OptSearchPoint` is the optimizer-layer analogue of `SearchPoint`. All optimizer state flows through it — check existing fields before proposing new data structures. Both layers must be independently reconstructable from disk.

### Prompt decomposition & alias groups

Two core mechanisms that work together (both actively evolving):

- **Prompt decomposition** — PromptPotter decomposes a backend's monolithic prompt into internal fields (`persona`, `task_intent`, `thinking_style`, `answer_format`, `problem_description`) via LLM restructure. A variant library (`api/config/prompt_variants.json`) provides per-field alternatives for sensitivity scan.
- **Prompt alias groups** — `register_alias` / `resolve_aliases` link semantically equivalent prompt hashes so historical evaluations are discoverable across forms. Resolution is transitive.

### Pipeline composability

PromptPotter uses **`pipeline_params`** format throughout — nested dicts keyed by step name (e.g. `{"llm_ranking": {"temperature": 0.5}}`). `BackendClient.run_match()` translates to the `node_config` wire-format key at the TermNorm boundary. No flat param names, no translation layer.

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

0. **Pipeline snapshot** — display full pipeline JSON before any evaluation (experiment parameter manifest)
1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan maps the accuracy landscape
3. **Optimize** — critique-guided feedback cycle with L1→L2→L3 escalation
4. **Harvest** — human reviews results
5. **Reuse** — coverage advisor discovers all stored `dataset_runs` regardless of source

**Key principle:** Every backend evaluation writes to the same `dataset_runs` store with content-addressed deduplication. No data is siloed per campaign.

### Optimizer pipeline

The optimizer is a 4-step pipeline (`l1_generate`, `l1_evaluate`, `l2_refine_context`, `l3_modify_plan`), declared in `api/config/optimizer_pipeline.json` using the building block format. **L1 Generate is the sole `pipeline_params` decider**; L2 refines situation context (`task_context`) + meta-settings (creativity, n_variants, sample_size); L3 modifies the strategic plan. Critique (part of L1 Evaluate) produces a 5-field analysis (`positive_critique`, `negative_critique`, `priority_fix`, `suggested_axes`, `summary`) fed to both L1 and L2. Pluggable `EscalationCheck`s (e.g., `DegradationCheck`) can short-circuit evaluation mid-round and route to L2/L3. See [`docs/critique-agent.md`](docs/critique-agent.md) for the full critique/escalation architecture, [`docs/building-blocks.md`](docs/building-blocks.md) for the building block standard, and [`docs/specs/m7-optimizer-pipeline.md`](docs/specs/m7-optimizer-pipeline.md) for the M7 spec.

### Building block primitive (`api/core/llm_call.py`)

`llm_call()` is the shared LLM interaction primitive. Config-driven from `api/config/optimizer_pipeline.json` with runtime overrides. Used by all optimizer building blocks (`l1_generate`, `refine_context`, `modify_plan`, `CritiqueAgent`).

### Milestones

Each milestone has an executable spec in `docs/specs/`. See [`docs/specs/CLAUDE.md`](docs/specs/CLAUDE.md) for the process and milestone table.

### TermNorm reference patterns

The TermNorm repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`. See its `CLAUDE.md` for reference implementations (Langfuse, MLflow, prompt registry).

## Data model reference

All optimization services follow: `f(SearchPoint, PipelineSchema, eval_data) → scores`.

### PromptState (`api/models/prompt_state.py`)

Immutable prompt configuration organized into optimization layers:
- **Layer 1 (Generate)**: persona, task_intent, thinking_style, answer_format, etc. — change every pass
- **Layer 2 (Refine Context)**: optimizer_params — adjust when Layer 1 stalls. `task_context` lives on OptSearchPoint (not PromptState).
- **Layer 3 (Modify Plan)**: plan — rarely changed (strategy defaults)

### OptSearchPoint (`api/models/opt_search_point.py`)

Optimizer-level search point — the optimizer's configuration at a moment in the feedback cycle. Cross-reference design: holds `content_hashes` linking to target-layer `dataset_runs`. Fields: `critique_text`, `thinking_styles`, `plan`, `optimizer_params`, `task_context`, `l2_directive`, `content_hashes`.

### SearchPoint (`api/models/search_point.py`)

Frozen model bundling `prompt_state` + `model` + `temperature` + `pipeline_params`. `content_hash(eval_data)` is the dedup key for `evaluate_prompt_cached()`.

## Service catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation and round winner selection |
| `backend_client.py` | HTTP client for backend APIs (sync, replay, `fetch_pipeline()`) |
| `pipeline_discovery.py` | Pipeline schema factory (parses `GET /pipeline` response into `PipelineSchema`) |
| `project_store.py` | Facade over focused store modules in `stores/` |
| `campaign/feedback_cycle.py` | Iterative optimization: 3-loop escalation (L1→L2→L3) with patience-based stopping |
| `campaign/layer_transitions.py` | L2 (context + meta-settings + l2_directive), L3 (plan) |
| `dataset_builder.py` | Excel ground-truth loading and train/test splitting |
| `campaign/campaign_init.py` | Campaign initialization, `resolve_experiment_id()`, `apply_experiment_overrides()` |
| `search/smart_search.py` | Sensitivity scan (OAT), adaptive search, `filter_variant_library()` |
| `search/scan_advisor.py` | LLM-driven scan recommendations |
| `search/coverage.py` | Historical index, coverage advisor, scan variant diagnostics |
| `obs/observability_logger.py` | File-based observability (Langfuse-compatible traces, MLflow) |
| `obs/step_tracer.py` | `observed_step()` — async context manager for step-level timing + tracing |
| `stores/` | Focused store modules: Backend, Execution, DatasetRun, Dataset, SmartSearch, Campaign |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with exponential backoff |

### Evaluation gateway

`evaluate_prompt_cached()` in `prompt_eval.py` is the **single entry point** for all eval persistence. All evaluation paths (smart search, feedback cycle) converge here.

### Pipeline discovery — ownership principle

**TermNorm owns all pipeline metadata** — step descriptions, param mappings, observation config, node roles — served via `GET /pipeline`. `parse_pipeline_response()` builds `PipelineSchema` entirely from the live response (no hardcoded fallback). Each node carries an `optimizer` sub-object with PromptPotter-consumed metadata (param_keys, override_map, observation_mappings). Registry-owned metadata (`StepOutputSchema`, `StepPromptMeta`) is resolved from `resolved_schemas`/`resolved_prompts` in the response.

**No backend-specific constants in PromptPotter code.** Do NOT add hardcoded pipeline schemas, step names, param keys, or any other backend-specific knowledge to PromptPotter services or notebooks. All pipeline structure must come from the live `GET /pipeline` response. The only exception is tests, which may use inline test schemas. If PromptPotter needs new metadata from a backend, add it to the backend's `pipeline.json` → `optimizer` section and consume it generically via `parse_pipeline_response()`.

### ProjectStore disk layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  datasets/{name}.json
  dataset_runs/{run_id}.json          # completed eval runs (shared across all eval paths)
  dataset_runs.json                   # index (content_hash -> run_id)
  smart_search_plans/{plan_id}.json
  campaigns/{campaign_id}.json
  campaigns/{campaign_id}/trial_NNNN.json
  obs/langfuse/events.jsonl
```

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims, no dual-format readers.
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **`sample_size`**: Universal eval sampling parameter across all services (0 = use all). No synonyms (`max_queries`, `eval_queries_per_point`, etc.).
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields. Surfaces schema violations immediately.
- **Pipeline reproducibility**: The notebook MUST display the full pipeline configuration (all node configs, models, temperatures, schemas) via `GET /pipeline` before any evaluation. This is the experiment's parameter manifest.
- **EXPERIMENT_ID is the single source of truth**: Every notebook cell operates within the scope of `EXPERIMENT_ID`. When set, config MUST match the stored experiment — mismatches raise `ValueError` demanding a new ID. When `None`, a new experiment is auto-created from the config hash. This invariant applies to feedback cycle, scan, and all data surfaces. No silent config drift between runs.
- **Display parity**: Cached results must display identically to fresh results — no visible difference in output format between cached and computed data. The user should not be able to tell whether a result came from cache or a live backend call.
- **Graceful interrupt**: Backend eval batches use a signal-flag pattern for Ctrl+C. First interrupt lets the in-flight backend call finish (result printed + saved), then stops. Second interrupt force-quits. Partial results are always saved to disk with `"partial": True` so the SP-hash query cache bridges them on re-run. All interrupt handlers must catch both `KeyboardInterrupt` and `asyncio.CancelledError`. **No completed work is ever discarded.**

See [`docs/design-principles.md`](docs/design-principles.md) for the full principles catalog with rationale.
