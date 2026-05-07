# CLAUDE.md

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. The backend declares tunable params via `GET /pipeline`; the optimizer runs critique-guided generate→score→critique with PoBB elimination (ε=0.05, n_min=4), cross-run memory, and self-healing rails. Python 3.13+, hexagonal. **Orchestration is the product — backends are pluggable.** TermNorm is the only registered connector today (`promptpotter/connectors/termnorm.py` — bundles wire adapter, session lifecycle, and experiment-data extraction under one `Connector` shape); BBEH is the headline benchmark.

The user is the operator. The project file tree IS the dashboard, plus an **ugly read-only webapp preview** at `webapp/index.html` mounted on `/ui` (M11 Track 3 Slice 1) that polls the active cycle's `dashboard.json` every 2 s — used in concert with the file tree, not in place of it. Full M12 webapp (control plane, monitoring, multi-cycle) is the headline milestone. Onboarding: install → restart VS Code → `/potter-run` (downloads TermNorm, starts its `.bat`, converts datasets, prompts for API keys).

## STOP — read this before writing any code

**No backward compatibility, ever.** Zero released versions, zero stale on-disk data. There is nothing to be compatible with. **This is the rule that gets ignored most often.** Skipping this section wastes the operator's time.

**Delete on sight — don't ask, don't TODO, don't "remove later":**
- `// removed`, `# old name`, `# kept for parity`, `# kept for callers that still wire it through`
- Re-export aliases (`OldName = NewName`, `from .x import NewName as OldName`)
- `try/except ImportError` shims for renamed modules
- `dict.get(new, dict.get(old, default))` chains for renamed keys
- `getattr(obj, "new", getattr(obj, "old", default))` chains for renamed fields
- Methods/properties that exist solely to map old → new names
- `# legacy dict`, `# legacy format`, `# legacy payload` branches and the comments justifying them
- `(formerly ``module.x``)` reorganization breadcrumbs in code comments
- "Phase 2 / Phase 3 cleanup will replace this" docstrings — document current state, not half-done plans
- No-op stubs whose docstring says "kept for X"
- `dict.get("new") or dict.get("old", default)` fallbacks for renamed config keys

**Changing a contract:** rename, restructure, delete the old test, write the new one. No compat test, no deprecation warning, no shim, no fallback default.

**Found a shim someone else wrote?** Delete it in the same PR you noticed it. Don't file a TODO. Don't add a "remove later" comment.

The word `legacy` in a comment or docstring is a code smell — either the path is dead (delete it) or the word is wrong (delete the word). The only sanctioned uses of `deprecated` are the fatal-warning sample lifecycle (`is_deprecated`, `deprecated_samples`, `retry_of_deprecated_cache`, `RoundResult.deprecated`) — these are core domain language, not back-compat.

(This section is about **shim code and misleading wording**, not about docstrings explaining real invariants. See the docstring-trimming rule in Conventions: real WHY-docstrings stay.)

## Backbone

These primitives are settled. Reorganizing them — adding a parallel ingress, a getattr-chain fallback, a second dispatch site — should feel like swimming upstream because the shape doesn't permit it. Extend in place; if you genuinely need to change one, change the primitive itself. The wrong shape is meant to be hard to express, not policed by a test.

| Primitive | Where | Self-enforcing because |
|---|---|---|
| Three I/O kinds (Persistence / Display / Control) | per this file | Persistence has one ingress (`CycleLedger.append`); `RunCallbacks` is a typed event constructor over that ingress and is the only writer-side API; display happens via ledger subscribers (`LiveDisplay`, `LiveDashboardProjection`); control-side `stop_check` writes no campaign artifacts |
| `DecisionKind` + `DECISION_GATING` | `domain/run_records.py` | Import-time exhaustiveness — adding a kind without a gating mode raises before the module loads |
| Hexagonal layer separation | per this file | `tests/test_layer_imports.py` — and modules in different layers don't expose what the other side would need |
| `CycleLedger` + `ProjectionBase` dispatch | `infrastructure/ledger.py`, `projections/base.py` | `ProjectionBase.on_record` owns the `isinstance(record, …)` dispatch; subclasses override hooks. There's no second dispatch path because the base class is the only one |
| `score_search_point()` gateway | `application/scoring/search_point_scorer.py` | Single function callers reach for; `measure_sample` is implementation detail. The gateway is the natural call |
| Path helpers + `CycleDir` / `RootCycleDir` newtypes | `infrastructure/store/paths.py`, `domain/cycle_paths.py` | Projections and stores accept `CycleDir`/`RootCycleDir`, not `str`/`Path`. Callers get directories from helpers |
| `JobSearchPoint` / `PromptTemplate` / `OptSearchPoint` | `domain/search_point.py`, `domain/opt_search_point.py` | Frozen Pydantic models. Mutation isn't a thing the type permits; lineage is encoded by `derive()` |
| `EscalationState` cause-driven dynamics | `application/optimization/cycle.py` | Counters are private. The only mutation surface is observation methods; `next_action` is computed from stall_depth + mutation_history, so "signals from measurement, not the calendar" is structural |
| Tracing-as-shadow | `infrastructure/tracing/` | Tracing exposes no read API. State reaches the optimizer only via the ledger; tracing is fan-out only |

## Commands

```bash
pip install -e ".[all,dev]"
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q   # CI runs same chain
python -m promptpotter init --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json
python -m promptpotter optimize                              # resume default; Ctrl+C: 1st saves, 2nd force-quits
python -m promptpotter optimize --from N                     # rewind in place
python -m promptpotter optimize --fork-on-divergence         # sibling cycle at divergence point
python -m uvicorn promptpotter.main:app --port 8001          # read-only API + /ui webapp preview
```

Webapp preview lives at `http://localhost:8001/ui/` once uvicorn is running. **When the operator mentions the dashboard / webapp / UI**: probe `GET /api/v1/health` on :8001 — if it answers, share the URL; if not, suggest the uvicorn line above and remind them to keep `python -m promptpotter optimize` running in another terminal so `dashboard.json` refreshes live. Page reads `active_session.json` on load — `init` a new cycle ⇒ reload the page.

`.env` with `GROQ_API_KEY` (or OPENAI/ANTHROPIC/OPENROUTER) required. Provider is per-campaign in `campaign.json::optimizer_llm.provider`.

## Architecture

**Hexagonal.** `domain/` (pure) → `application/` (use cases) → `infrastructure/` (I/O) → `presentation/` (entry adapters), plus leaf `shared/`, `config/`. **Strict:** `application/intelligence/` MUST NOT import from `application/optimization/` (`tests/test_layer_imports.py`).

**SearchPoint hierarchy.** `JobSearchPoint` (frozen target spec, content-hashed) + `PromptTemplate` (8-field scheme) → `OptSearchPoint` (optimizer state: lineage, L2/L3 overrides, per-individual memory). Twin tracing: target → `archive/measurements/{run_id}.json` (content-addressed, the DB core); optimizer → `campaigns/{cycle_id}/rounds/round_NNNN.json`. **New optimizer state MUST flow through `OptSearchPoint`** — no sidecar state.

**Three-layer loop + four healing loops** (gradual, one nudge per fire). L1 generates/measures/scores/critiques every round. L2 fires on L1 stall — refines `OptSearchPoint.task_context` (the broadcast L2 channel, persistent and merged each fire) plus optional `l1_layout` / optimizer-param tweaks; never touches pipeline_params. L3 fires on L2 stall — replans the strategy. Healing: L2 nurses L1 on `ValidationFailure` and on `RuntimeFailure` (DegradationCheck mid-eval); L3 replans on L2 patience; L3 nurses L2 on validator outcome (`l2_task_context_verbatim_repeat`, layout HARD failures). Compression chain: eval results → `RoundDiagnostics` (via `compute_round_diagnostics`) → L1 critique → L2 `task_context` refinement → L1 generate. **Fan-in:** L1-generate reads both `plan` (from L3, persistent) and `task_context` (from L2, persistent until L2 next refines). L2 also reads `plan` — symmetric to L2→L1: L3 sets the strategic framework that both L2 (when refining the framing) and L1 (when generating) operate within. **All four optimizer LLM calls go through one path:** `build_bundle(cycle)` → `DispatchHub.fill_l1` (L1 generate, walks `opt_sp.l1_layout` over `SIGNALS`) or `fill_fixed` (L1 critique / L2 / L3, resolves `{{name}}` in fixed templates over `SIGNALS`) → `compile_prompt(**hub_dict, **extras)` → LLM. **No prompt site summarizes its own data** — fields enter prompts only via this path.

**Three I/O kinds (invariant, not guideline).** Allowlists owned by `tests/test_artifact_parity.py`. (1) **Persistence:** sole ingress is per-cycle `CycleLedger` (`infrastructure/ledger.py`, `events.jsonl`). `RunCallbacks` (`application/run_callbacks.py`) is a typed event constructor over `CycleLedger.append` — it is the writer-side API orchestration uses. Two newtype-guarded projections under `infrastructure/projections/`: `LiveDashboardProjection` (root-only; `dashboard.json`, `output.log`); `AuditTrailProjection` (per cycle/fork; `.cache/rounds/round_NNNN.json`). Forks via `CycleLedger.inherit_from(parent, offset)`. `DecisionKind` + `DECISION_GATING` (`domain/run_records.py`) are the SoT for replayed-vs-archival gating. Observers built via one ingress: `application/run_observers.py::build_run_observers` (CLI, notebook, future webapp). (2) **Display:** subscribes to the ledger via `ProjectionBase.on_record` (`LiveDisplay` for terminal/notebook, `LiveDashboardProjection` for `dashboard.json`); subscribers MUST NOT write campaign artifacts beyond their declared allowlist. (3) **Control:** `stop_check` on `Session`; MUST NOT write campaign artifacts. **Entry points MUST NOT write campaign artifacts directly.**

**Pipeline params** — always nested dicts keyed by node (`{"llm_only": {"model": ...}}`). No flat format, no `override_map`, no `resolve_flat_param()`. `PROMPT_STRING_FIELDS` (`config/settings.py`) splits prompt fields from node params. `PipelineSchema` is built entirely from `GET /pipeline` — zero backend constants in PromptPotter. Canonical prompts at `datasets/{name}/prompts/{node}.json` as `PromptTemplate` JSON; monolithic `prompt` strings in `pipeline.json` are deprecated.

**Scoring — traces are facts, scores are policy.** `score_search_point()` is the single gateway. Each trace carries `{scorer_id: {score, hit, formula}}`; every load rescores under the active scorer. Resume replays decisions against rescored inputs — first mismatch halts; `--fork-on-divergence` mints a sibling rooted there. Hot-swap composite via `campaigns/{cycle_id}/scoring_steer.json` → `{"per_round": "..."}`; next round-end recompiles `session.round_scorer` after validation.

**Cycle identity.** Cycle hash = baseline `JobSearchPoint.content_hash(dataset)` (folds in pipeline_params + rendered prompt + dataset; excludes loop-control knobs). `cmd_optimize` recomputes per run; mismatch with active session → fresh session+cycle auto-minted.

**Round-boundary scoring-set mutations — two sanctioned writers, in order:** (1) zero-signal filter (off by default; mutates `datasets/{name}.json::excluded`); (2) scoring-set evolution (off by default; in-memory `session.scoring.scoring_set` only). No third writer is sanctioned.

## Persistence

```
.promptpotter/
  active_session.json                              # {tenant_id, session_id, cycle_id}
  projects/{tenant_id}/
    sessions/{session_id}/                         # session.json, journal.md, notes.md
    campaigns/{root_cycle_id}/                     # FAMILY ROOT
      dashboard.json                               # telemetry, root only (shared across forks)
      index.json log.md rounds/ prompts/ langfuse/
      .cache/rounds/{NNNN}.json                    # per-round LLM audit
      forks/{cycle_id}/ diag/ sweeps/              # per-fork audit; telemetry stays at root
    archive/measurements/{run_id}.json             # MeasurementArchive — DB core
```

`archive/` is cross-cycle/session/tenant. One row = `(sample × config → outcome)`. Retrieval views: `measurements_for_sample()`, `measurements_for_config(predicate)`. Cache reuse + LLM digests are derived views over this archive. **Reads happen by opening files** — no read CLI.

## Per-dataset configuration

`datasets/{name}/`: `pipeline.json` (full pipeline + backend metadata), `campaign.json` (knobs, scoring formula, optimizer LLM), `task_description.md` (decomposed at `init` into `task_context`), `prompts/{node}.json`, `dataset.md`. **Configs are the source of truth.** No parallel default ladders elsewhere.

## Conventions (non-derivable)

- **PEP 604** type hints; `logging` module only (no `print()` in `promptpotter/`); ruff line-length **100**; `APP_VERSION` in `config/settings.py`.
- **No backward compatibility** — see the **STOP** section above. Non-negotiable.
- **No fallbacks in service code.** Two sanctioned exceptions: `score_population()` synthetic-0 on `validation_failures`; load-boundary deprecated-sample gate (uses `classify_result()` fatal codes). Any new fallback must be documented alongside these.
- **`eval` banned from identifiers and prose.** Exception: the `Evaluator` class + direct registry consumers (`evaluators` field, `all_evaluators()`, `materialize_*_values`). Use loop / round / searchpoint / sample / measurement / scoring / fitness / trial / critique. Domain vocabulary: evolve, generation, population, mutation, selection, individual.
- Pipeline components are **nodes**.
- Optimizer LLM calls go through `llm_call()` (`application/optimization/llm_call.py`), never `chat()`.
- Escalation flows via return value (`QueryLoopResult.escalation_signal`), not exception. Use `graceful()` (`shared/errors.py`) where exceptions must escape.
- **Tests are subtractive.** Each guards a named invariant (`tests/CLAUDE.md`). No volume tests, ≤2–3 monkeypatches per test.
- **Direct field access** — `dict[key]` for guaranteed fields, not `.get(key, fallback)`.
- **CLI timeouts: 30s default for ALL commands.** Increase only when told "ready for data collection". **Never run `campaign_runner` with `run_in_background`** — always foreground.
- **Commit messages: HARD CAP 800 chars total** (incl. trailer). Title <70. Terse bullets — no motivation essays. Over 800 → rewrite, do not commit-and-fix-later. Conventional commits (`feat:`, `fix:`, `docs:`, …).
- Comments default to none — only non-obvious *why*.
- **Docstring trimming is out of charter for LOC-shrink work.** Existing module / class / function docstrings explain WHY (invariants, contracts, hidden constraints) and are user-facing value. Do **not** trim them as a shortcut to a smaller diff. Real LOC wins come from pattern unification (one shape covers two cases), dead-code removal, inlining single-use helpers, fixing god-objects — never from shrinking explainers. If a docstring is genuinely an essay restating what the code does, ask first.

## Known issues

- **TermNorm backend at** `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`. User's own project — cross-repo edits authorized; coordinate explicitly.
- **`llm_ranking` broken — always set `"exclude_nodes": ["llm_ranking"]`** (`json_validate_failed` ~50% of queries). Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.

## Roadmap

**M12 is the headline** — multi-connector, competitor head-to-head, webapp Phase 2. **M10 active** — prompt-iteration framework + L1-generate tuning; ≥95% in ≤5 rounds. **M11** — BBEH benchmarks, ablation, webapp read-only (Track 3 Slice 1 shipped — see `docs/specs/m11-webapp-minimal-preview.md`). M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Pointers

`docs/manual/` install→first run→reading→troubleshooting · `docs/concepts/` how it works · `docs/operations/` CLI/env/persistence/rewind-and-fork/observability · `docs/developer/README.md` architecture brief (prompt structure, dispatch, scoring node, cross-run memory) · `tests/CLAUDE.md` test charter.
