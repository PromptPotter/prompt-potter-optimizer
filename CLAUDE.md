# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PromptPotter is an **LLM-driven program evolution** system for prompts and pipelines. Given a dataset + an LLM pipeline endpoint, each round it evolves a population of candidate configurations through a 3-layer loop: **L1** (generate population → measure fitness → critique), **L2** (refine the search neighbourhood on stall), **L3** (replan the meta-strategy when L2 stalls). Four LLM nodes: `restructure` (one-time), `l1` (`l1_generate` + `l1_critique` sub-phases), `l2_context`, `l3_plan`. Backend can be a single LLM call or a multi-step pipeline. Tested with TermNorm; primary publication benchmark is BBEH.

**Recon archived.** The sensitivity-scan / recon pass had no callers in the active loop and was deleted from `main`. Code is preserved at the `recon-archive` git tag; restore with `git checkout recon-archive -- promptpotter/application/recon/`.

**Self-healing optimization — two rails.** Failures attach to the individual that produced them (per-individual `OptSearchPoint.memory`), never to the round. Rail 1 (`ValidationFailure`, pre-fitness): L2 teaches L1 what not to propose. Rail 2 (`RuntimeFailure`, mid-fitness): L2 adjusts its own strategy; L3 replans if the pattern persists. Full mechanics in [`docs/developer/self-healing-internals.md`](docs/developer/self-healing-internals.md).

**Data vs. scoring policy — rescore-on-load + decision-replay + fork.** Traces are facts; scores are policy. Each trace carries a ledger of `{scorer_id: {score, hit, formula}}`; every load boundary rescores under the active scorer. On resume, recorded decisions are replayed against rescored inputs — first mismatch halts so the operator can review. Rerun with `optimize --fork-on-divergence` to mint a sibling cycle rooted at the divergence point (with a `parent_cycle_id` pointer) and continue under the current scorer. Recorded kinds: `round_winner`, `elimination_cut`, `l2_escalation_trigger`, `l3_escalation_trigger`, `probe_round_commitment`; the first four are divergence-gated, probe is archive-only (LLM-output projection, invariant under pure scorer swap). Each record is two-tier — `inputs_ref` + `outcome` drive divergence, `data` sidecar archives LLM output / diagnostics and is never compared. Full mechanics in [`docs/concepts/scoring-and-traces.md`](docs/concepts/scoring-and-traces.md) and [`docs/operations/rewind-and-fork.md`](docs/operations/rewind-and-fork.md).

**Exploration / exploitation sample selection — Rasch + Knowledge Gradient.** Off by default (`CampaignConfig.optimization.scoring_set.enabled`). Between rounds, the `evolve_scoring_set()` step in `runner.py` refits a Rasch IRT posterior on accumulated `(candidate, sample, hit)` triples, then trades understood samples for high-info ones in the active scoring set (which sample IDs are in play next round). Wilcoxon pairing intact within a round. Full mechanics: [`docs/methods/exploration-exploitation.md`](docs/methods/exploration-exploitation.md). The δ_s leaderboard this produces is the seed of a standalone capability — [`docs/specs/hard-sample-sorter.md`](docs/specs/hard-sample-sorter.md).

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

# CLI workflow — only two write verbs.
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/lca-termnorm/campaign.json
python -m promptpotter optimize             # full loop; Ctrl+C to stop
```

**CLI mental model.** The CLI is two write verbs: `init` creates a session+cycle, `optimize` runs a campaign against it. Reads happen by opening the on-disk artifact tree (`sessions/{id}/`, `campaigns/{cycle_id}/`) — `dashboard.json` for live state, `log.md` for the digest, `index.json` for the final summary including `stop_reason`. Stop with Ctrl+C (first finishes in-flight and saves; second force-quits) — there is no mid-run pause/resume.

CI runs: `ruff check` → `ruff format --check` → `deptry` → `mypy` → `pytest`. All must pass.

## Code Conventions

- **Python 3.13+**. Type hints: PEP 604 (`X | None`, `list[str]`) — no `Optional`, no `List`.
- **Ruff** line-length 100.
- **Logging** via `logging` module, never `print()`. Setup in `promptpotter/config/logging.py`.
- **No backward compatibility** — freely break signatures, rename, restructure. No shims.
- Pipeline components are called **nodes**, not "building blocks" or "services".
- **Terminology** — "eval" (and `evaluate` / `evaluation`) is banned from identifiers and code prose, with **one sanctioned exception**: the `Evaluator` class in `application/scoring/evaluators.py` and its direct registry consumers (the `evaluators: dict[str, float]` field that carries per-round registry values, plus `all_evaluators()`, `materialize_round_values`, `materialize_query_values`). Anywhere else, use **loop**, **round**, **searchpoint**, **sample**, **measurement**, **scoring**, **match**, **fitness**, **trial**, **critique**. User-facing display copy and external-wire field names (e.g. TermNorm's `evaluation_results`) may use natural English. Python's `eval()` builtin in `application/scoring/formula.py` is the language keyword, not the term — fine. **Domain framing** — PromptPotter is an instance of **LLM-driven program evolution**; new identifiers, doc sentences, and display copy should draw vocabulary from that domain: *evolve / evolution*, *generation*, *population*, *fitness*, *mutation*, *selection*, *individual*.
- **Direct field access**: `dict[key]` not `.get(key, fallback)` for guaranteed fields.
- **No fallbacks** in service code. Sole sanctioned exception: `score_population()` validation-failure synthetic-0 — when `OptSearchPoint.validation_failures` is non-empty the candidate loop synthesizes a `{accuracy: 0.0, invalid: True}` report instead of running an invalid SearchPoint. The **deprecated-sample exclusion** in `_compute_accuracy` and the cache eviction in `score_search_point::_filter_deprecated_priors` are not fallbacks — they are load-boundary data-quality gates that drop measurements whose `classify_result()` (in `application/optimization/diagnostics.py`) returns a non-empty `fatal_codes` set (e.g. `llm_only:reasoning_budget_exhausted`) before the scoring layer runs, count them separately as `deprecated`, and force a fresh backend call on next encounter (tag: `retry_of_deprecated_cache`). The classifier derives fatal codes from raw response shape (advisory + `finish_reason` + `reasoning_tokens`) rather than string-matching a backend warning; rule table lives in `diagnostics.py`. Trace records are still archived to `library/dataset_runs/`. See [`docs/concepts/scoring-and-traces.md`](docs/concepts/scoring-and-traces.md#deprecated-samples) and [`docs/developer/self-healing-internals.md`](docs/developer/self-healing-internals.md#classify_result--fatal-classification). Any new fallback must be documented alongside these.
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
│   ├── intelligence/  # SHARED materialized view — AxisIndex, variant_library
│   ├── scoring/       # score_search_point gateway
│   └── datasets/
├── infrastructure/  # store/, backend/, llm/, tracing/, persistence/
├── presentation/    # cli/, api/, ui/ — thin per-surface adapters
├── shared/          # leaf utilities (errors, constants)
└── config/          # settings, APP_VERSION, logging
```

**Directionality rule (strict):** `intelligence/` MUST NOT import from `optimization/` — it's shared ground.

**Three-layer I/O architecture (INVARIANT):**
- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. Campaign artifacts split into two bands: **root telemetry** (`dashboard.json`, `output.log`) binds to the family root cycle (the one with no `parent_cycle_id`) — forks share one continuous live stream; **per-cycle audit** (`index.json`, `log.md`, `trials/`, `langfuse/`, `prompts/`, `archived/`, plus `.cache/candidates/` + `.cache/rounds/` for internal resume state) lives in each cycle's own dir. The allowlists (`ROOT_TELEMETRY_ARTIFACTS`, `PER_CYCLE_AUDIT_ARTIFACTS`, `CAMPAIGN_ARTIFACTS`, `SESSION_ARTIFACTS`) live in `tests/test_artifact_parity.py` — the test owns the contract.
- **Display** (per-entry-point) — caller passes `RunListener`. MUST NOT write to disk.
- **Control** (per-entry-point) — `stop_check` callable on `Session` (CLI polls a flag; notebook uses kernel interrupt). MUST NOT write campaign artifacts.

**SearchPoint hierarchy** — `JobSearchPoint` (frozen target spec, pipeline_params) and `PromptTemplate` → `OptSearchPoint` (optimizer state + memory). All services: `f(SearchPoint, PipelineSchema, dataset) → scores`. Every state traced at both layers: `JobSearchPoint` → `dataset_runs/` (content-addressed, shared); `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint). Details in [`docs/developer/code-layout.md`](docs/developer/code-layout.md).

**Scoring pipeline** — `score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway. Three early-exit paths (validation-failure synthetic-0, full-run cache hit, mid-eval escalation) live in `application/optimization/nodes/l1/measure.py::score_population` and are detailed in [`docs/developer/self-healing-internals.md`](docs/developer/self-healing-internals.md).

**Pipeline params** — always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from backend's `GET /pipeline` — zero backend-specific constants in PromptPotter.

## Entry Points (Maturity Order)

1. **Notebook** (primary): `notebooks/optimization_campaign.ipynb`; calls `application/` directly + `presentation/views/` for rendering. Display via the shared `LiveDisplay` (`presentation/views/live.py`); notebook orchestration (`init_notebook_session`, `prepare_scoring_context_notebook`, `run_optimization_notebook`) lives in `presentation/views/notebook_run.py`.
2. **CLI**: `python -m promptpotter` at `presentation/cli/`. Core path: `init → optimize`. Reads happen by opening `campaigns/{cycle_id}/`.
3. **Claude skill `/potter-run`**: `.claude/skills/potter-run/SKILL.md` — operator-style entry point that drives the CLI from a chat session; resume-by-default, dataset-aware.
4. **FastAPI REST API**: `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
5. **Next.js webapp** (planned M11/M12): zero code today; consumes FastAPI API.

Features land left → right. Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`; live-phase per-query output is notebook-only pending M9 Track 4.

## Superuser Monitoring (live runs)

The cleanest live-monitoring setup for an operator running `python -m promptpotter optimize`:

1. **Open `campaigns/{root_cycle_id}/dashboard.json` in an editor that auto-reloads.** This is the live scalar state — phase, round, candidate, query, baseline / best / current accuracy, in-flight query payload, and `current_round.nodes` (the per-round node I/O snapshot, including per-candidate per-sample HIT/MISS lines under `l1_score.output.candidates[].samples`). Rewritten on every callback (per-query to per-candidate cadence). For forked cycles, telemetry binds to the **family root** (the cycle with no `parent_cycle_id`); the active fork is identified by `dashboard.json::cycle_id` so a single tail covers the whole family.

2. **Watch CLI stdout in the terminal that's running `optimize`.** [`presentation/views/live.py::LiveDisplay`](promptpotter/presentation/views/live.py) prints per-query HIT/MISS lines, per-candidate summaries, and round-complete banners — same data dashboard.json carries, but in narrative order with tqdm progress bars.

3. **Drill into peer files when the dashboard isn't enough.** Layout splits into two bands:
   - At `campaigns/{root_cycle_id}/` (telemetry, shared across all forks of the family):
     - `output.log` — append-only HIT/MISS history (raw, ungrouped, fast to tail). Contains a `=== FORK <id> from round N (parent: ...) ===` banner at each cutover.
   - At `campaigns/{cycle_id}/` (per-cycle audit, one set per fork):
     - `log.md` — derived markdown digest, regenerated on every round-complete and at finalize. Status block, per-round critique / L2 directive / changes, hard-samples heatmap (when sorter enabled), final winner. Pure render over `index.json` + `trials/`; safe to delete and recompute.
     - `trials/trial_NNNN.json` — per-round optimizer checkpoint (critique text, l2_directive, escalation state).
     - `index.json` — campaign metadata + trial index, plus the `final` block (best/baseline/stop_reason/winner) once the cycle finishes.
     - `.cache/candidates/round_NNNN.json` — pre-scoring candidate checkpoint (resume state). Internal; overwritten next round.
     - `.cache/rounds/round_NNN.json` — per-round LLM action audit (developer artifact). Internal.

`optimize_result.json` and `hard_samples.json` were folded away this cycle: the final-run summary now lives at `index.json::final`, and the hard-samples heatmap is rendered as a section inside `log.md` instead of being its own file. Langfuse mirrors live under `campaigns/{cycle_id}/langfuse/` (including `langfuse/events.jsonl`); none of those are read for state reconstruction.

**Alternatives to the dashboard.json-tail workflow:**

- **`/potter-run` skill** ([`.claude/skills/potter-run/SKILL.md`](.claude/skills/potter-run/SKILL.md)) — chat-driven operator session that preps configs, runs `optimize`, reads dashboard.json + trials between rounds, and summarizes. Combines well with the dashboard.json tail.
- **Notebook** (`notebooks/optimization_campaign.ipynb`) — drives the same loop in-process; live-phase per-query rendering is currently notebook-only.
- **Webapp** — minimal read-only dashboard planned on top of the FastAPI surface (`promptpotter/main.py`); zero code today.

**Composite-score steering** (operator hot-swap): drop `campaigns/{cycle_id}/scoring_steer.json` with `{"per_round": "..."}` and the next round-end recompiles `session.round_scorer` against the new formula. Validation happens before the swap so a typo leaves state untouched. The default formula already includes a 5%-weighted `prompt_compactness` term that linearly penalizes prompts past 4 000 chars; crank the weight via the steer file when prompts grow round-over-round. Full playbook: [`docs/operations/improvement-tracking.md`](docs/operations/improvement-tracking.md).

**Active session pointer** (`.promptpotter/active_session.json`): stores `{tenant_id, session_id, cycle_id}`. Written by `init`, read by `optimize`. `--session <id>` overrides `session_id`; `--tenant <id>` selects the partition (default `"default"`).

**Persistence: two trees (sessions + campaigns).** Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.
- `{tenant_id}/sessions/{session_id}/` — operator session metadata: `session.json`, `journal.md` / `notes.md` (notebook ↔ Claude exchange). The currently-active cycle for the workspace is recorded in `.promptpotter/active_session.json` (single source of truth).
- `{tenant_id}/campaigns/{root_cycle_id}/` — root cycle's dir holds the family's telemetry stream (`dashboard.json`, `output.log`) plus the root's own per-cycle audit (`index.json` with `parent_session_id`, `log.md`, `trials/`, `langfuse/`, `prompts/`, plus `.cache/candidates/` and `.cache/rounds/` for internal resume state). Forks of this family nest under `campaigns/{root_cycle_id}/forks/{cycle_id}/` (audit only — telemetry stays at the root). Pre-existing flat-layout fork dirs are auto-migrated on store init (one-time, idempotent).
- `{tenant_id}/library/` — **the measurement archive — the database core, cross-cycle, cross-session, cross-tenant.** One row = `(sample × config → outcome)`. Two retrieval views, both first-class: `MeasurementArchive.measurements_for_sample(sample_id)` (by training example) and `MeasurementArchive.measurements_for_config(predicate)` (by searchpoint). Both return `list[Measurement]`. Cache reuse and LLM digests are derived views over this same archive. See [`docs/concepts/measurement-archive.md`](docs/concepts/measurement-archive.md) and [`docs/developer/measurement-archive-internals.md`](docs/developer/measurement-archive-internals.md). Layout: `library/measurements/{run_id}.json` (facts), `library/measurements.json` (index), plus `backends/`, datasets, `prompt_aliases.json`. Both digest layers (`AxisIndex` and `SampleIndex`) are in-memory only — rebuilt from the archive every refresh, no on-disk file.

Full tree in [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md); state schema and resume flow in `infrastructure/persistence/session_emitter.py` and `application/campaign/runner.py`.

## Key Patterns

- **Two-object boundary**: user knobs live on `CampaignConfig` (Pydantic, nested sub-models, `extra='forbid'` — typos raise at load, not silently drop); everything else (session identity, loop infra, scoring env) lives on `Session` in `application/campaign/campaign_setup.py`. Services take whichever they need. Nothing mutates user config; `configure_and_apply_pipeline` writes derived `pipeline_params` onto `session`, not onto `campaign_config`.
- **Store**: `Stores` bundle + `build_stores(projects_root, tenant_id="default")` — frozen composite over focused leaf stores. `Stores.archive` is the `MeasurementArchive` (database core); peers are `BackendStore`, `SessionStore`, `CampaignStore`.
- **Error handling**: `graceful()` context manager in `shared/errors.py`. Escalation flows via `QueryLoopResult.escalation_signal` (return value, not exception).
- **Graceful interrupt**: First Ctrl+C finishes in-flight call and saves; second force-quits. There is no mid-run pause/resume — re-running `optimize` resumes from the latest completed round.
- **Optimizer LLM calls**: all go through `llm_call()` in `application/optimization/pipeline.py`, not `chat()` directly.
- **Cycle identity**: cycle hash is the baseline `JobSearchPoint`'s `content_hash(dataset)` (truncated, `cycle_` prefix). `JobSearchPoint.pipeline_params` already carries the active-steps list + per-node target-layer config (model, temperature, max_tokens, …) and `content_hash` folds in the rendered prompt + dataset, so changing the target LLM, prompt, pipeline composition, or dataset starts a new cycle root. Loop-control / strategy knobs on `CampaignConfig` (max_rounds, optimizer-LLM, patience, n_variants, …) are not part of `JobSearchPoint` and are excluded. `cmd_optimize` recomputes the hash from the live `pipeline.json` on every run; if it differs from the active session pointer's `cycle_id`, a fresh session+cycle is auto-minted before baseline runs. See `cycle_config_identity()` in `domain/cycle_identity.py` and `_compute_cycle_id()` in `presentation/cli/campaign_runner.py`.
- **Two-tier sampling**: `sp_budget_ttest` controls the optimization loop scoring set. Sequential elimination early-stops inferior candidates via the Wilcoxon signed-rank test.
- **Canonical prompt authoring**: dataset starting prompts live in `datasets/{name}/prompts/{node}.json` (or `default.json`) as `PromptTemplate` JSON. Monolithic `prompt` strings in `pipeline.json` are deprecated.
- **Round-boundary scoring-set mutations** — two sanctioned writers, in this order: (1) **Zero-signal filter** (off by default, `min_observations=5`): queries with 0 variance across ≥ N samples physically moved to `datasets/{name}.json::excluded`; mutates the on-disk dataset. (2) **Scoring-set evolution** (off by default, code symbol `scoring_set`): Rasch + KG swaps understood samples ↔ high-info samples in the in-memory `session.scoring_dataset` only; never touches disk. No other mutation of either is sanctioned.

## Known Issues

### Notebook ↔ CLI Session Parity

**Campaign path closed:** `run_optimization` auto-mints a session+cycle pair when caller passes `session_id=""`, producing the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`.

**M9 Track 4:** Shared file-directory view model — renderer unification is still that track's work.

### TermNorm Backend

- **Backend repo lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`.** It is the user's own project — edits to backend code (logging, pipeline metadata, node behaviour) are fair game when a change needs to land alongside a PromptPotter change. Coordinate cross-repo edits explicitly.
- **`llm_ranking` broken — always exclude.** Produces `json_validate_failed` on ~50% of queries. Set `"exclude_nodes": ["llm_ranking"]`. Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.
- **Without `llm_ranking`, prompt string fields have no effect.** Only `entity_profiling` has an LLM. Optimization focuses on pipeline params.

## Roadmap

**M12 is the headline** — multi-connector architecture (`ConnectorProtocol`, connector registry, second backend), competitor head-to-head, webapp Phase 2 (launch + live monitoring). **M10 (prompt-iteration framework + L1-generate tuning, targeting ≥95% in ≤5 rounds) is the next active milestone**; it doubles as the L4 partial implementation (most of self-optimization's credit-assignment infrastructure, operated manually — see [`docs/specs/m12-plus-backlog.md § Self-optimization`](docs/specs/m12-plus-backlog.md)). M11 (BBEH benchmarks, ablation, webapp read-only) follows. Both are backbone work in front of M12, not destinations. M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Testing

Minimal suite — only stable contracts tested. No volume tests, no O(n) complexity. Mock: `monkeypatch` for async, stdlib `unittest.mock`. See `tests/CLAUDE.md`.

## Navigation

**Manual (users)**: [`manual/`](docs/manual/README.md) — numbered walkthrough, install → first run → reading output → troubleshooting.

**Concepts (how it works, concept-first)**: [`measurement-archive.md`](docs/concepts/measurement-archive.md), [`campaign-lifecycle.md`](docs/concepts/campaign-lifecycle.md), [`three-layer-loop.md`](docs/concepts/three-layer-loop.md), [`self-healing.md`](docs/concepts/self-healing.md), [`scoring-and-traces.md`](docs/concepts/scoring-and-traces.md), [`axis-index.md`](docs/concepts/axis-index.md), [`prompts-and-individuals.md`](docs/concepts/prompts-and-individuals.md), [`nodes-and-pipelines.md`](docs/concepts/nodes-and-pipelines.md), [`glossary.md`](docs/concepts/glossary.md)

**Developer (implementation)**: [`code-layout.md`](docs/developer/code-layout.md), [`information-flow.md`](docs/developer/information-flow.md), [`measurement-archive-internals.md`](docs/developer/measurement-archive-internals.md), [`node-standard.md`](docs/developer/node-standard.md), [`prompt-scheme-internals.md`](docs/developer/prompt-scheme-internals.md), [`axis-index-internals.md`](docs/developer/axis-index-internals.md), [`self-healing-internals.md`](docs/developer/self-healing-internals.md), [`display-conventions.md`](docs/developer/display-conventions.md), [`code-map.md`](docs/developer/code-map.md)

**Operations**: [`cli-reference.md`](docs/operations/cli-reference.md), [`environment.md`](docs/operations/environment.md), [`backend-integration.md`](docs/operations/backend-integration.md), [`persistence-and-state.md`](docs/operations/persistence-and-state.md), [`rewind-and-fork.md`](docs/operations/rewind-and-fork.md), [`improvement-tracking.md`](docs/operations/improvement-tracking.md), [`observability.md`](docs/operations/observability.md)

**Methods**: [`candidate-elimination.md`](docs/methods/candidate-elimination.md), [`exploration-exploitation.md`](docs/methods/exploration-exploitation.md)

**Research**: [`benchmarks.md`](docs/research/benchmarks.md), [`metrics.md`](docs/research/metrics.md), [`related-work.md`](docs/research/related-work.md)

**Specs**: [`docs/specs/`](docs/specs/CLAUDE.md) — active (M9, M10, M11, M12, M12+), archived (M8, old M9)
