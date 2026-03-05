# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs optimization campaigns (grid search, iterative candidate generation) to improve prompt accuracy. The primary evaluation mode is **backend evaluation** — calling the backend's `/matches` endpoint with `ranking_prompt` overrides.

## Commands

```bash
# Run API server
uvicorn api.main:app --port 8001 --reload

# Run all tests
pytest -v --tb=short

# Run a single test file
pytest tests/test_project_store_evals.py -v

# Run a single test function
pytest tests/test_prompt_state.py::test_create_and_derive -v

# Lint
ruff check api/ tests/

# Docker (JupyterLab + API)
cd docker && docker-compose up --build
```

## Architecture

### Two entry points, shared core

1. **FastAPI API** (`api/main.py`) — REST endpoints at `/api/v1/`. Routers: `backends`, `campaigns`, `health`, `workflows`.
2. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` is the **primary working interface** at this stage. Uses `notebooks/_campaign_lib.py`, a thin wrapper that delegates to `api/services/` and adds tqdm progress bars + IPython display. The notebook flow:
   - **Setup** — `init_services()` connects to TermNorm, syncs experiments, loads session terms
   - **Configuration** — `campaign_config` dict: query budget, exploration rate, pipeline overrides, grid/smart search knobs, LLM settings
   - **Data** — pipeline snapshot (`fetch_pipeline()`), backend status check, dataset summary, Excel ground-truth loading, Langfuse sync config
   - **Smart Search** — scan advisor (LLM-driven axis recommendations) → build diagnostic set → historical data audit + coverage advisor → sensitivity scan → select scan winner
   - **Grid Search** (optional) — build/resume grid plan → run grid → display results + LLM analysis → select grid winner
   - **Optimization** — feedback cycle (automatic with patience-based stopping) or manual round-by-round
   - **Results** — campaign comparison table, per-query flip tracking, PromptState lineage chain, save winner, Langfuse backfill

All core logic lives in `api/services/`. The notebook library (`_campaign_lib.py`) never implements business logic — it only wraps service functions with UI output.

`tests/test_e2e_optimization.py` is the testable E2E proxy for the notebook workflow — critical because Claude Code can't run Jupyter.

### Service layer (`api/services/`)

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts against datasets via backend `/matches` endpoint. Content-addressed deduplication via `eval_content_hash()`. Incremental writes (`.partial.jsonl`) for crash recovery. |
| `search/grid_core.py` | Grid search over Layer 1 prompt fields. Distance-weighted stratified sampling. LLM-assisted context restructuring and result analysis. Grid plan persistence. Skips `init_session` when all points are cached. |
| `prompt_optimizer.py` | LLM meta-prompt candidate generation, round winner selection, improvement suggestions. |
| `backend_client.py` | HTTP client for backend APIs (sync experiments, replay queries, init sessions, `fetch_pipeline()`). |
| `pipeline_discovery.py` | Pipeline schema factory. `TERMNORM_DEFAULT_SCHEMA` (structural only) + `parse_pipeline_response()` merges live `GET /pipeline` metadata. |
| `project_store.py` | Facade over focused store modules in `stores/`. File I/O for `.promptpotter/projects/`. |
| `campaign/feedback_cycle.py` | Iterative optimization orchestrator: `CycleConfig` → generate_candidates → evaluate_and_select_winner loop with patience-based stopping. Hierarchical 3-loop escalation (L1 generate → L2 refine_context → L3 modify_plan) when enable_l2/enable_l3 are set. 4-path routing (generate/refine_context/modify_plan/stop). |
| `campaign/layer_transitions.py` | L2 (refine_context) and L3 (modify_plan) LLM-driven transitions for the 3-loop feedback cycle. |
| `dataset_builder.py` | Excel ground-truth loading (`load_excel_ground_truth`) and train/test splitting. Column mapping via `SHEET_COLUMN_MAP`. |
| `campaign/campaign_init.py` | Campaign initialization: project store setup, backend sync, baseline evaluation. |
| `search/smart_search.py` | Sensitivity scan (OAT perturbation), adaptive search (coordinate descent), axis classification. `filter_variant_library()` drops axes not in active pipeline. |
| `search/scan_advisor.py` | LLM-driven scan recommendations. Enriched with output schema fields + prompt metadata from `PipelineSchema`. |
| `search/coverage.py` | Historical index (`build_prompt_result_index`) and coverage advisor. Discovers all stored `dataset_runs` for reuse across optimization threads. |
| `obs/observability_logger.py` | File-based observability: Langfuse-compatible traces, MLflow experiments, prompt versioning. `events.jsonl` flat nav log. |
| `obs/langfuse_client.py` | Langfuse v2 cloud integration (singleton). |
| `obs/langfuse_push.py` | Push eval runs to Langfuse cloud. `push_run()` (auto, per-eval) + `push_all_runs()` (batch). |
| `stores/` | Focused store modules: `BackendStore`, `ExecutionStore`, `DatasetRunStore`, `DatasetStore`, `GridPlanStore`, `SmartSearchStore`, `CampaignStore`. Shared I/O in `stores/base.py`. |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with `_OpenAICompatibleClient` base. Global singleton via `get_llm_client()`. Exponential backoff for transient 503/429 errors. |
| `query_utils.py` | Shared query-parsing utilities (e.g. `parse_bom_material()`). |
| `comparison.py` | Statistical comparison (hit@k, McNemar, Wilcoxon). |

### Data model

**PromptState** defines the prompt being optimized. **PipelineSchema** defines the backend pipeline being targeted. Together they parameterize every optimization service: `f(PromptState, PipelineSchema, eval_data) → scores`.

**PromptState** (`api/models/prompt_state.py`) — Immutable, versioned prompt configuration organized into 3 optimization layers:
- **Layer 1 (Generate)**: `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples` — change every optimization pass
- **Layer 2 (Refine Context)**: `context`, `parameters` — adjust when Layer 1 stalls
- **Layer 3 (Modify Plan)**: `plan` — rarely changed (strategy defaults)

PromptState is frozen (`model_config = {"frozen": True}`). Use `derive(**changes)` to create children (sets `parent_id` automatically). Use `render()` to assemble Layer 1 fields into a prompt string. Use `diff(a, b)` for structured diffs.

**PipelineSchema** (`api/models/pipeline_schema.py`, M6) — Backend-agnostic pipeline description with derivation methods: `step_param_keys()`, `obs_extraction_map()`, `template_variables`, `langfuse_type_map()`. Each `PipelineStep` can carry `output_schema` (field names/descriptions) and `prompt_meta` (template variables, prompt template). Factory in `api/services/pipeline_discovery.py`: `parse_pipeline_response()` parses `GET /pipeline` and merges live metadata (live always wins). `TERMNORM_DEFAULT_SCHEMA` carries structural metadata only (observation_mappings, langfuse_type, param_keys, runtime) — registry-owned `output_schema`/`prompt_meta` come exclusively from the live response's `resolved_schemas`/`resolved_prompts`.

**ProjectStore** disk layout, store conventions, and evaluation flow details: see [`api/services/CLAUDE.md`](api/services/CLAUDE.md).

### Pipeline composability

PromptPotter controls backend pipeline behavior through **`node_overrides`** — structured per-node override dicts that mirror the backend's `GET /pipeline` config shape. `run_match()` translates internal flat param names (e.g. `ranking_temperature`) to `node_overrides` format (e.g. `{"llm_ranking": {"temperature": 0.5}}`) at the HTTP boundary. Backends only accept `node_overrides`.

Each LLM node supports `prompt`, `output_schema`, and `model` overrides. See [`docs/connectors/termnorm.md`](docs/connectors/termnorm.md) for the full key mapping.

### North star workflow (HITL optimization cycle)

The human workflow is a repeatable loop:

0. **Pipeline snapshot** — call `backend_client.fetch_pipeline()` and display the full JSON. This ensures every experiment run has its pipeline parameters recorded inline in the notebook output.
1. **Generate data** — sync from backend, build eval dataset, run baseline eval
2. **Explore** — sensitivity scan and/or grid search map the accuracy landscape; results are persisted as `dataset_runs` keyed by content hash
3. **Optimize** — feedback cycle (LLM candidate generation → backend evaluation → winner selection) runs iteratively until the human stops it
4. **Harvest** — the human reviews the optimization results and stops the thread. All eval data from the optimization (every candidate evaluated against every query) is already stored in `dataset_runs` via `evaluate_prompt_cached`
5. **Reuse** — sensitivity scan's coverage advisor and historical index automatically discover all stored `dataset_runs`, regardless of which optimization thread produced them. The human can start a brand new optimization from a freshly calculated starting point, benefiting from all previously collected eval data

**Key principle:** Every backend evaluation — whether from grid search, sensitivity scan, or feedback cycle — writes to the same `dataset_runs` store with content-addressed deduplication. This makes all eval data automatically available for future scans and optimizations. No data is siloed per campaign.

### Evaluation flow

All evaluation paths (grid search, smart search, feedback cycle) converge on `evaluate_prompt_cached()` — the single gateway for eval persistence with content-addressed deduplication. See [`api/services/CLAUDE.md`](api/services/CLAUDE.md) for details.

### Workflow engine scaffold

CWL-inspired workflow engine (`api/core/`, `api/nodes/`). The node base classes and optimizer nodes (InitNode, GrowFilterNode, AnalysisEvalNode) are actively used by the feedback cycle. The YAML-driven WorkflowRunner and REST endpoints are scaffold for future migration. See [`api/core/CLAUDE.md`](api/core/CLAUDE.md).

### How to start a milestone

Each milestone has an executable spec in `docs/specs/`. One Claude Code session = one WBS work package.

**Steps:**
1. Read the milestone spec (`docs/specs/m{N}-*.md`) — scope decisions, deliverables, API sketches
2. Read `docs/specs/wbs.md` to find your work package ID and dependencies
3. Read the service files listed in the deliverables table
4. Check the "Reading list per work package" table in the milestone spec for WP-specific files

| Milestone | Spec file | Pre-reading hint |
|-----------|-----------|-----------------|
| M5: Observability | Complete | See [`docs/obs-guide.md`](docs/obs-guide.md) for data exploration. |
| M6: PipelineSchema + Workflow Migration | Wave 2 complete. [`docs/specs/m6-workflow-migration.md`](docs/specs/m6-workflow-migration.md) | Waves 4-5: read `api/core/workflow_runner.py` and `workflows/optimizer_single_pass.yaml` |
| M7: Multi-Connector | [`docs/specs/m7-multi-connector.md`](docs/specs/m7-multi-connector.md) | Read `docs/connectors/termnorm.md` and `api/services/backend_client.py` |

### TermNorm reference patterns

The TermNorm repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\`. See its `CLAUDE.md` for reference implementations (Langfuse, MLflow, prompt registry).

## Project Conventions

- **No backward compatibility** — freely break signatures, rename, restructure. No compat shims.
- **Python**: 3.13, ruff for linting (line-length 100, select E/F rules)
- **Type hints**: PEP 604 (`X | None`, not `Optional[X]`), lowercase generics (`list[str]`, not `List[str]`)
- **Logging**: `logging` module (no `print()` in non-notebook code). Setup in `api/config/logging.py`.
- **Default LLM**: `meta-llama/llama-4-maverick-17b-128e-instruct` via Groq
- **Config**: Pydantic `BaseSettings` loading from `.env` (see `api/config/settings.py`)
- **Version**: Centralized in `api/config/settings.py` as `APP_VERSION`
- **API versioning**: all endpoints under `/api/v1/`
- **Pipeline reproducibility**: The notebook MUST display the full pipeline configuration (all node configs, models, temperatures, schemas) via `GET /pipeline` before any evaluation. This is the experiment's parameter manifest — never strip it to just step names. The scan advisor also reads this config to make informed recommendations.
