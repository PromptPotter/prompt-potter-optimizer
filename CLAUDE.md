# CLAUDE.md

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. The backend declares tunable params via `GET /pipeline`; the optimizer runs critique-guided generate→score→critique with PoBB elimination (ε=0.05, n_min=4), cross-run memory, and self-healing rails. Python 3.13+, hexagonal. **Orchestration is the product — backends are pluggable.** TermNorm is the only registered extractor today (`EXPERIMENT_EXTRACTORS` / `TRACE_GT_RESOLVERS` in `application/config.py`); BBEH is the headline benchmark.

The user is the operator and the project file tree IS their dashboard — no webapp yet (M11/M12 plan one over FastAPI). Onboarding: install → restart VS Code → `/potter-run` (downloads TermNorm, starts its `.bat`, converts datasets, prompts for API keys).

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json
python -m promptpotter optimize                              # resume default; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter optimize --from N                     # rewind in place
python -m promptpotter optimize --fork-on-divergence         # sibling cycle at divergence point
uvicorn promptpotter.main:app --port 8001                    # read-only API
```

`.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required. Provider is per-campaign in `campaign.json::optimizer_llm.provider`.

## Architecture

**Hexagonal.** `domain/` (pure) → `application/` (use cases) → `infrastructure/` (I/O) → `presentation/` (entry adapters), plus leaf `shared/`, `config/`. **Strict:** `application/intelligence/` MUST NOT import from `application/optimization/` (`tests/test_layer_imports.py`).

**SearchPoint hierarchy.** `JobSearchPoint` (frozen target spec, content-hashed) + `PromptTemplate` (8-field scheme) → `OptSearchPoint` (optimizer state: lineage, L2/L3 overrides, per-individual memory). Twin tracing: target → `library/measurements/{run_id}.json` (content-addressed, the DB core); optimizer → `campaigns/{cycle_id}/trials/trial_NNNN.json`. **New optimizer state MUST flow through `OptSearchPoint`** — no sidecar state.

**Three-layer loop + four healing loops** (gradual, one nudge per fire). L1 generates/measures/scores/critiques every round. L2 fires on L1 stall — writes to `OptSearchPoint` (directive, `l1_section_overrides`, optimizer-param tweaks); never touches pipeline_params. L3 fires on L2 stall — replans the strategy. Healing: L2 nurses L1 on `ValidationFailure` and on `RuntimeFailure` (DegradationCheck mid-eval); L3 replans on L2 patience; L3 nurses L2 on validator outcome (cross-field dup, verbatim self-repeat, catalogue redundancy). Compression chain: eval results → `dispatch_msg` (`compile_l1_critique_blob`) → L1 critique → L2 `directive` → L1 generate. **Fan-in:** L1-generate reads both `plan` (from L3, persistent on `OptSearchPoint`, never cleared) and `l2_directive` (from L2, one-round window via `clear_volatile()`). L2 also reads `plan` — symmetric to L2→L1: L3 sets the strategic framework that both L2 (when refining) and L1 (when generating) operate within. **All four optimizer LLM calls go through one path:** `DispatchState` (per-call state) → `LAYER_CONFIGS[layer]` (`{var: section_renderer}` table) → `compile_prompt_vars` (applies OSP overrides, merges per-call extras) → LLM. **No prompt site summarizes its own data** — fields enter prompts only via this path.

**Three I/O kinds (invariant, not guideline).** Allowlists owned by `tests/test_artifact_parity.py`. (1) **Persistence:** sole ingress is per-cycle `RunLedger` (`infrastructure/ledger.py`, `events.jsonl`). Two newtype-guarded projections under `infrastructure/projections/`: `LiveDashboardProjection` (root-only; `dashboard.json`, `output.log`); `AuditTrailProjection` (per cycle/fork; `.cache/rounds/round_NNNN.json`). Forks via `RunLedger.inherit_from(parent, offset)`. `DecisionKind` + `DECISION_GATING` (`domain/run_records.py`) are the SoT for replayed-vs-archival gating. (2) **Display:** `RunListener` (`application/runner.py`); MUST NOT write to disk. (3) **Control:** `stop_check` on `Session`; MUST NOT write campaign artifacts. **Entry points MUST NOT write campaign artifacts directly.**

**Pipeline params** — always nested dicts keyed by node (`{"llm_only": {"model": ...}}`). No flat format, no `override_map`, no `resolve_flat_param()`. `PROMPT_STRING_FIELDS` (`config/settings.py`) splits prompt fields from node params. `PipelineSchema` is built entirely from `GET /pipeline` — zero backend constants in PromptPotter. Canonical prompts at `datasets/{name}/prompts/{node}.json` as `PromptTemplate` JSON; monolithic `prompt` strings in `pipeline.json` are deprecated.

**Scoring — traces are facts, scores are policy.** `score_search_point()` is the single gateway. Each trace carries `{scorer_id: {score, hit, formula}}`; every load rescores under the active scorer. Resume replays decisions against rescored inputs — first mismatch halts; `--fork-on-divergence` mints a sibling rooted there. Hot-swap composite via `campaigns/{cycle_id}/scoring_steer.json` → `{"per_round": "..."}`; next round-end recompiles `session.round_scorer` after validation.

**Cycle identity.** Cycle hash = baseline `JobSearchPoint.content_hash(dataset)` (folds in pipeline_params + rendered prompt + dataset; excludes loop-control knobs). `cmd_optimize` recomputes per run; mismatch with active session → fresh session+cycle auto-minted.

**Round-boundary scoring-set mutations — two sanctioned writers, in order:** (1) zero-signal filter (off by default; mutates `datasets/{name}.json::excluded`); (2) scoring-set evolution (off by default; in-memory `session.scoring_dataset` only). No third writer is sanctioned.

## Persistence

```
.promptpotter/
  active_session.json                              # {tenant_id, session_id, cycle_id}
  projects/{tenant_id}/
    sessions/{session_id}/                         # session.json, journal.md, notes.md
    campaigns/{root_cycle_id}/                     # FAMILY ROOT
      dashboard.json output.log                    # telemetry, root only (shared across forks)
      index.json log.md trials/ prompts/ langfuse/
      .cache/rounds/{NNNN}.json                    # per-round LLM audit
      forks/{cycle_id}/ diag/ sweeps/              # per-fork audit; telemetry stays at root
    library/measurements/{run_id}.json             # MeasurementArchive — DB core
```

`library/` is cross-cycle/session/tenant. One row = `(sample × config → outcome)`. Retrieval views: `measurements_for_sample()`, `measurements_for_config(predicate)`. Cache reuse + LLM digests are derived views over this archive. **Reads happen by opening files** — no read CLI.

## Per-dataset configuration

`datasets/{name}/`: `pipeline.json` (full pipeline + backend metadata), `campaign.json` (knobs, scoring formula, optimizer LLM), `task_description.md` (decomposed at `init` into `task_context`), `prompts/{node}.json`, `dataset.md`. **Configs are the source of truth.** No parallel default ladders elsewhere.

## Conventions (non-derivable)

- **PEP 604** type hints; `logging` module only (no `print()` in `promptpotter/`); ruff line-length **100**; `APP_VERSION` in `config/settings.py`.
- **No backward compatibility** — break, rename, restructure freely. No compat shims, no `// removed` comments. Replace contract → delete old test.
- **No fallbacks in service code.** Two sanctioned exceptions: `score_population()` synthetic-0 on `validation_failures`; load-boundary deprecated-sample gate (uses `classify_result()` fatal codes). Any new fallback must be documented alongside these.
- **`eval` banned from identifiers and prose.** Exception: the `Evaluator` class + direct registry consumers (`evaluators` field, `all_evaluators()`, `materialize_*_values`). Use loop / round / searchpoint / sample / measurement / scoring / fitness / trial / critique. Domain vocabulary: evolve, generation, population, mutation, selection, individual.
- Pipeline components are **nodes**.
- Optimizer LLM calls go through `llm_call()` (`application/optimization/pipeline.py`), never `chat()`.
- Escalation flows via return value (`QueryLoopResult.escalation_signal`), not exception. Use `graceful()` (`shared/errors.py`) where exceptions must escape.
- **Tests are subtractive.** Each guards a named invariant (`tests/CLAUDE.md`). No volume tests, ≤2–3 monkeypatches per test.
- **Direct field access** — `dict[key]` for guaranteed fields, not `.get(key, fallback)`.
- **CLI timeouts: 30s default for ALL commands.** Increase only when told "ready for data collection". **Never run `campaign_runner` with `run_in_background`** — always foreground.
- **Commit messages: HARD CAP 800 chars total** (incl. trailer). Title <70. Terse bullets — no motivation essays. Over 800 → rewrite, do not commit-and-fix-later. Conventional commits (`feat:`, `fix:`, `docs:`, …).
- Comments default to none — only non-obvious *why*.

## Known issues

- **TermNorm backend at** `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`. User's own project — cross-repo edits authorized; coordinate explicitly.
- **`llm_ranking` broken — always set `"exclude_nodes": ["llm_ranking"]`** (`json_validate_failed` ~50% of queries). Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.

## Roadmap

**M12 is the headline** — multi-connector, competitor head-to-head, webapp Phase 2. **M10 active** — prompt-iteration framework + L1-generate tuning; ≥95% in ≤5 rounds. **M11** — BBEH benchmarks, ablation, webapp read-only. M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Pointers

`docs/manual/` install→first run→reading→troubleshooting · `docs/concepts/` how it works · `docs/operations/` CLI/env/persistence/rewind-and-fork/observability · `docs/developer/README.md` architecture brief (prompt structure, dispatch, scoring node, cross-run memory) · `tests/CLAUDE.md` test charter.
