# CLAUDE.md

## What this is

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. The backend declares tunable params via `GET /pipeline`; the optimizer runs critique-guided generate→score→critique with PoBB elimination (ε=0.05, n_min=4), cross-run memory, and self-healing rails. Python 3.13+, hexagonal. **Orchestration is the product — backends are pluggable.** TermNorm is the only registered connector today (`promptpotter/connectors/termnorm.py` — bundles wire adapter, session lifecycle, and experiment-data extraction under one `Connector` shape); BBEH is the headline benchmark.

The user is the operator. The project file tree IS the dashboard, plus a **read-only operator dashboard** at `/ui` (Next.js project at `webapp/`, static export at `webapp/out/`) that polls the active cycle's `dashboard.json` every 2 s — used in concert with the file tree, not in place of it. Full M12 webapp (control plane, monitoring, multi-cycle) is the headline milestone. Onboarding: install → restart VS Code → `/potter-run` (downloads TermNorm, starts its `.bat`, converts datasets, prompts for API keys).

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
| `EscalationState` cause-driven dynamics | `application/optimization/cycle.py` | Counters are private. The only mutation surface is observation methods. `observe_round` delegates to `cadence.evaluate_round(SignalInputs)` over `DEFAULT_ROUND_RULES`; the rule set is the post-round transition policy and the FSM lives nowhere |
| `cadence` rules engine | `application/optimization/cadence/{rules,evaluator}.py` | `evaluate_round` is sort-by-priority first-match-wins over `DEFAULT_ROUND_RULES`. Adding a rule means adding a `CadenceRule` row; predicates read frozen `SignalInputs` snapshots, no mutation surface to abuse |
| `DecisionTrace` (frozen Pydantic) | `domain/decision_trace.py` | `extra="forbid"` + frozen + JSON-roundtrip-stable. PoBB writes traces at decision points into `RoundResult.decision_traces`; the `decision_trace_summary` signal renders them for `l1_critique`. There's no sidecar write path because the model is frozen |
| `dispatch_hub.SIGNALS` typed dict | `application/optimization/dispatch_hub.py` | Each entry is a `_Signal(name, kind, render, doc)`; `validate_template()` (called from `load_optimizer_prompt`) raises on `{{slot}}` names not in the registry. A typo in a template fails at module load, not at first render |
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

**SearchPoint hierarchy.** `JobSearchPoint` (frozen target spec, content-hashed) + `PromptTemplate` (8-field scheme) → `OptSearchPoint` (optimizer state: lineage, L2/L3 overrides, per-individual memory). Twin tracing: target → `archive/measurements/{run_id}.json` (content-addressed, the DB core); optimizer → `campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json`. **New optimizer state MUST flow through `OptSearchPoint`** — no sidecar state.

**Three-layer loop + four wounds.** L1 generates/measures/scores/critiques every round; L2 fires on L1 stall (refines `task_context`, never touches `pipeline_params`); L3 fires on L2 stall (replans strategy). **All four optimizer LLM calls go through one path:** `build_bundle(cycle)` → `DispatchHub.fill_l1` or `fill_fixed` → `compile_prompt(**hub_dict, **extras)` → LLM — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. **No prompt site summarizes its own data** — fields enter prompts only via this path. Signal routing, fan-in, healing rules, and the four wound channels live in [`docs/developer/dispatch-hub.md`](docs/developer/dispatch-hub.md) — that's the canonical info-flow doc; `promptpotter/CLAUDE.md` covers the L1/L2/L3 agent contracts.

**Three I/O kinds (invariant, not guideline).** Allowlists owned by `tests/test_artifact_parity.py`. (1) **Persistence:** sole ingress is per-cycle `CycleLedger` (`infrastructure/ledger.py`, `events.jsonl`). `RunCallbacks` (`application/run_callbacks.py`) is a typed event constructor over `CycleLedger.append` — it is the writer-side API orchestration uses. Newtype-guarded projections under `infrastructure/projections/`: `LiveDashboardProjection` (root-only; `dashboard.json`, `output.log`); `AuditTrailProjection` (per cycle/fork; `.runtime/cache/rounds/round_NNNN.json`); `PoBBStreamProjection` (per cycle; `.runtime/streams/round_NNNN_p_best.jsonl`); `SignalsProjection` (per cycle; `.runtime/signals.jsonl` — appends one line per `cadence/rule_fired` PhaseRecord). Forks via `CycleLedger.inherit_from(parent, offset)`. `DecisionKind` + `DECISION_GATING` (`domain/run_records.py`) are the SoT for replayed-vs-archival gating. Observers built via one ingress: `application/run_observers.py::build_run_observers` (CLI, notebook, future webapp). (2) **Display:** subscribes to the ledger via `ProjectionBase.on_record` (`LiveDisplay` for terminal/notebook, `LiveDashboardProjection` for `dashboard.json` — also mirrors cadence firings into `recent_rules` + `current_signals`); subscribers MUST NOT write campaign artifacts beyond their declared allowlist. (3) **Control:** `stop_check` on `Session`; MUST NOT write campaign artifacts. **Entry points MUST NOT write campaign artifacts directly.**

**Pipeline params** — always nested dicts keyed by node (`{"llm_only": {"model": ...}}`). No flat format, no `override_map`, no `resolve_flat_param()`. `PROMPT_STRING_FIELDS` (`config/settings.py`) splits prompt fields from node params. `PipelineSchema` is built entirely from `GET /pipeline` — zero backend constants in PromptPotter. Canonical prompts at `datasets/{name}/prompts/{node}.json` as `PromptTemplate` JSON; monolithic `prompt` strings in `pipeline.json` are deprecated.

**Scoring — traces are facts, scores are policy.** `score_search_point()` is the single gateway. Each trace carries `{scorer_id: {score, hit, formula}}`; every load rescores under the active scorer. Resume replays decisions against rescored inputs — first mismatch halts; `--fork-on-divergence` mints a sibling rooted there. Hot-swap composite via `campaigns/{cycle_id}/scoring_steer.json` → `{"per_round": "..."}`; next round-end recompiles `session.round_scorer` after validation.

**Cycle identity.** Cycle hash = baseline `JobSearchPoint.content_hash(dataset)` (folds in pipeline_params + rendered prompt + dataset; excludes loop-control knobs). `cmd_optimize` recomputes per run; mismatch with active session → fresh session+cycle auto-minted.

**Round-boundary scoring-set mutations — two sanctioned writers, in order:** (1) zero-signal filter (off by default; mutates `datasets/{name}.json::excluded`); (2) scoring-set evolution (off by default; in-memory `session.scoring.scoring_set` only). No third writer is sanctioned.

## Persistence

`.promptpotter/` holds two trees: `sessions/{session_id}/` (operator workspace) and `campaigns/{root_cycle_id}/` (one cycle family per directory; siblings under `forks/`, `diag/`, `sweeps/`). Telemetry (`dashboard.json`) lives at the family root — shared across forks. Per-cycle audit (`index.json`, `log.md`, `rounds/`, `langfuse/`, `prompts/`) at each cycle's top level; runtime internals (ledger, cache, P(best) streams) under `.runtime/`. Cross-cycle `archive/measurements/` is the MeasurementArchive — DB core. **Reads happen by opening files** — no read CLI. Full tree, fork lineage, and recovery workflows: [`docs/operations/persistence-and-state.md`](docs/operations/persistence-and-state.md). Layer contracts: [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md).

## Per-dataset configuration

`datasets/{name}/`: `pipeline.json` (full pipeline + backend metadata), `campaign.json` (knobs, scoring formula, optimizer LLM), `task_description.md` (decomposed at `init` into `task_context`), `prompts/{node}.json`, `dataset.md`. **Configs are the source of truth.** No parallel default ladders elsewhere.

## Conventions (non-derivable)

Non-negotiables only — full style, code-shape, tests, CLI, git rules in [`docs/developer/conventions.md`](docs/developer/conventions.md).

- **No backward compatibility** — see the **STOP** section above. Non-negotiable.
- **`eval` banned from identifiers and prose.** Exception: the `Evaluator` class + direct registry consumers (`evaluators` field, `all_evaluators()`, `materialize_*_values`). Use loop / round / searchpoint / sample / measurement / scoring / fitness / trial / critique. Domain vocabulary: evolve, generation, population, mutation, selection, individual.
- **Vocabulary.** A dataset row is a **sample**. The input-string field on a sample is `query` — parallel naming across `Sample.query`, `BackendResult.query`, `QueryMeasurement.query`. Use `query` *only* as a field name or when describing genuine retrieval / TermNorm wire; never as a synonym for "sample" elsewhere. Use `sample` for everything that aggregates over rows: `n_samples`, `per_sample` scoring scope, `SampleProfile`, `SampleDifficulty`, `SampleRecord`, `compile_sample_difficulty`, `update_sample_tracker`, `count_degraded_samples`, `degraded_samples`. **Do not** use the phrase **"query ranking"** — pick the precise name: `posterior elimination` (PoBB, `application/optimization/elimination.py`), `Rasch sort` with axes `sample-difficulty rank` + `candidate-ability rank` (`application/intelligence/hard_sample_sorter.py`), or the backend's `llm_ranking` node (per-sample item ordering). The umbrella "how query budget is spent across N candidates per round" is **`candidate budget allocation`** — implemented by posterior elimination. `candidate` = a prompt SearchPoint variant; **never** a retrieval-list item (those are `ranked_items`). `meta-prompt` = the L1/L2/L3/Critique LLM template (synonymous with "optimizer prompt"; field-standard from PromptWizard / DSPy / OPRO).

## Known issues

- **TermNorm backend at** `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`. User's own project — cross-repo edits authorized; coordinate explicitly.
- **`llm_ranking` broken — always set `"exclude_nodes": ["llm_ranking"]`** (`json_validate_failed` ~50% of queries). Effective pipeline: `cache_lookup → fuzzy_matching → web_search → entity_profiling → token_matching`.

## Roadmap

**M12 is the headline** — multi-connector, competitor head-to-head, webapp Phase 2. **M10 active** — prompt-iteration framework + L1-generate tuning; ≥95% in ≤5 rounds. **M11** — BBEH benchmarks, ablation, webapp read-only (Slice 1 vanilla shipped + cut over to Next.js port — see `docs/specs/m11-webapp-react-port.md`; vanilla preservation list at `docs/specs/m11-webapp-minimal-preview.md`). M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Pointers

**Per-layer contracts** (progressive disclosure — load only the layer you're touching): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (L1/L2/L3 agent contracts) · [`promptpotter/application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration shape) · [`promptpotter/domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) (frozen models) · [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`promptpotter/presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) (CLI + API + views).

**Topical docs:** `docs/manual/` install→first run→reading→troubleshooting · `docs/concepts/` how it works · `docs/operations/` CLI/env/persistence/rewind-and-fork/observability · `docs/developer/README.md` architecture brief (prompt structure, dispatch, scoring node, cross-run memory) · [`docs/developer/conventions.md`](docs/developer/conventions.md) full style + code-shape rules · `tests/CLAUDE.md` test charter.
