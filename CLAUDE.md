# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter is an **LLM-driven program evolution** system for prompts and pipelines. Given a dataset + an LLM pipeline endpoint, each round it evolves a population of candidate configurations through a 3-layer loop: **L1** (generate population → measure fitness → critique), **L2** (refine the search neighbourhood on stall), **L3** (replan the meta-strategy when L2 stalls). Four LLM nodes: `restructure` (one-time), `l1` (`l1_generate` + `l1_critique` sub-phases), `l2_context`, `l3_plan`. Backend can be a single LLM call or a multi-step pipeline. Tested with TermNorm; primary publication benchmark is BBEH.

**Recon archived.** The sensitivity-scan / recon pass had no callers in the active loop and was deleted from `main` to stop paying the update tax on every cross-cutting refactor. Code is preserved at the `recon-archive` git tag; restore with `git checkout recon-archive -- promptpotter/application/recon/`. Last design notes: `docs/specs/m9-stable-config-and-scaffolding.md` (Track 7 post-ship note).

**Self-healing optimization — two rails.** Failures attach to the individual that produced them (per-individual `OptSearchPoint.memory`), never to the round. Rail 1 (`ValidationFailure`, pre-fitness): L2 teaches L1 what not to propose. Rail 2 (`RuntimeFailure`, mid-fitness): L2 adjusts its own strategy; L3 replans if the pattern persists. Full mechanics in [`docs/developer/self-healing-internals.md`](docs/developer/self-healing-internals.md).

**Data vs. scoring policy — rescore-on-load + decision-replay + fork.** Traces are facts; scores are policy. Each trace carries a ledger of `{scorer_id: {score, hit, formula}}`; every load boundary rescores under the active scorer. On resume, recorded decisions are replayed against rescored inputs — first mismatch halts so the operator can review. Rerun with `optimize --fork-on-divergence` to mint a sibling cycle rooted at the divergence point (with a `parent_cycle_id` pointer) and continue under the current scorer. Recorded kinds: `round_winner`, `elimination_cut`, `l2_escalation_trigger`, `l3_escalation_trigger`, `probe_round_commitment`; the first four are divergence-gated, probe is archive-only (LLM-output projection, invariant under pure scorer swap). Each record is two-tier — `inputs_ref` + `outcome` drive divergence, `data` sidecar archives LLM output / diagnostics and is never compared. Full mechanics in [`docs/concepts/scoring-and-traces.md`](docs/concepts/scoring-and-traces.md) and [`docs/operations/rewind-and-fork.md`](docs/operations/rewind-and-fork.md).

**Exploration / exploitation sample selection — Rasch + Knowledge Gradient.** Off by default (`CampaignConfig.optimization.adaptive_prefix.enabled`). Between rounds, `runner.py::_maybe_evolve_adaptive_prefix` refits a Rasch IRT posterior on accumulated `(candidate, sample, hit)` triples, then trades understood samples for high-info ones on the scoring prefix. Wilcoxon pairing intact within a round. Full mechanics: [`docs/methods/exploration-exploitation.md`](docs/methods/exploration-exploitation.md). The δ_s leaderboard this produces is the seed of a standalone capability — [`docs/specs/hard-sample-sorter.md`](docs/specs/hard-sample-sorter.md).

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
- **Ruff** line-length 100.
- **Logging** via `logging` module, never `print()`. Setup in `promptpotter/config/logging.py`.
- **No backward compatibility** — freely break signatures, rename, restructure. No shims.
- Pipeline components are called **nodes**, not "building blocks" or "services".
- **Terminology** — "eval" is banned from identifiers (function/class/variable/field names; use **loop**, **round**, **searchpoint**, **sample**, **measurement**, **scoring**, **match**). User-facing display labels may use natural English. **Domain framing** — PromptPotter is an instance of **LLM-driven program evolution**; new identifiers, doc sentences, and display copy should draw vocabulary from that domain: *evolve / evolution*, *generation*, *population*, *fitness*, *mutation*, *selection*, *individual*.
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **No fallbacks** in service code. Sole sanctioned exception: `_score_candidates()` validation-failure synthetic-0 — when `OptSearchPoint.memory.validation_failures` is non-empty the candidate loop synthesizes a `{accuracy: 0.0, invalid: True}` report instead of running an invalid SearchPoint. Any new fallback must be documented alongside this one.
- **Init never evaluates**: `init` is pure prep (load prompt + dataset, compute cycle hash, create session dir). The baseline runs as phase 0 of `optimize` on the **same top-N slice L1 uses** (`sample_dataset(dataset, sp_budget_ttest)` — deterministic prefix; datasets are already shuffled at creation, no second RNG). Identical call, identical output, so baseline fills the per-query cache in the exact shape L1 round 1 consumes. `sp_budget_ttest` stays on `CampaignConfig.optimization` and never enters `pipeline_params`, so the `JobSearchPoint` hash is target-layer-pure. **Budget contract:** baseline runs the *full* `sp_budget_ttest` slice with no early-stop (it is the t-test prior). Each round's candidates run *up to* `sp_budget_ttest`, but `EliminationCheck` (`elimination_n_min=4`, `elimination_alpha=0.2`, Wilcoxon signed-rank + Holm-Bonferroni) can truncate a clearly-inferior candidate as early as query 4. There is no `--skip-baseline` flag.
- **CLI timeouts**: 30 seconds default for ALL CLI commands. Only increase when told "ready for data collection".
- **No background CLI commands**: Never run `campaign_runner` with `run_in_background`. Always foreground.
- Version: `APP_VERSION` in `promptpotter/config/settings.py`.
- **Commit messages**: keep under 800 characters total. Terse bullets, not prose.
- **Sample IDs**: `sample_id: int` is optional on each sample (`QueryResult.sample_id`). It's an **internal positional index** assigned by the loader over the final merged list — not a canonical upstream ID. BBEH assigns sequential ints over its flattened 23-task mini list; display shows `#NNN` in the per-query line when present.

## Architecture

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema — pure, no I/O
├── application/
│   ├── campaign/    # campaign lifecycle + thin orchestration
│   ├── optimization/  # THE CORE LOOP — L1/L2/L3 nodes, l1_critique, llm_call, restructure
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
- **Control** (per-entry-point) — `stop_check` callable on `Session` (CLI polls a flag; notebook uses kernel interrupt). MUST NOT write campaign artifacts.

**SearchPoint hierarchy** — `JobSearchPoint` (frozen target spec, pipeline_params) and `PromptTemplate` → `OptSearchPoint` (optimizer state + memory). All services: `f(SearchPoint, PipelineSchema, dataset) → scores`. Every state traced at both layers: `JobSearchPoint` → `dataset_runs/` (content-addressed, shared); `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint). Details in [`docs/developer/code-layout.md`](docs/developer/code-layout.md).

**Scoring pipeline** — `score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway. Three early-exit paths (full-run cache hit, validation-failure synthetic-0, mid-eval escalation) live in `application/optimization/nodes/l1/measure.py::score_candidates` and are detailed in [`docs/developer/self-healing-internals.md`](docs/developer/self-healing-internals.md).

**Pipeline params** — always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from backend's `GET /pipeline` — zero backend-specific constants in PromptPotter.

## Entry Points (Maturity Order)

1. **Notebook** (primary): `notebooks/optimization_campaign.ipynb`; `presentation/ui/campaign/` is pure display.
2. **CLI**: `python -m promptpotter` at `presentation/cli/`. Core path: `init → [set-task] → optimize → show-results`.
3. **Claude skill `/potter-run`**: `.claude/skills/potter-run/SKILL.md` — operator-style entry point that drives the CLI from a chat session; resume-by-default, dataset-aware.
4. **FastAPI REST API**: `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
5. **Next.js webapp** (planned M10/M11): zero code today; consumes FastAPI API.

Features land left → right. Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`; live-phase per-query output is notebook-only pending M9 Track 4.

## Superuser Monitoring (live runs)

The cleanest live-monitoring setup for an operator running `python -m promptpotter optimize`:

1. **Open `campaigns/{cycle_id}/dashboard.json` in an editor that auto-reloads.** This is the live scalar state — phase, round, candidate, query, baseline / best / current accuracy, in-flight query payload, HITL signals, and `current_round.nodes` (the per-round node I/O snapshot, including per-candidate per-sample HIT/MISS lines under `l1_score.output.candidates[].samples`). Rewritten on every callback (per-query to per-candidate cadence). Renderer: [`presentation/views/dashboard.py::render_dashboard()`](promptpotter/presentation/views/dashboard.py), also reachable via `python -m promptpotter show-status`.

2. **Watch CLI stdout in the terminal that's running `optimize`.** [`presentation/views/live_cli.py::CliDisplay`](promptpotter/presentation/views/live_cli.py) prints per-query HIT/MISS lines, per-candidate summaries, and round-complete banners — same data dashboard.json carries, but in narrative order with tqdm progress bars.

3. **Drill into peer files in the same `campaigns/{cycle_id}/` directory when the dashboard isn't enough:**
   - `output.log` — append-only HIT/MISS history (raw, ungrouped, fast to tail).
   - `phase_events.jsonl` — structured event trace, one JSON per line.
   - `trials/trial_NNNN.json` — per-round optimizer checkpoint (critique text, l2_directive, escalation state).
   - `candidates/round_NNNN.json` — full per-round node I/O including the L1 leaderboard with scores, eliminations, and change descriptions.
   - `index.json` — campaign metadata + trial index.

`log.md` was deleted in this cycle — it duplicated dashboard.json + trials + candidates with no unique fields. For a narrative view of a finished round, read `trials/trial_NNNN.json` and `candidates/round_NNNN.json` directly, or run `python -m promptpotter show-results`.

**Alternatives to the dashboard.json-tail workflow:**

- **`/potter-run` skill** ([`.claude/skills/potter-run/SKILL.md`](.claude/skills/potter-run/SKILL.md)) — chat-driven operator session that preps configs, runs `optimize`, reads dashboard.json + trials between rounds, and summarizes. Combines well with the dashboard.json tail.
- **Notebook** (`notebooks/optimization_campaign.ipynb`) — drives the same loop in-process; live-phase per-query rendering is currently notebook-only.
- **Webapp** — minimal read-only dashboard planned on top of the FastAPI surface (`promptpotter/main.py`); zero code today.

The HITL control file (`control.json`) lives in the **session** directory (`sessions/{session_id}/`), not the cycle directory — pause/resume/stop via `python -m promptpotter control pause|resume|stop` or by editing the file directly.

**Active session pointer** (`.promptpotter/active_session.json`): stores `{tenant_id, session_id, cycle_id}`. Written by `init`, read by every other command. `--session <id>` overrides `session_id`; `--tenant <id>` selects the partition (default `"default"`).

**Persistence: two trees (sessions + campaigns).** Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.
- `{tenant_id}/sessions/{session_id}/` — operator session metadata: `session.json`, `journal.md` / `notes.md` (notebook ↔ Claude exchange), `control.json` (HITL signals). The currently-active cycle for the workspace is recorded in `.promptpotter/active_session.json` (single source of truth).
- `{tenant_id}/campaigns/{cycle_id}/` — per-cycle optimization artifacts: `index.json` (campaign metadata + trial index + `parent_session_id`), `dashboard.json`, `output.log`, `phase_events.jsonl`, `trials/trial_NNNN.json`, `candidates/round_NNNN.json`, langfuse shadow, prompts.
- `{tenant_id}/library/` — cross-cycle reference: datasets, backends, dataset_runs, mlruns, search_memory, aliases.

Full tree in [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md); state schema and resume flow in `infrastructure/persistence/session_emitter.py` and `application/campaign/runner.py`.

## Key Patterns

- **Two-object boundary**: user knobs live on `CampaignConfig` (Pydantic, nested sub-models, `extra='forbid'` — typos raise at load, not silently drop); everything else (session identity, loop infra, scoring env) lives on `Session` in `application/campaign/campaign_setup.py`. Services take whichever they need. Nothing mutates user config; `configure_and_apply_pipeline` writes derived `pipeline_params` onto `session`, not onto `campaign_config`.
- **Store**: `Stores` bundle + `build_stores(projects_root, tenant_id="default")` — frozen composite over focused leaf stores (BackendStore, SessionStore, CampaignStore, DatasetRunStore).
- **Error handling**: `graceful()` context manager in `shared/errors.py`. Escalation flows via `QueryLoopResult.escalation_signal` (return value, not exception).
- **Graceful interrupt**: First Ctrl+C finishes in-flight call and saves; second force-quits.
- **HITL pause**: runtime via `control.json::requested_state` (`pause` / `resume` / `stop`, CLI: `python -m promptpotter control pause`); the optimizer reads at the `after_round` checkpoint and raises `PauseForReviewError`. No static config flag.
- **Optimizer LLM calls**: all go through `llm_call()` in `application/optimization/pipeline.py`, not `chat()` directly.
- **Cycle identity**: hash covers only *what problem* the cycle solves (active steps + baseline prompt + dataset). Loop-control / strategy knobs are excluded so tweaking optimizer strategy or resuming with different budgets does not create a new cycle. See `cycle_config_identity()` in `domain/cycle_identity.py`.
- **Two-tier sampling**: `sp_budget_ttest` controls the optimization loop scoring set. Sequential elimination early-stops inferior candidates via the Wilcoxon signed-rank test.
- **Canonical prompt authoring**: dataset starting prompts live in `datasets/{name}/prompts/{node}.json` (or `default.json`) as `PromptTemplate` JSON. Monolithic `prompt` strings in `pipeline.json` are deprecated.
- **Round-boundary scoring-set mutations** — two sanctioned writers, in this order: (1) **Zero-signal filter** (off by default, `min_observations=5`): queries with 0 variance across ≥ N samples physically moved to `datasets/{name}.json::excluded`; mutates the on-disk dataset. (2) **Exploration/exploitation rebalance** (off by default, code symbol `adaptive_prefix`): Rasch + KG swaps understood samples ↔ high-info samples in the in-memory `session.scoring_dataset` only; never touches disk. No other mutation of either is sanctioned.

## Known Issues

### Notebook ↔ CLI Session Parity

**Campaign path closed:** `run_optimization` auto-mints a session+cycle pair when caller passes `session_id=""`, producing the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`.

**M9 Track 4:** Shared file-directory view model — renderer unification is still that track's work.

### TermNorm Backend

- **`llm_ranking` broken — always exclude.** Produces `json_validate_failed` on ~50% of queries. Set `"exclude_nodes": ["llm_ranking"]`. Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **Without `llm_ranking`, prompt string fields have no effect.** Only `entity_profiling` has an LLM. Optimization focuses on pipeline params.

## Roadmap

**M11 is the headline** — multi-connector architecture (`ConnectorProtocol`, connector registry, second backend), competitor head-to-head, webapp Phase 2 (launch + live monitoring). M9 (stable config, hexagonal layout, multi-dataset, file-directory UI v0) and M10 (BBEH benchmarks, ablation, webapp read-only) are backbone work in front of M11, not destinations. M9 Tracks 2 + 7 are done; Tracks 1, 3, 4 in progress. M0–M8 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Testing

Minimal suite — only stable contracts tested. No volume tests, no O(n) complexity. Mock: `monkeypatch` for async, stdlib `unittest.mock`. See `tests/CLAUDE.md`.

## Navigation

**Manual (users)**: [`manual/`](docs/manual/README.md) — numbered walkthrough, install → first run → reading output → troubleshooting.

**Concepts (how it works, concept-first)**: [`campaign-lifecycle.md`](docs/concepts/campaign-lifecycle.md), [`three-layer-loop.md`](docs/concepts/three-layer-loop.md), [`self-healing.md`](docs/concepts/self-healing.md), [`scoring-and-traces.md`](docs/concepts/scoring-and-traces.md), [`search-memory.md`](docs/concepts/search-memory.md), [`prompts-and-individuals.md`](docs/concepts/prompts-and-individuals.md), [`nodes-and-pipelines.md`](docs/concepts/nodes-and-pipelines.md), [`glossary.md`](docs/concepts/glossary.md)

**Developer (implementation)**: [`code-layout.md`](docs/developer/code-layout.md), [`information-flow.md`](docs/developer/information-flow.md), [`node-standard.md`](docs/developer/node-standard.md), [`prompt-scheme-internals.md`](docs/developer/prompt-scheme-internals.md), [`search-memory-internals.md`](docs/developer/search-memory-internals.md), [`self-healing-internals.md`](docs/developer/self-healing-internals.md), [`display-conventions.md`](docs/developer/display-conventions.md), [`code-map.md`](docs/developer/code-map.md)

**Operations**: [`cli-reference.md`](docs/operations/cli-reference.md), [`environment.md`](docs/operations/environment.md), [`backend-integration.md`](docs/operations/backend-integration.md), [`persistence-and-state.md`](docs/operations/persistence-and-state.md), [`rewind-and-fork.md`](docs/operations/rewind-and-fork.md), [`observability.md`](docs/operations/observability.md)

**Methods**: [`candidate-elimination.md`](docs/methods/candidate-elimination.md), [`exploration-exploitation.md`](docs/methods/exploration-exploitation.md)

**Research**: [`benchmarks.md`](docs/research/benchmarks.md), [`metrics.md`](docs/research/metrics.md), [`related-work.md`](docs/research/related-work.md)

**Specs**: [`docs/specs/`](docs/specs/CLAUDE.md) — active (M9, M10, M11, M11+), archived (M8, old M9)
