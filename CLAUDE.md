# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter Optimizer finds better prompts automatically. Give it a dataset (question + expected answer pairs) and point it at your LLM pipeline's evaluation endpoint — it tries prompt and parameter variations, measures accuracy, and iterates through a critique-guided 3-layer optimization loop. The backend can be anything from a single LLM call to a multi-step pipeline with retrieval, enrichment, and ranking. Currently tested with TermNorm (AI terminology normalization).

## Commands

```bash
# Install (dev)
pip install -e ".[dev,jupyter,stats]"

# Lint & format
ruff check promptpotter/ tests/
ruff format promptpotter/ tests/

# Type check
mypy promptpotter/

# Tests
pytest tests/                          # all tests
pytest tests/test_search_point.py      # single file
pytest tests/ -k "test_name"           # single test by name

# Run API server
uvicorn promptpotter.main:app --port 8001 --reload

# CLI campaign runner (HITL optimization from terminal)
python -m promptpotter.cli.campaign_runner init --backend-url http://127.0.0.1:8000
python -m promptpotter.cli.campaign_runner optimize --auto     # full loop
python -m promptpotter.cli.campaign_runner optimize --round    # generate → pause for review
python -m promptpotter.cli.campaign_runner optimize --evaluate # resume evaluation

# Export results
python -m promptpotter.cli.export_results supplemental --backend-id local -o supplemental.md
python -m promptpotter.cli.export_results json --backend-id local -o paper_results.json
```

CI runs: `ruff check` → `ruff format --check` → `mypy` → `pytest --cov`. All must pass.

## Code Conventions

- **Python 3.13+**. Type hints: PEP 604 (`X | None`, `list[str]`) — no `Optional`, no `List`.
- **Ruff** line-length 100, McCabe max complexity 15.
- **Logging** via `logging` module, never `print()`. Setup in `promptpotter/config/logging.py`.
- **No backward compatibility** — freely break signatures, rename, restructure. No shims.
- Pipeline components are called **nodes**, not "building blocks" or "services".
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **`sample_size`**: Universal eval sampling parameter (0 = all). No synonyms.
- **CLI timeouts**: 30 seconds default for ALL CLI commands. Only increase when told "ready for data collection".
- **No background CLI commands**: Never run `campaign_runner` with `run_in_background`. Always foreground so stale processes don't leak.
- Version: `APP_VERSION` in `promptpotter/config/settings.py`.

## Architecture

### Mental Model

Three entry points (notebook, CLI, web API), one service core in `promptpotter/services/`. All entry points produce identical persistent artifacts via the three-layer I/O architecture.

**Three-layer I/O architecture (INVARIANT):**
- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter`. Entry points MUST NOT write campaign artifacts directly. New artifacts → `CAMPAIGN_SESSION_ARTIFACTS` in `campaign/state.py`; `tests/test_artifact_parity.py` enforces.
- **Display** (per-entry-point) — caller passes `RunCallbacks`. MUST NOT write to disk.
- **Control** (per-entry-point) — `FileControlSurface` (CLI) or kernel interrupt (notebook). MUST NOT write campaign artifacts.

**Two loops:** Human sensitivity scan (explore which axes matter) feeds the AI critique-guided optimization loop (L1 generate → L1 evaluate → L2 refine → L3 replan). All evaluation data archived to `dataset_runs/` store. SearchMemory (M8) aggregates historical data into a materialized view that feeds both loops.

### SearchPoint Hierarchy

```
SearchPoint (abstract — render())
  ├── JobSearchPoint       — frozen target-layer spec (pipeline_params)
  └── PromptTemplate       — 8-field prompt decomposition (persona, task_intent, etc.)
      └── OptSearchPoint   — optimizer state (lineage, L2/L3, memory, escalation)
```

All services follow: `f(SearchPoint, PipelineSchema, dataset) → scores`. `JobSearchPoint` is the first positional arg to `eval_search_point()`. `OptSearchPoint` is the source of truth for all optimizer state; projected to `JobSearchPoint` via `to_job_search_point()`.

### Two-Layer Tracing

Every state traced at **both** layers independently:
- **Target layer**: `JobSearchPoint` → `eval_search_point()` → `dataset_runs/` (content-addressed, shared)
- **Optimizer layer**: `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint)

### Evaluation Pipeline

`eval_search_point()` (in `eval_gateway.py`) is the single gateway for evaluation archival + observability. Per-node cache reuse via `IntermediateCache` — `walk_prefix()` finds longest cached prefix; when ALL target nodes cached, short-circuits via `_build_local_result()` (no backend call). `dataset_run_store` is archive-only. `BackendClient` translates `pipeline_params` to wire-format `node_config`.

### Pipeline Params — Two Namespaces

Always **nested dicts** keyed by node name (`{"web_search": {"max_sites": 5}}`). No flat format. `PROMPT_STRING_FIELDS` is the canonical split (import from `shared/constants.py`, never define locally). L1 candidates use `pipeline_params_override`: keys matching `PROMPT_STRING_FIELDS` auto-route to `derive_candidate()` (prompt fields); all others nest under their node name. `l1_generate()` has a safety net that auto-nests flat params the LLM emits.

### Self-Describing Pipeline

`PipelineSchema` built entirely from backend's `GET /pipeline` — zero backend-specific constants in PromptPotter.

### Three Entry Points

1. **Notebook** (primary): `notebooks/optimization_campaign.ipynb` — `promptpotter/display/campaign/` is pure display, delegates to services
2. **CLI**: `promptpotter/cli/campaign_runner.py` — `init → [task-context] → [scan] → [scan-results] → optimize → results`
3. **FastAPI API**: `promptpotter/main.py` — `/api/v1/backends`, `/api/v1/campaigns`

**CLI session directory** (`{backend_id}/sessions/{session_id}/`): `session.json`, `campaign_state.json` (live state, `control.requested_state` for pause/resume/stop), `campaign_output.log`, `campaign_log.md`.

### Key Patterns

- **Store**: `ProjectStore` facade over focused stores in `services/stores/`.
- **Error handling**: `graceful()` context manager in `shared/errors.py`. `EscalationError` carries structured `partial_results`.
- **Graceful interrupt**: First Ctrl+C finishes in-flight call and saves; second force-quits. No completed work discarded.
- **HITL mode**: `RunConfig.pause_before_eval` raises `PauseForReviewError` between L1 generate and evaluate. Candidates persisted to `round_NNNN_candidates.json` before pause.
- **Optimizer LLM calls**: All go through `llm_call()` in `config/optimizer_pipeline.py`, not `chat()` directly.
- **`shared/`**: Leaf-level utilities only — no domain model or service dependencies allowed.

## Design Principles

- **Prompt decomposition & variant library** — Backends have monolithic prompts. PromptPotter decomposes into 8 independent fields via LLM restructure, perturbs each independently. See `docs/prompt-scheme.md`.
- **Prompt alias groups** — `register_alias`/`resolve_aliases` link equivalent prompt hashes so historical data is discoverable across forms. Transitive resolution.
- **Cross-campaign learning via SearchMemory** (M8) — Materialized view over `dataset_runs/`. Three pillars: parameter impact, query patterns, failure modes. Atomic accessors only.

## Known Issues

### Notebook ↔ CLI Session Parity

The notebook has no `session_id` — scan/campaign results don't persist. Root cause: display layer wrappers accept `session_id` but notebook never passes one. Both entry points must eventually produce identical artifacts (whitelabel prerequisite).

### TermNorm Backend

- **`llm_ranking` broken — always exclude.** Produces `json_validate_failed` on ~50% of queries, 7–16s latency, falls back anyway. Set `"exclude_nodes": ["llm_ranking"]`. Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **Without `llm_ranking`, prompt string fields have no effect.** Only `entity_profiling` has an LLM with its own fixed template. Optimization focuses on pipeline params: `entity_profiling` (model, temperature, schema), `web_search` (max_sites, num_results), `token_matching` (max_token_candidates), `fuzzy_matching` (threshold, scorer).

## Roadmap

M0–M7 complete (archived). **M8 complete** — Campaign Intelligence (SearchMemory, all 17 waves). **M9 future** — Multi-Connector Architecture. Benchmarks (HotPotQA, GSM8K) planned post-M8.

## Testing

Minimal suite — only stable contracts tested. No volume tests, no O(n) complexity. Mock: `monkeypatch` for async, stdlib `unittest.mock` — no pytest-mock. See `tests/CLAUDE.md`.

## Navigation

1. [`docs/architecture.md`](docs/architecture.md) — system design, two-loop diagram, caching, disk layout
2. [`docs/optimization.md`](docs/optimization.md) — L1/L2/L3 loop, critique, escalation
3. [`docs/prompt-scheme.md`](docs/prompt-scheme.md) — 8-field decomposition, variant library
4. [`docs/sensitivity-scan.md`](docs/sensitivity-scan.md) — OAT scan, coverage
5. [`docs/cli-workflow.md`](docs/cli-workflow.md) — full CLI reference, eval output format
6. [`docs/node-standard.md`](docs/node-standard.md) — node types, `llm_call()` primitive
7. [`docs/specs/`](docs/specs/CLAUDE.md) — active milestone specs (M8, M9)
8. [`docs/observability.md`](docs/observability.md), [`docs/setup-guide.md`](docs/setup-guide.md), [`docs/benchmarks.md`](docs/benchmarks.md), [`docs/information-flow.md`](docs/information-flow.md)
