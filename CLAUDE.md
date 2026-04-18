# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter finds better prompts automatically.

**Core — the optimization loop.** Give it a dataset + an LLM pipeline endpoint — it tries prompt and parameter variations, measures accuracy, and iterates through a critique-guided 3-layer optimization loop (L1 generate + evaluate → L2 refine → L3 replan), with a critique node between rounds. Five LLM call sites total: `restructure` (one-time campaign setup), `l1_generate`, `critique`, `l2_context`, `l3_plan`. This is the product. Backend can be a single LLM call or a multi-step pipeline. Tested with TermNorm; primary publication benchmark is BBEH (see [`docs/research/benchmarks.md`](docs/research/benchmarks.md)).

**Optional — the sensitivity scan.** A separate, human-driven pre-step that measures which prompt/parameter axes matter before optimization starts. One LLM call site (`recon_advisor`) plus a one-at-a-time perturbation runner. Lives in its own `application/recon/` package and is fully optional — `optimize` runs end-to-end without it. When used, the scan produces a `ReconBrief` that is passed into the optimizer as a starting-point hint; that handoff is the **only** sanctioned bridge between the two features.

**🔁 Self-healing optimization — two rails.** Failures attach to the candidate that produced them (per-candidate `OptSearchPoint.memory`), never to the round, so a losing candidate's problem never disrupts the round winner. New self-healing mechanisms must pick one of the two existing rails — do not invent a sidecar, do not silently drop, do not just log.

- **Rail 1 — L1 self-healing via L2 directive** (`ValidationFailure`, pre-eval). When L1 proposes a structurally invalid candidate (e.g. `model: gpt-4o` when the allowed set is `[openai/gpt-oss-120b]`), the failure is detected at parse time, recorded on **`OptSearchPoint.memory.validation_failures`**, drives a synthetic-0 early exit (zero backend calls), and surfaces in `candidate_scores`. L2 reads it next round and emits an explicit directive (`"use ONLY one of: …"`) that **teaches L1 what not to propose**. L1 is the healer; L2 is the teacher; L3 has no role — L2 can always articulate the missing constraint. Flow: `detect → attach → synthetic-0 → surface → L2 directive → L1 heals`.

- **Rail 2 — L2 self-healing with L3 escalation** (`RuntimeFailure`, mid-eval). When a candidate runs with in-range parameters but produces high runtime warning rates (e.g. `max_tokens=150` → 100% `empty_content_reasoning_fallback` on a reasoning model), the `DegradationCheck` synthesises a `RuntimeFailure` from the check result, attaches it to that **single candidate's** `OptSearchPoint.memory.runtime_failures`, eliminates only that candidate, and lets the rest of the round finish. Per-candidate `RuntimeFailures` are **also mirrored cumulatively onto the outer `state.opt_sp.memory.runtime_failures`** so the trail survives across rounds. L2 next round sees both partitions (*NEW this round* and *ACCUMULATED surviving from earlier rounds*) and **updates its own strategy** — tightens the directive, refines `task_context`, adjusts `optimizer_params` — to re-shape what L1 is allowed to search over. L2 is the healer (not L1 as in rail 1). When the `ACCUMULATED` list keeps growing despite L2's adjustments, L3 `modify_plan` reads the same trail and replans the pipeline itself (change `pipeline_params`, swap nodes, rewrite `plan` text). L2 is the first healer; L3 is the escalation. Flow: `detect → attach per-candidate → real score stands → mirror to outer memory → L2 adjusts own strategy → (if pattern persists) L3 replans`.

Both rails share the rule *"detect → trace on the candidate → surface on `candidate_scores` → feed the right teacher"* but diverge on **who the teacher is** and **what the healing action looks like**. See `docs/architecture/optimization.md` "Self-healing optimization" for the full mechanics and `docs/architecture/display-conventions.md` for the `⚠ … ↳` rendering convention.

## Commands

```bash
# Install (dev — everything bundled)
pip install -e ".[all,dev]"

# Verify everything (~5s, minimal output)
python -m ruff check promptpotter/ tests/ -q && python -m ruff format --check promptpotter/ tests/ -q && python -m deptry . -q && python -m mypy promptpotter/ --no-error-summary && python -m pytest tests/ --tb=no -q -p no:warnings

# Individual checks
python -m ruff check promptpotter/ tests/     # lint
python -m ruff format promptpotter/ tests/    # format (auto-fix)
python -m mypy promptpotter/                  # type check
python -m pytest tests/                       # all tests
python -m pytest tests/ -k "test_name"        # single test

# Run API server
uvicorn promptpotter.main:app --port 8001 --reload

# CLI workflow
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/lca-termnorm/campaign.json --skip-baseline
python -m promptpotter set-task --task-file datasets/lca-termnorm/task_description.md
python -m promptpotter recon --variants-file datasets/lca-termnorm/recon_variants.json
python -m promptpotter show-recon
python -m promptpotter optimize             # full loop
python -m promptpotter show-results
python -m promptpotter show-status          # live dashboard
```

CI runs: `ruff check` → `ruff format --check` → `deptry` → `mypy` → `pytest`. All must pass.

## Code Conventions

- **Python 3.13+**. Type hints: PEP 604 (`X | None`, `list[str]`) — no `Optional`, no `List`.
- **Ruff** line-length 100, McCabe max complexity 15.
- **Logging** via `logging` module, never `print()`. Setup in `promptpotter/config/logging.py`.
- **No backward compatibility** — freely break signatures, rename, restructure. No shims.
- Pipeline components are called **nodes**, not "building blocks" or "services".
- **Terminology** — "eval" is banned from identifiers. Use: **loop** (optimization loop), **round** (one iteration), **searchpoint** (configuration being tested), **sample** (one query from the dataset), **measurement** (running one sample through the pipeline + comparing to ground truth), **scoring** (aggregating measurements across a dataset for a SearchPoint), **match** (expected-vs-actual comparison).
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **No fallbacks** in service code. Sanctioned exceptions (keep this list short):
  1. `_score_candidates()` validation-failure synthetic-0: when `OptSearchPoint.memory.validation_failures` is non-empty, the candidate loop synthesizes a 0-accuracy report and skips the backend instead of running an invalid candidate. This is *not* a "default value when the real one fails" — it is the result for a structurally invalid SearchPoint, computed from the validation failure itself, with no hidden retry. See "Scoring Pipeline" → "Three early-exit paths" below.
  Any doc or code introducing a new fallback must add it to this list.
- **Cycle identity**: Two-tier system. Experiment mode (default) hashes only the *problem* (dataset, baseline, pipeline steps) — everything else (optimizer model, seed, n_variants, creativity, patience, thresholds) is tweakable without breaking the cycle. Strict mode (`strict_cycle_identity: true`) hashes everything — for publication reproducibility only. See `TUNING_KEYS` in `lifecycle.py` and `docs/research/benchmarks.md` "Reproducibility: Cycle Identity Modes".
- **Two-tier sampling**: `sp_budget_ttest` (must be > 0) controls the optimization loop scoring set. `recon_sample_size` controls sensitivity scan queries. Sequential elimination early-stops inferior candidates via Welch's t-test after 20 queries, so actual round cost is well below `n_variants × eval_size`.
- **Skip baseline by default**: Always `init --skip-baseline`. The optimizer auto-measures a baseline on the `sp_budget_ttest` slice before round 1 when `run_baseline=False` and the starting prompt is non-empty, so L1/L2/critique/winner-selection all see a real reference anchor instead of a fake 0.0%. Only run explicit full-dataset baseline (`--baseline`) when substantial historical data exists (≥ 50 unique queries, ≥ 5 dataset runs) and the user requests comparison.
- **CLI timeouts**: 30 seconds default for ALL CLI commands. Only increase when told "ready for data collection".
- **No background CLI commands**: Never run `campaign_runner` with `run_in_background`. Always foreground so stale processes don't leak.
- Version: `APP_VERSION` in `promptpotter/config/settings.py`.
- **Commit messages**: keep under 900 characters total (subject + body + trailers). Terse bullets, not prose.

## Architecture

### Mental Model

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`. Four entry points — see § Four Entry Points (Maturity Order). All entry points produce identical persistent artifacts via the three-layer I/O architecture.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema, ScoringEnv, TenantContext — pure, no I/O
├── application/
│   ├── campaign/           # campaign lifecycle + thin orchestration (setup, runner, config)
│   ├── optimization/       # THE CORE LOOP: L1/L2/L3 nodes, critique, llm_call, restructure, prompts/
│   │   └── nodes/
│   ├── recon/              # OPTIONAL human loop — recon_advisor, recon_runner, adaptive_recon, recon_report, coverage, failure_groups
│   ├── intelligence/       # SHARED materialized view — SearchMemory, variant_library, eval_set_adaptation. Feeds both loops, imports from neither.
│   ├── scoring/            # scoring gateway (score_search_point), stale_data
│   ├── datasets/
│   └── pipeline_discovery.py
├── infrastructure/  # store/, backend/, llm/, tracing/, persistence/ (state, control, session_emitter, round_recorder)
├── presentation/    # cli/, api/, ui/ — thin adapters per surface
├── shared/          # leaf utilities (errors, constants, scoring formula compiler)
└── config/          # settings, APP_VERSION, logging
```

**Directionality rule (strict):** `optimization/` MUST NOT import from `recon/`. `recon/` MAY import from `optimization/` for shared primitives (`llm_call`, `decompose_prompt_fields`, etc.) — recon runs before optimization as campaign setup. `intelligence/` MUST NOT import from either `recon/` or `optimization/` — it's shared ground. The **only** sanctioned runtime bridge between recon and optimization is `ReconBrief` flowing through `RunConfig.recon_brief` into L1.

**Three-layer I/O architecture (INVARIANT):**
- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. New artifacts → `CAMPAIGN_SESSION_ARTIFACTS` in `infrastructure/persistence/session_emitter.py`; `tests/test_artifact_parity.py` enforces.
- **Display** (per-entry-point) — caller passes `RunCallbacks`. MUST NOT write to disk.
- **Control** (per-entry-point) — `FileControlSurface` (CLI) or kernel interrupt (notebook). MUST NOT write campaign artifacts.

**Core loop + optional scan:** The **optimization loop** (L1 generate → L1 evaluate → critique → L2 refine → L3 replan) is the product and always runs. The **sensitivity scan** is an optional, human-driven pre-step that explores which axes matter and hands a `ReconBrief` to the optimizer as a starting-point hint. They are independent features in independent packages; skipping the scan leaves the optimization loop fully functional. All evaluation data from both (and from any earlier run) is archived to `dataset_runs/` store. SearchMemory (lives in `intelligence/`, M8) aggregates historical data into a materialized view that feeds both features without either importing the other. Three-tier intelligence: deterministic code triage (CI-gated query exclusion, no LLM), critique (every-round intelligence hub — enriched with SearchMemory tractability, axis exhaustion, value trends), L2 (escalation-only — round trajectory, candidate comparison, failure group × axis). L3 receives SearchMemory aggregate picture. L1 stays clean. See [`docs/architecture/search-memory-intelligence.md`](docs/architecture/search-memory-intelligence.md).

### SearchPoint Hierarchy

```
SearchPoint (abstract — render())
  ├── JobSearchPoint       — frozen target-layer spec (pipeline_params)
  └── PromptTemplate       — 8-field prompt decomposition (persona, task_intent, etc.)
      └── OptSearchPoint   — optimizer state (lineage, L2/L3, memory, escalation)
```

All services follow: `f(SearchPoint, PipelineSchema, dataset) → scores`. `JobSearchPoint` is the first positional arg to `score_search_point()`. `OptSearchPoint` is the source of truth for all optimizer state; projected to `JobSearchPoint` via `to_job_search_point()`.

### Two-Layer Tracing

Every state traced at **both** layers independently:
- **Target layer**: `JobSearchPoint` → `score_search_point()` → `dataset_runs/` (content-addressed, shared)
- **Optimizer layer**: `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint)

### Scoring Pipeline

`score_search_point()` (in `application/scoring/search_point_scorer.py`) is the single gateway for scoring, archival, and observability. Reuse is per-query: `find_by_node_configs()` in `dataset_run_store` matches prior dataset runs via `PipelineSchema.node_configs()` (ordered `[(name, config)]` list). Exact matches reuse every non-error item; partial matches reuse items whose `terminated_at` falls within the shared prefix. `sp_hash = stable_hash(node_configs)` serves as the archive identity. `BackendClient` translates `pipeline_params` to wire-format `node_config`.

**Three early-exit paths around `score_search_point()`** (the candidate loop in `application/optimization/nodes/score.py::_score_candidates` shortcuts each one before calling the gateway):
1. **Full-run cache hit** — prior `dataset_runs/` entry covers this exact searchpoint; results are replayed and the candidate scored without any backend calls.
2. **Validation failure** — the candidate's `OptSearchPoint.memory.validation_failures` is non-empty (an L1-proposed value was outside the user-declared allowed set, e.g. `model: gpt-4o` when `PipelineSchema.available_models = [openai/gpt-oss-120b]`). The candidate is structurally invalid; the loop synthesizes a `{accuracy: 0.0, invalid: True, validation_failures: [...]}` report and skips the gateway entirely. **Zero backend calls are spent on invalid candidates.** The failure is fully traced — persisted on the OptSearchPoint trace, surfaced in `dashboard.json.last_scoring_metadata`, fed into L2 refine_strategy as a self-healing signal, rendered in the notebook UI with the `⚠ … ↳` convention, and the existing accuracy comparator deprioritizes the candidate in `_select_round_winner`. See `docs/architecture/optimization.md` "Validation failures as SearchPoint properties".
3. **Mid-evaluation escalation** — the per-query check protocol (`DegradationCheck`, `EliminationCheck`, `EmptyOutputCheck`) returns an `EscalationSignal` mid-loop. Elimination/empty-output signals are absorbed inside `_score_candidates` (see `EscalationTarget.ELIMINATE_CANDIDATE`); only true degradation propagates to the runner.

### Pipeline Params — Two Namespaces

Always **nested dicts** keyed by node name (`{"web_search": {"max_sites": 5}}`). `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical split. See [`docs/architecture/information-flow.md`](docs/architecture/information-flow.md) for L1 override routing and the `l1_generate()` auto-nest safety net.

### Self-Describing Pipeline

`PipelineSchema` built entirely from backend's `GET /pipeline` — zero backend-specific constants in PromptPotter.

### Four Entry Points (Maturity Order)

Features land left → right. Notebook is the daily driver and the testing ground; the webapp is a polish layer on top of whatever the first three surfaces expose. When adding a feature, prove it in the notebook first, then the CLI, then the API, then the webapp. Do not invert this order.

1. **Notebook** (primary): `notebooks/optimization_campaign.ipynb` — `promptpotter/presentation/ui/campaign/` is pure display, delegates to `application/`. Most features land here first.
2. **CLI**: `python -m promptpotter` — scripted and local workflows. Core path: `init → [set-task] → optimize → show-results`. Optional recon step: `recon → show-recon` inserted between set-task and optimize. Lives at `presentation/cli/`.
3. **FastAPI REST API**: `promptpotter/main.py` mounts routers from `presentation/api/` — `/api/v1/backends`, `/api/v1/campaigns`. Programmatic access for automation and the webapp.
4. **Next.js webapp** (planned, M10 → M11): browser surface, consumes the FastAPI API. Reads the M9 file-directory view model. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

**Active session pointer** (`.promptpotter/active_session.json`): Stores `{tenant_id, cycle_id}` of the current campaign (v3). Written by `init`, read by every other command. Works like a browser's active tab — `optimize`, `show-status`, `show-results` etc. all operate on the active campaign automatically. `--session <id>` overrides it. `init` always creates a new cycle and overwrites the pointer. `--tenant <id>` selects the tenant partition (default `"default"`).

**Persistence: session ≡ campaign (v3).** One mint point. Per-cycle artifacts live under `{tenant_id}/campaigns/{cycle_id}/` (dashboard, control, logs, trial_NNNN, candidates, langfuse shadow, events.jsonl, prompts); cross-cycle reference lives under `{tenant_id}/library/` (datasets, backends, dataset_runs, recon_plans, mlruns, search_memory, aliases). See `docs/architecture/overview.md § Persistence` for the full tree; state schema and resume flow in `promptpotter/infrastructure/persistence/session_emitter.py` and `application/campaign/lifecycle.py`.

### Key Patterns

- **Store**: `Stores` bundle + `build_stores(projects_root, tenant_id="default")` in `infrastructure/store/` — frozen composite over focused leaf stores (BackendStore, CampaignStore, DatasetRunStore, PlanStore). `SessionStore` was merged into `CampaignStore` in Wave A (session ≡ campaign).
- **Error handling**: `graceful()` context manager in `shared/errors.py`. Escalation signals flow via `QueryLoopResult.escalation_signal` (return value, not exception).
- **Graceful interrupt**: First Ctrl+C finishes in-flight call and saves; second force-quits. No completed work discarded.
- **HITL mode**: `RunConfig.pause_before_scoring` raises `PauseForReviewError` between L1 generate and score. Candidates persisted to `round_NNNN_candidates.json` before pause.
- **Optimizer LLM calls**: All go through `llm_call()` in `application/optimization/pipeline.py`, not `chat()` directly. The recon advisor also routes through this primitive (recon → optimization is allowed).
- **`shared/`**: Leaf-level utilities only — no domain model or service dependencies allowed.
- **Recon/optimization isolation**: `optimization/` must not import from `recon/`. `intelligence/` must not import from either. Sole sanctioned bridge is `RunConfig.recon_brief`. Enforced by review; breaking this couples an optional feature into the core loop.

## Design Principles

- **Prompt decomposition & variant library** — Backends have monolithic prompts. PromptPotter decomposes into 8 independent fields via LLM restructure, perturbs each independently. See `docs/architecture/prompt-scheme.md`.
- **Canonical prompt authoring** — Dataset starting prompts live in `datasets/{name}/prompts/{node}.json` (or `default.json` for single-node datasets) as 6-field `PromptTemplate` JSON. Authoring a monolithic `prompt` string inside `pipeline.json` `nodes.{node}.config` is deprecated and warned about at pipeline load. Listing `"prompt"` as a single atomic axis in `optimizer.param_keys` is deprecated — use the 6 canonical field names instead so L1 can perturb each axis independently.
- **Prompt alias groups** — `register_alias`/`resolve_aliases` link equivalent prompt hashes so historical data is discoverable across forms. Transitive resolution.
- **Cross-campaign learning via SearchMemory** (M8) — Materialized view over `dataset_runs/` with three pillars (parameter impact, query patterns, failure modes) and three-tier intelligence (deterministic triage, critique, L2). See [`docs/architecture/search-memory-intelligence.md`](docs/architecture/search-memory-intelligence.md) for architecture detail.
- **events.jsonl is a human navigation log, not a WAL** — `obs/langfuse/events.jsonl` is an append-only flat index for local inspection; nothing reads it back for state reconstruction. Resume and mid-cycle rewind (`optimize --from <round>`, int) are driven by `campaigns/{cycle_id}/trial_NNNN.json`, which carries the full serialized `OptSearchPoint`. FileSink also writes a parallel Langfuse-schema JSON mirror and MLflow run dirs under `obs/` for offline tooling. See [`docs/architecture/optimization.md § Resuming mid-cycle`](docs/architecture/optimization.md#resuming-mid-cycle) and [`docs/observability.md`](docs/observability.md).
- **Zero-signal sample filtering** (on by default, gated by `min_observations=5`) — Queries with variance 0 across ≥ `zero_signal_filter_min_observations` samples (symmetric over always-hit and always-miss) are physically moved from `datasets/{name}.json::items` to `datasets/{name}.json::excluded` at round boundaries. Fires from `campaign/runner.py::_maybe_apply_zero_signal_filter` after `SearchMemory.on_round_complete()`. **Only sanctioned round-boundary mutation of the active dataset.** Not a fallback — deterministic dataset shrinking driven entirely by observed data. See [`docs/architecture/search-memory-intelligence.md § Zero-Signal Sample Filtering`](docs/architecture/search-memory-intelligence.md).

## Known Issues

### Notebook ↔ CLI Session Parity

**Campaign path closed:** `run_optimization` auto-mints a session when the caller passes `session_id=""` and claims the active pointer, so notebook/smoke/future-API runs produce the same five `CAMPAIGN_SESSION_ARTIFACTS` as CLI `init`. CLI path unchanged — it still mints at `init` and passes the id through (no double-mint).

**Still open — scan path:** `run_sensitivity_scan` does not yet auto-mint; recon results don't persist from the notebook. Follow-up applies the same pattern.

**M9 Track 4:** Shared file-directory view model — renderer unification is still that track's work.

### TermNorm Backend

- **`llm_ranking` broken — always exclude.** Produces `json_validate_failed` on ~50% of queries, 7–16s latency, falls back anyway. Set `"exclude_nodes": ["llm_ranking"]`. Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **Without `llm_ranking`, prompt string fields have no effect.** Only `entity_profiling` has an LLM with its own fixed template. Optimization focuses on pipeline params: `entity_profiling` (model, temperature, schema), `web_search` (max_sites, num_results), `token_matching` (max_token_candidates), `fuzzy_matching` (threshold, scorer).

## Roadmap

M0–M7 complete (archived). **M8 complete** — Campaign Intelligence (SearchMemory, all 17 waves). **M9 next** — Stable config, hierarchy refactor, multi-dataset/pipeline, file-directory UI v0. **M10** — BBEH benchmarks, ablation studies, webapp read-only views. **M11** — Multi-connector, competitor comparison, webapp Phase 2 (launcher + live monitoring). **M11+** — Backlog (cost tracking, multimodal, MCP, self-optimization). See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Testing

Minimal suite — only stable contracts tested. No volume tests, no O(n) complexity. Mock: `monkeypatch` for async, stdlib `unittest.mock` — no pytest-mock. See `tests/CLAUDE.md`.

## Navigation

**Architecture** (how it works):
1. [`docs/architecture/overview.md`](docs/architecture/overview.md) — system design, two-loop diagram, caching, disk layout
2. [`docs/architecture/optimization.md`](docs/architecture/optimization.md) — L1/L2/L3 loop, critique, escalation
3. [`docs/architecture/prompt-scheme.md`](docs/architecture/prompt-scheme.md) — 8-field decomposition, variant library
4. [`docs/architecture/information-flow.md`](docs/architecture/information-flow.md) — prompt injection map
5. [`docs/architecture/node-standard.md`](docs/architecture/node-standard.md) — node types, `llm_call()` primitive

**Operations** (how to use it):
6. [`docs/cli-workflow.md`](docs/cli-workflow.md) — full CLI reference, scoring output format
7. [`docs/setup-guide.md`](docs/setup-guide.md), [`docs/observability.md`](docs/observability.md)

**Research** (methodology & analysis):
8. [`docs/research/benchmarks.md`](docs/research/benchmarks.md), [`docs/architecture/search-memory-intelligence.md`](docs/architecture/search-memory-intelligence.md)

**Specs**: [`docs/specs/`](docs/specs/CLAUDE.md) — active (M9, M10, M11, M11+), archived (M8, old M9)
