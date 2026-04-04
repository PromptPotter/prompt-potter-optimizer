# CLAUDE.md

## What This Is

PromptPotter Optimizer is a backend-first prompt optimization service. It connects to LLM application backends (currently TermNorm), syncs experiment data, replays pipelines with different configurations, and runs L1/L2/L3 optimization campaigns to improve prompt accuracy.

## Commands

```bash
# Run API server
uvicorn promptpotter.main:app --port 8001 --reload

# Run tests
pytest -v --tb=short

# Lint
ruff check promptpotter/ tests/

# Docker (JupyterLab + API)
cd docker && docker-compose up --build

# CLI campaign runner (HITL optimization from terminal)
python -m promptpotter.cli.campaign_runner init --backend-url http://127.0.0.1:8000
python -m promptpotter.cli.campaign_runner optimize --round   # generate → pause for review
python -m promptpotter.cli.campaign_runner optimize --evaluate # resume evaluation
python -m promptpotter.cli.campaign_runner optimize --auto     # full loop, no pause
```

## CLI Workflow

The CLI campaign runner (`promptpotter/cli/campaign_runner.py`) follows a strict subcommand sequence. Each step persists to `SessionStore` so progress survives interrupts. Config via `--config` JSON file.

```
init ──→ [task-context] ──→ [scan] ──→ [scan-results] ──→ optimize ──→ results
```

**Critical data flow:** `configure_pipeline(svc, campaign_config)` produces `pipeline_params` (with `exclude_nodes` applied). This must be threaded to every eval call — baseline, scan, and optimization. The configured `pipeline_params` is stored in `state["pipeline_params"]` and in the session directory.

**Session directory** (`{backend_id}/sessions/{session_id}/`):
- `session.json` — config, phase, pipeline_params, cycle_id, best_accuracy
- `campaign_state.json` — live optimization state (overwritten per update, carries counters across cycles via `resume_from`)
- `campaign_output.log` — append-only eval log (ANSI-stripped)
- `campaign_log.md` — structured campaign report

**Bidirectional control:** Edit `campaign_state.json`'s `control.requested_state` to `"pause"`, `"resume"`, or `"stop"`. Set `control.pause_before_l2_eval: true` to pause after L2 generates new context. Or use `python -m promptpotter.cli.campaign_runner control --pause`.

See [`docs/cli-workflow.md`](docs/cli-workflow.md) for the full subcommand reference.

## Mental Model

Three entry points (Jupyter notebook, CLI runner, web app), one service core in `promptpotter/services/`. The notebook is `notebooks/optimization_campaign.ipynb`; `campaign_lib` wraps services with display. The CLI (`promptpotter/cli/campaign_runner.py`) is a parallel orchestration layer with HITL support — generates candidates, pauses for human/AI review, then evaluates. All entry points produce identical persistent artifacts via the three-layer architecture:

**Three-layer I/O architecture (INVARIANT):**
- **Persistence** (shared, mandatory) — `run_optimization()` auto-creates `CampaignPersistenceEmitter` (`promptpotter/services/campaign/persistence_emitter.py`). Entry points MUST NOT write campaign artifacts directly — all persistence flows through the emitter. New artifacts must be added to `CAMPAIGN_SESSION_ARTIFACTS` (`promptpotter/services/campaign/artifacts.py`); `tests/test_artifact_parity.py` enforces this.
- **Display** (per-entry-point) — caller passes `display_callbacks: CycleCallbacks`. MUST NOT write to disk.
- **Control** (per-entry-point, optional) — `FileControlSurface` (CLI/web) or kernel interrupt (notebook). MUST NOT write campaign artifacts.

**Two loops:** Human sensitivity scan (explore which axes matter) feeds the AI critique-guided optimization loop (L1 generate → L1 evaluate → L2 refine → L3 replan). All evaluation data shares one `dataset_runs/` store via content-addressed dedup. SearchMemory *(M8 — live)* aggregates all historical evaluation data into a materialized view (parameter impact, query patterns, failure modes) that feeds both loops.

**Two-layer tracing:** Target layer (JobSearchPoint → dataset_runs/) and optimizer layer (OptSearchPoint → campaign trials). Both independently reconstructable from disk.

**Pipeline composability:** `pipeline_params` (nested dicts keyed by node name) throughout PromptPotter. `node_config` only at the TermNorm wire boundary.

**Per-query cache matching:** Two cache layers. (1) `dataset_runs` cache: `find_cached_queries()` uses `config_hash(pipeline_params, rp_hash)` — a canonical hash of normalized pipeline config (prompt/output_schema stripped) + prompt identity. Exact match only. (2) `IntermediateCache`: step-sequence prefix matching. When the intermediate cache covers ALL target steps, `eval_query_via_backend()` short-circuits — constructs the result locally without any backend call. See [`docs/architecture.md` § Evaluation Flow](docs/architecture.md#evaluation-flow).

**Two parameter namespaces:** Prompt scheme fields (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format` — rendered into a prompt string by `render()`) vs pipeline node params (nested dicts like `{"token_matching": {"thinking_style": "..."}}` — sent to backend nodes). These are orthogonal and may share names. L1 candidates use `pipeline_params_override` for both: keys matching `PROMPT_STRING_FIELDS` auto-route to `derive_candidate()` (updating prompt scheme fields); all other keys are nested under their node name (`{"web_search": {"max_sites": 5}}`). **No flat param format** — all pipeline params use nested format from LLM output through to backend. See [`docs/prompt-scheme.md`](docs/prompt-scheme.md).

See [`docs/architecture.md`](docs/architecture.md) for diagrams, caching, pipeline discovery, and disk layout.

## Data Model Reference

All services follow: `f(SearchPoint, PipelineSchema, eval_data) → scores`.

```
SearchPoint (abstract — render())
    ├── JobSearchPoint      — target evaluation space (pipeline_params, frozen)
    └── PromptTemplate      — 8-field prompt scheme (render/compile)
            └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

`JobSearchPoint` bundles `pipeline_params` for frozen target eval. `OptSearchPoint.to_job_search_point()` projects optimizer state into target-layer. `PipelineSchema` describes pipelines. `EvalContext` bundles eval infrastructure.

See [`docs/architecture.md` § Data Models](docs/architecture.md#data-models) for the full class hierarchy, methods, and fields.

## Eval Output Format

Per-query result lines printed during evaluation, scan, and optimization. Generated by `_fmt_query_result()` in `notebooks/campaign_lib/display.py`.

```
[  3]  12.1s MISS 8/20 [web📖]->[llm]  SJRG0013-PA/molding                            -> Glass fibre reinforced plastic | 60
│      │     │    │     │               │                                                  │
│      │     │    │     │               query (45 chars)                                   prediction (35 chars)
│      │     │    │     node trace: which pipeline nodes ran
│      │     │    ground-truth rank / total candidates (MISS only; --/20 = GT absent)
│      │     HIT = GT is top-ranked, MISS = not
│      total backend time
global query counter across all candidates
```

**Node trace tags:** `cach` · `fuzz` · `webS` · `ai_1` · `rank` · `ai_2`. Derived from `PipelineSchema.build_display_tags()`: per-node `display_tag` override → `WIRE_TYPE_TAGS[wire_type]` default → name[:4]. Auto-enumerated when multiple nodes share the same base tag. `📖` = cached (precomputed_through). `[webS📖]->[ai_2]` means cached through web_search, pipeline resumed at the next node after web, and terminated at llm_ranking.

**Annotation lines** (indented below the result):

| Marker | Meaning |
|--------|---------|
| `⚠ node: message` | Per-node diagnostic warning (e.g. token limit error) |
| `↩ degraded observed (1/3 toward rerun)` | Stale data protocol: degraded cache detected, counting toward rerun threshold |
| `🔄 rerun of degraded cache` | Query re-evaluated fresh after hitting degradation threshold |
| `🔬 samplescan probe` | Probe round query |
| `🔀 switched out (unreliable)` | Query removed from sample as unreliable |
| `⚠ persistently degraded` | Degradation persists even after rerun |

## Service Catalog

| Service | Purpose |
|---------|---------|
| `prompt_eval.py` | Evaluate prompts via backend `/matches` — single eval gateway + stale data load protocol |
| `l1_optimizer.py` | L1 candidate generation (`l1_generate`) and winner selection (`l1_evaluate`) |
| `backend_client.py` | HTTP client for backend APIs (sync, replay, `fetch_pipeline()`) |
| `pipeline_discovery.py` | Parses `GET /pipeline` response into `PipelineSchema` |
| `project_store.py` | Facade over focused store modules in `stores/` (incl. `SessionStore`) |
| `campaign/optimization_loop.py` | L1→L2→L3 optimization loop with patience-based stopping |
| `campaign/layer_transitions.py` | L2 (`task_context` + meta-settings), L3 (plan) |
| `campaign/campaign_init.py` | Campaign init, `resolve_experiment_id()`, experiment overrides |
| `search/smart_search.py` | Sensitivity scan (OAT), adaptive search |
| `search/scan_advisor.py` | LLM-driven scan recommendations |
| `search/coverage.py` | Historical index, step-sequence coverage matching |
| `search/search_memory.py` | Cross-campaign intelligence materialized view *(M8 — live)* |
| `obs/observability_logger.py` | Langfuse-compatible traces, MLflow |
| `stores/session_store.py` | Session lifecycle — config, scan results, campaign log (shared by notebook + CLI) |
| `cli/campaign_runner.py` | CLI campaign runner — HITL optimization with `pause_before_eval` |
| `llm_client.py` | Unified LLM abstraction (Groq, OpenAI) with exponential backoff |

## Project Conventions

### Code Style

- **CLI command timeouts**: **30 seconds default for ALL CLI commands** — including `init`, `optimize`, `scan`. This is the diagnostic/development phase; we are analyzing algorithm behavior, not collecting data. 30s is enough to observe one round starting, check output, and verify fixes. Only increase timeout when explicitly told "ready for data collection".
- **No background CLI commands**: Never run CLI campaign commands (`campaign_runner`) in the background or with `run_in_background`. Always foreground so stale processes don't leak and spam the backend. After any interrupted CLI run, verify with `ps aux | grep python` and kill orphans with `taskkill //PID <pid> //F`.
- **Type hints**: PEP 604 (`X | None`), lowercase generics (`list[str]`)
- **Logging**: `logging` module (no `print()` in services). Setup in `promptpotter/config/logging.py`.
- **`sample_size`**: Universal eval sampling parameter (0 = all). No synonyms.
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields. Surfaces schema violations immediately rather than hiding them behind silent defaults.

### Architecture

- **No backward compatibility** — freely break signatures, rename, restructure. No shims, no dual-format readers. Old data is regenerated, not supported.
- **Pipeline reproducibility**: Notebook displays full pipeline config via `GET /pipeline` before any evaluation.
- **EXPERIMENT_ID**: Single source of truth. Config must match stored experiment when set.
- **Display parity**: Cached and fresh results use the same output format. A provenance indicator distinguishes data source for transparency.
- **Graceful interrupt**: Signal-flag pattern — first Ctrl+C finishes in-flight call and saves; second force-quits. No completed work is ever discarded. See [architecture.md § Caching & Crash Recovery](docs/architecture.md#caching--crash-recovery).
- **Error handling**: `graceful()` context manager in `campaign/helpers.py` is the standard suppress-and-log pattern. `EscalationError` carries structured `partial_results` for campaign flow control.
- **`promptpotter/shared/`**: Leaf-level utilities shared by models and services (hashing, schema mutations). No domain model or service dependencies allowed.
- **`promptpotter/shared/constants.py`**: Canonical source for `PROMPT_STRING_FIELDS`, `LAYER_FIELDS`, and `LAYER1_STRING_FIELDS`. All modules must import field lists from here — never define them locally.
- **`promptpotter/config/optimizer_pipeline.py`**: Optimizer pipeline schema loader + `llm_call()` primitive. All optimizer nodes use this instead of calling `chat()` directly.
- **HITL mode**: `CycleConfig.pause_before_eval` raises `PauseForReviewError` between L1 generate and L1 evaluate. Candidates are already persisted to `round_NNNN_candidates.json` before the pause. On resume (`run_optimization()` with same `cycle_id`), `load_round_candidates()` loads them and evaluation proceeds. Campaign finalized as `"paused"` with `StopReason.PAUSED_FOR_REVIEW`.
- **SessionStore**: `store.sessions` manages cross-phase state (`session.json`, `scan_results.json`, `campaign_log.md`) under `{backend_id}/sessions/{session_id}/`. All entry points use the same store — pass `session_id` to `sensitivity_scan()` / `run_optimization_notebook()` to activate persistence.
- **Nested-only pipeline params**: All pipeline params use nested format (`{"node_name": {"param": value}}`) from LLM output through to backend. No flat-to-nested resolution. LLM prompt instructs nested output; `PROMPT_STRING_FIELDS` split separates prompt fields (inherently flat) from node params. `l1_generate()` has a safety net that auto-nests any flat params the LLM still emits.

## Design Principles

What's genuinely distinctive about how PromptPotter works.

- **Prompt decomposition & variant library** — Backends have monolithic prompts. PromptPotter decomposes them into independent fields via LLM restructure, then perturbs each field independently using a variant library. This turns one opaque prompt into a combinatorial search space where sensitivity scan can measure each axis and the feedback cycle can mutate specific fields. See [`docs/prompt-scheme.md`](docs/prompt-scheme.md).

- **SearchPoint hierarchy as atomic unit** — `SearchPoint` → `PromptTemplate` (8-field scheme) → `OptSearchPoint` (+ lineage, L2/L3, memory). `JobSearchPoint` bundles `pipeline_params` for frozen target eval. Content-hashable, prevents accidental mutation. See [`docs/architecture.md` § Data Models](docs/architecture.md#data-models).

- **Prompt alias groups** — `register_alias` / `resolve_aliases` link equivalent prompt hashes so historical data is discoverable across forms. Resolution is transitive. Main use case: linking the backend's original monolithic prompt to its LLM-restructured decomposed form.

- **Cross-campaign learning via SearchMemory** *(M8 — live)* — Evaluation data compounds across campaigns via a materialized view over `dataset_runs/`. Three pillars: parameter impact, query patterns, failure modes. Atomic data accessors only — each consumer composes what it needs. See [`docs/architecture.md` § SearchMemory](docs/architecture.md#searchmemory-m8-wave-3).

## Known Backend Issues (TermNorm)

- **`llm_ranking` node is broken — always exclude it.** The TermNorm `llm_ranking` node (LLM-driven re-ranking) is a leftover that produces `json_validate_failed` errors on ~50% of queries, triggers `max_tokens` retries, adds 7–16s latency per query, and falls back to token_matching scores anyway. Always set `"exclude_nodes": ["llm_ranking"]` in campaign configs. The `configs/campaign_default.json` already has this set. Do NOT attempt to enable it — the bug is in the backend, not in PromptPotter. The effective pipeline is: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **`direct_prompt` node** — Backend-only diagnostic node, not part of the optimization pipeline. Ignore it.
- **Without `llm_ranking`, prompt string fields have no effect.** The only LLM node in the active pipeline is `entity_profiling`, which has its own fixed prompt template. PromptPotter's prompt scheme fields (`thinking_style`, `instruction`, etc.) only affect nodes with a prompt template that references them. With `llm_ranking` excluded, optimization must focus on pipeline params: `entity_profiling` (model, temperature, schema, raw_content_limit), `web_search` (max_sites, num_results, content_char_limit, query_prefix), `token_matching` (max_token_candidates), `fuzzy_matching` (threshold, scorer).

## Evaluated & Rejected Refactorings

- **PromptDecomposition sub-model on OptSearchPoint**: Evaluated 2026-03-26 and rejected. 15+ `getattr(opt_sp, field)` iteration sites across 7 files depend on flat fields via `PROMPT_STRING_FIELDS`. Extracting to `opt_sp.prompt.field` would add indirection at every site without clarity gain.
- **OptimizationMemory sub-model on OptSearchPoint**: 9 memory fields accessed from 5 files in fragmented patterns. No clean seam exists.

## Navigation Guide

1. **This file** — overview, commands, data models, conventions, service catalog
2. [`docs/architecture.md`](docs/architecture.md) — system design, two-loop diagram, two-layer tracing, caching, pipeline discovery, disk layout
3. [`docs/optimization.md`](docs/optimization.md) — L1/L2/L3 optimization loop, critique agent, escalation, configuration
4. [`docs/node-standard.md`](docs/node-standard.md) — node type hierarchy, `llm_call()` primitive (`promptpotter/config/optimizer_pipeline.py`), pipeline declaration format
5. [`docs/sensitivity-scan.md`](docs/sensitivity-scan.md) — OAT scan workflow, coverage, circuit breaker
6. [`docs/observability.md`](docs/observability.md) — Langfuse, MLflow, events.jsonl
7. [`docs/setup-guide.md`](docs/setup-guide.md) — installation, quick start, REST API
8. [`docs/specs/`](docs/specs/CLAUDE.md) — active milestone specs (M8, M9) + roadmap; archived specs in `docs/specs/archive/`
9. [`docs/prompt-scheme.md`](docs/prompt-scheme.md) — prompt decomposition (8 fields), rendering, variant library, projection to target pipeline
10. [`docs/information-flow.md`](docs/information-flow.md) — data origins, consumer matrix, information compression chain
11. `promptpotter/cli/campaign_runner.py` — CLI campaign runner (HITL subcommands: init, scan, optimize --round/--evaluate/--auto, results, status)

