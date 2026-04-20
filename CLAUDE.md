# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter finds better prompts automatically. Give it a dataset + an LLM pipeline endpoint — it tries prompt and parameter variations, measures accuracy, and iterates through a critique-guided 3-layer loop (L1 generate + score → critique → L2 refine → L3 replan). Five LLM call sites in the core: `restructure` (one-time), `l1_generate`, `critique`, `l2_context`, `l3_plan`. Backend can be a single LLM call or a multi-step pipeline. Tested with TermNorm; primary publication benchmark is BBEH.

**Note — `application/recon/` is a preserved template, not dead code.** The sensitivity-scan / recon pass is kept as a working-shape reference for anyone who wants to revive it later. It is dormant by design: no CLI subcommand, no UI wrapper, no L1 parameter, no `CampaignConfig` field references it. **Do not remove it** — the structure itself is the spec. To revive, re-wire the seams; otherwise ignore.

**Self-healing optimization — two rails.** Failures attach to the candidate that produced them (per-candidate `OptSearchPoint.memory`), never to the round. Rail 1 (`ValidationFailure`, pre-eval): L2 teaches L1 what not to propose. Rail 2 (`RuntimeFailure`, mid-eval): L2 adjusts its own strategy; L3 replans if the pattern persists. Full mechanics in [`docs/architecture/optimization.md § Self-healing optimization`](docs/architecture/optimization.md).

**Data vs. scoring policy — rescore-on-load + decision-replay + fork.** Traces are facts; scores are policy. Each trace carries a ledger of `{scorer_id: {score, hit, formula}}`; every load boundary rescores under the active scorer. On resume, recorded decisions are replayed against rescored inputs — first mismatch halts with a fork hint. `python -m promptpotter fork` mints a new cycle rooted at the divergence point with a `parent_cycle_id` pointer. Recorded kinds: `round_winner`, `elimination_cut`, `l2_escalation_trigger`, `l3_escalation_trigger`, `probe_round_commitment`; the first four are divergence-gated, probe is archive-only (LLM-output projection, invariant under pure scorer swap). Each record is two-tier — `inputs_ref` + `outcome` drive divergence, `data` sidecar archives LLM output / diagnostics and is never compared. Full mechanics in [`docs/architecture/optimization.md § Data vs. scoring policy`](docs/architecture/optimization.md).

## Commands

```bash
# Install (dev — everything bundled)
pip install -e ".[all,dev]"

# Verify everything (~5s, minimal output)
python -m ruff check promptpotter/ tests/ -q && python -m ruff format --check promptpotter/ tests/ -q && python -m deptry . && python -m mypy promptpotter/ --no-error-summary && python -m pytest tests/ --tb=no -q -p no:warnings

# Individual checks
python -m ruff check promptpotter/ tests/     # lint
python -m ruff format promptpotter/ tests/    # format (auto-fix)
python -m mypy promptpotter/                  # type check
python -m pytest tests/                       # all tests
python -m pytest tests/ -k "test_name"        # single test

# Run API server
uvicorn promptpotter.main:app --port 8001 --reload

# CLI workflow
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/lca-termnorm/campaign.json
python -m promptpotter set-task --task-file datasets/lca-termnorm/task_description.md
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
- **Terminology** — "eval" is banned from identifiers (function/class/variable/field names; use **loop**, **round**, **searchpoint**, **sample**, **measurement**, **scoring**, **match**). User-facing display labels may use natural English.
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **No fallbacks** in service code. Sole sanctioned exception: `_score_candidates()` validation-failure synthetic-0 — when `OptSearchPoint.memory.validation_failures` is non-empty the candidate loop synthesizes a `{accuracy: 0.0, invalid: True}` report instead of running an invalid SearchPoint. Any new fallback must be documented alongside this one.
- **Init never evaluates**: `init` is pure prep (load prompt + dataset, compute cycle hash, create session dir). The baseline runs as phase 0 of `optimize` on the **same top-N slice L1 uses** (`sample_dataset(dataset, sp_budget_ttest)` — deterministic prefix; datasets are already shuffled at creation, no second RNG). Identical call, identical output, so baseline fills the per-query cache in the exact shape L1 round 1 consumes. `sp_budget_ttest` stays on `CampaignConfig.optimization` and never enters `pipeline_params`, so the `JobSearchPoint` hash is target-layer-pure. **Budget contract:** baseline runs the *full* `sp_budget_ttest` slice with no early-stop (it is the t-test prior). Each round's candidates run *up to* `sp_budget_ttest`, but `EliminationCheck` (`elimination_n_min=4`, `elimination_alpha=0.2`, Wilcoxon signed-rank + Holm-Bonferroni) can truncate a clearly-inferior candidate as early as query 4. There is no `--skip-baseline` flag.
- **CLI timeouts**: 30 seconds default for ALL CLI commands. Only increase when told "ready for data collection".
- **No background CLI commands**: Never run `campaign_runner` with `run_in_background`. Always foreground.
- Version: `APP_VERSION` in `promptpotter/config/settings.py`.
- **Commit messages**: keep under 800 characters total. Terse bullets, not prose.

## Architecture

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema, ScoringEnv — pure, no I/O
├── application/
│   ├── campaign/    # campaign lifecycle + thin orchestration
│   ├── optimization/  # THE CORE LOOP — L1/L2/L3 nodes, critique, llm_call, restructure
│   ├── recon/         # TEMPLATE — dormant sensitivity-scan archive, preserved for future revival
│   ├── intelligence/  # SHARED materialized view — SearchMemory, variant_library
│   ├── scoring/       # score_search_point gateway
│   └── datasets/
├── infrastructure/  # store/, backend/, llm/, tracing/, persistence/
├── presentation/    # cli/, api/, ui/ — thin per-surface adapters
├── shared/          # leaf utilities (errors, constants)
└── config/          # settings, APP_VERSION, logging
```

**Directionality rule (strict):** `intelligence/` MUST NOT import from `optimization/` — it's shared ground.

**Three-layer I/O architecture (INVARIANT):**
- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. New artifacts go in `CAMPAIGN_ARTIFACTS` (per-cycle, in `campaigns/{cycle_id}/`) or `SESSION_ARTIFACTS` (per-session, in `sessions/{session_id}/`); `tests/test_artifact_parity.py` enforces both sets.
- **Display** (per-entry-point) — caller passes `RunListener`. MUST NOT write to disk.
- **Control** (per-entry-point) — `FileControlSurface` (CLI) or kernel interrupt (notebook). MUST NOT write campaign artifacts.

**SearchPoint hierarchy** — `JobSearchPoint` (frozen target spec, pipeline_params) and `PromptTemplate` → `OptSearchPoint` (optimizer state + memory). All services: `f(SearchPoint, PipelineSchema, dataset) → scores`. Every state traced at both layers: `JobSearchPoint` → `dataset_runs/` (content-addressed, shared); `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint). Details in [`docs/architecture/overview.md`](docs/architecture/overview.md).

**Scoring pipeline** — `score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway. Three early-exit paths (full-run cache hit, validation-failure synthetic-0, mid-eval escalation) live in `application/optimization/nodes/score.py::_score_candidates` and are detailed in [`docs/architecture/optimization.md`](docs/architecture/optimization.md).

**Pipeline params** — always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from backend's `GET /pipeline` — zero backend-specific constants in PromptPotter.

## Entry Points (Maturity Order)

1. **Notebook** (primary): `notebooks/optimization_campaign.ipynb`; `presentation/ui/campaign/` is pure display.
2. **CLI**: `python -m promptpotter` at `presentation/cli/`. Core path: `init → [set-task] → optimize → show-results`.
3. **FastAPI REST API**: `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
4. **Next.js webapp** (planned M10/M11): zero code today; consumes FastAPI API.

Features land left → right. Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`; live-phase per-query output is notebook-only pending M9 Track 4.

**Active session pointer** (`.promptpotter/active_session.json`): stores `{tenant_id, session_id, cycle_id}`. Written by `init`, read by every other command. `--session <id>` overrides `session_id`; `--tenant <id>` selects the partition (default `"default"`).

**Persistence: two trees (sessions + campaigns).** Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.
- `{tenant_id}/sessions/{session_id}/` — operator session metadata: `session.json`, `journal.md` / `notes.md` (notebook ↔ Claude exchange), `control.json` (HITL signals). The currently-active cycle for the workspace is recorded in `.promptpotter/active_session.json` (single source of truth).
- `{tenant_id}/campaigns/{cycle_id}/` — per-cycle optimization artifacts: `index.json` (campaign metadata + trial index + `parent_session_id`), `dashboard.json`, `output.log`, `log.md`, `trials/trial_NNNN.json`, `candidates/round_NNNN.json`, langfuse shadow, events.jsonl, prompts.
- `{tenant_id}/library/` — cross-cycle reference: datasets, backends, dataset_runs, mlruns, search_memory, aliases.

Full tree in [`docs/architecture/overview.md § Persistence`](docs/architecture/overview.md); state schema and resume flow in `infrastructure/persistence/session_emitter.py` and `application/campaign/runner.py`.

## Key Patterns

- **Three-object boundary** (M9 Track 7): user knobs live on `CampaignConfig` (Pydantic, nested sub-models, `extra='forbid'` — typos raise at load, not silently drop); session identity + infra + runtime-derived state live on `SessionEnv` (`session_id`, `cycle_id`, `project_root`, `pipeline_schema`, `pipeline_params`); transient loop infra lives on `LoopEnv`. Services take whichever two they need. Nothing mutates user config; `configure_and_apply_pipeline` writes derived `pipeline_params` onto `session`, not onto `campaign_config`.
- **Store**: `Stores` bundle + `build_stores(projects_root, tenant_id="default")` — frozen composite over focused leaf stores (BackendStore, SessionStore, CampaignStore, DatasetRunStore, PlanStore).
- **Error handling**: `graceful()` context manager in `shared/errors.py`. Escalation flows via `QueryLoopResult.escalation_signal` (return value, not exception).
- **Graceful interrupt**: First Ctrl+C finishes in-flight call and saves; second force-quits.
- **HITL mode**: `RunConfig.pause_before_scoring` raises `PauseForReviewError` between L1 generate and score.
- **Optimizer LLM calls**: all go through `llm_call()` in `application/optimization/pipeline.py`, not `chat()` directly.
- **Cycle identity**: two-tier. Experiment mode (default) hashes only the problem; strict mode (`strict_cycle_identity: true`) hashes everything for publication reproducibility. See `TUNING_KEYS` in `lifecycle.py`.
- **Two-tier sampling**: `sp_budget_ttest` controls the optimization loop scoring set. Sequential elimination early-stops inferior candidates via the Wilcoxon signed-rank test.
- **Canonical prompt authoring**: dataset starting prompts live in `datasets/{name}/prompts/{node}.json` (or `default.json`) as `PromptTemplate` JSON. Monolithic `prompt` strings in `pipeline.json` are deprecated.
- **Zero-signal sample filtering** (on by default, `min_observations=5`): queries with 0 variance across ≥ N samples are physically moved to `datasets/{name}.json::excluded` at round boundaries. Only sanctioned round-boundary mutation of the active dataset.

## Known Issues

### Notebook ↔ CLI Session Parity

**Campaign path closed:** `run_optimization` auto-mints a session+cycle pair when caller passes `session_id=""`, producing the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`.

**M9 Track 4:** Shared file-directory view model — renderer unification is still that track's work.

### TermNorm Backend

- **`llm_ranking` broken — always exclude.** Produces `json_validate_failed` on ~50% of queries. Set `"exclude_nodes": ["llm_ranking"]`. Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **Without `llm_ranking`, prompt string fields have no effect.** Only `entity_profiling` has an LLM. Optimization focuses on pipeline params.

## Roadmap

M0–M8 complete. **M9 next** — stable config, hierarchy refactor, multi-dataset/pipeline, file-directory UI v0. **M10** — BBEH benchmarks, ablation studies, webapp read-only views. **M11** — multi-connector, webapp Phase 2. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Testing

Minimal suite — only stable contracts tested. No volume tests, no O(n) complexity. Mock: `monkeypatch` for async, stdlib `unittest.mock`. See `tests/CLAUDE.md`.

## Navigation

**Architecture**: [`overview.md`](docs/architecture/overview.md), [`optimization.md`](docs/architecture/optimization.md), [`prompt-scheme.md`](docs/architecture/prompt-scheme.md), [`information-flow.md`](docs/architecture/information-flow.md), [`node-standard.md`](docs/architecture/node-standard.md), [`display-conventions.md`](docs/architecture/display-conventions.md), [`search-memory-intelligence.md`](docs/architecture/search-memory-intelligence.md)

**Operations**: [`cli-workflow.md`](docs/cli-workflow.md), [`setup-guide.md`](docs/setup-guide.md), [`observability.md`](docs/observability.md)

**Research**: [`benchmarks.md`](docs/research/benchmarks.md)

**Specs**: [`docs/specs/`](docs/specs/CLAUDE.md) — active (M9, M10, M11, M11+), archived (M8, old M9)
