# CLAUDE.md

> **Architecture reference: [`docs/architecture.md`](docs/architecture.md).**
> Read it first — it's the single-page §0 + load-bearing surface
> (§0.5) every PR measures against. This file (root `CLAUDE.md`)
> covers onboarding pointers, project conventions, and the per-layer
> CLAUDE.md tree (`promptpotter/CLAUDE.md`,
> `promptpotter/application/CLAUDE.md`,
> `promptpotter/domain/CLAUDE.md`,
> `promptpotter/infrastructure/CLAUDE.md`,
> `promptpotter/presentation/CLAUDE.md`, `tests/CLAUDE.md`) for
> progressive disclosure of layer detail.

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
| Three I/O kinds (Persistence / Display / Control) | per this file | Persistence has one ingress (`CycleEventLog.append`); `RunCallbacks` is a typed event constructor over that ingress and is the only writer-side API; display happens via ledger subscribers (`LiveDisplay`, `LiveDashboardView`); control-side `stop_check` writes no campaign artifacts |
| Cycle records + derived-view dispatch (import-time exhaustive) | `domain/run_records.py`, `infrastructure/ledger.py`, `projections/base.py` | Two registries enforced at import time: `ResumeCheckpointKind` member without a `RESUME_CHECKPOINT_GATING` entry raises before the module loads; `DerivedView.on_record` owns the `isinstance(record, …)` dispatch and there's no second dispatch path because the base class is the only one. Add a kind / record / projection: extend the registry, no sidecar |
| Hexagonal layer separation | per this file | `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` + `test_cycle_does_not_import_prompt_surface` — and modules in different layers don't expose what the other side would need |
| `score_search_point()` gateway | `application/scoring/search_point_scorer.py` | Single function callers reach for; `measure_sample` is implementation detail. The gateway is the natural call |
| Frozen domain models + path newtypes | `domain/search_point.py`, `domain/opt_search_point.py`, `infrastructure/store/paths.py`, `domain/cycle_paths.py` | `JobSearchPoint` / `PromptTemplate` / `OptSearchPoint` are frozen Pydantic — mutation isn't a thing the type permits, lineage is encoded by `derive()`. `CycleDir` / `RootCycleDir` newtypes guard path construction — projections and stores accept the newtype, not `str`/`Path`. Wrong shapes rejected at the type level |
| `EscalationState` cause-driven dynamics + escalation rules engine | `application/optimization/escalation/{state,decide,rules,firing}.py` | Counters are private; the only mutation surface is observation methods. `observe_round` delegates to `decide_escalation(EscalationInputs)` over `DEFAULT_ESCALATION_RULES` — sort-by-priority, first-match-wins. Adding a rule means adding an `EscalationRule` row; predicates read frozen `EscalationInputs` snapshots, no mutation surface to abuse |
| `dispatch_hub.INJECTIONS` typed dict | `application/optimization/dispatch_hub.py` | Each entry is a `_Injection(name, kind, render, doc)`; `validate_template()` (called from `load_optimizer_prompt`) raises on `{{slot}}` names not in the registry. A typo in a template fails at module load, not at first render |
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

**Hexagonal.** `domain/` (pure) → `application/` (use cases) → `infrastructure/` (I/O) → `presentation/` (entry adapters), plus leaf `shared/`, `config/`. **Strict:** `application/intelligence/` MUST NOT import from `application/optimization/` (`tests/test_invariants.py::test_no_unexpected_runtime_layer_violations`).

**SearchPoint hierarchy.** `JobSearchPoint` (frozen target spec, content-hashed) + `PromptTemplate` (8-field scheme) → `OptSearchPoint` (optimizer state: lineage, L2/L3 overrides, per-individual memory). Twin tracing: target → `archive/measurements/{run_id}.json` (content-addressed, the DB core); optimizer → `campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json`. **New optimizer state MUST flow through `OptSearchPoint`** — no sidecar state.

**Three-layer loop + four wounds.** L1 generates/measures/scores/critiques every round; L2 fires on L1 stall (refines `task_context`, never touches `pipeline_params`); L3 fires on L2 stall (replans strategy). **All four optimizer LLM calls go through one path:** `build_bundle(cycle)` → `DispatchHub.fill_l1` or `fill_fixed` → `compile_prompt(**hub_dict, **extras)` → LLM — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`. **No prompt site summarizes its own data** — fields enter prompts only via this path. Signal routing, fan-in, healing rules, and the four wound channels live in [`docs/developer/dispatch-hub.md`](docs/developer/dispatch-hub.md) — that's the canonical info-flow doc; `promptpotter/CLAUDE.md` covers the L1/L2/L3 agent contracts.

**Four I/O kinds (invariant, not guideline).** Allowlists owned by `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` + `test_artifact_sets_are_disjoint_and_well_formed`. (1) **Persistence:** sole ingress is per-cycle `CycleEventLog` (`infrastructure/ledger.py`, `events.jsonl`). `RunCallbacks` (`application/run_callbacks.py`) is a typed event constructor over `CycleEventLog.append` — it is the writer-side API orchestration uses. Newtype-guarded projections under `infrastructure/projections/`: `LiveDashboardView` (root-only; `dashboard.json`, `output.log`); `AuditTrailView` (per cycle/fork; `.runtime/cache/rounds/round_NNNN.json`); `PoBBStreamView` (per cycle; `.runtime/streams/round_NNNN_p_best.jsonl`); `SignalsProjection` (per cycle; `.runtime/signals.jsonl` — appends one line per `escalation/rule_fired` PhaseRecord). Forks via `CycleEventLog.inherit_from(parent, offset)`. `ResumeCheckpointKind` + `RESUME_CHECKPOINT_GATING` (`domain/run_records.py`) are the SoT for replayed-vs-archival gating. Observers built via one ingress: `application/run_observers.py::build_run_observers` (CLI, notebook, future webapp). (2) **Display:** subscribes to the ledger via `DerivedView.on_record` (`LiveDisplay` for terminal/notebook, `LiveDashboardView` for `dashboard.json` — also mirrors cadence firings into `recent_rules` + `current_signals`); subscribers MUST NOT write campaign artifacts beyond their declared allowlist. (3) **Control-local:** `stop_check` on `Session`; MUST NOT write campaign artifacts. (4) **Human-input:** operator-supplied review events written to `CycleEventLog.append` from a watched file path when `Session.hitl_mode` is on, carrying typed `HumanReviewRecord` payloads. Same persistence ingress as orchestration writes — what's new is the source. M12 will add Control-remote (orchestrator daemon) on the same ingress. **Entry points MUST NOT write campaign artifacts directly.**

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

**M12 is the headline** — multi-connector, competitor head-to-head, webapp Phase 2. **M10 active** — prompt-iteration framework + L1-generate tuning; **targeting** ≥95% in ≤5 rounds (cleanup arc closed pass-2; framework + benchmark hit are the open M10 half). **M11** — BBEH benchmarks, ablation, webapp read-only (Slice 1 vanilla shipped + cut over to Next.js port — see `docs/specs/m11-webapp-react-port.md`; vanilla preservation list at `docs/specs/m11-webapp-minimal-preview.md`). M0–M9 complete. See [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

## Pointers

**Per-layer contracts** (progressive disclosure — load only the layer you're touching): [`promptpotter/CLAUDE.md`](promptpotter/CLAUDE.md) (L1/L2/L3 agent contracts) · [`promptpotter/application/CLAUDE.md`](promptpotter/application/CLAUDE.md) (orchestration shape) · [`promptpotter/domain/CLAUDE.md`](promptpotter/domain/CLAUDE.md) (frozen models) · [`promptpotter/infrastructure/CLAUDE.md`](promptpotter/infrastructure/CLAUDE.md) (ledger + projections + stores) · [`promptpotter/presentation/CLAUDE.md`](promptpotter/presentation/CLAUDE.md) (CLI + API + views).

**Topical docs:** `docs/manual/` install→first run→reading→troubleshooting · `docs/concepts/` how it works · `docs/operations/` CLI/env/persistence/rewind-and-fork/observability · `docs/developer/README.md` architecture brief (prompt structure, dispatch, scoring node, cross-run memory) · [`docs/developer/conventions.md`](docs/developer/conventions.md) full style + code-shape rules · `tests/CLAUDE.md` test charter.

## Pre-flight gate

Before adding any new concept (class, projection, injection, prompt,
field, dict, file), the PR description answers these eight questions.
"I don't know" or "kind of" on any answer is a hard block.

1. **Which §0 bucket does this belong to?** (central loop /
   escalation / errors-heal / dispatch / state+persistence / on-disk
   / tracing / archive). If "none of them," stop — either §0 is
   incomplete (update it deliberately) or this is the wrong PR.
2. **Does an existing channel already do this?** Default answer:
   yes. Search before adding.
3. **Is the name distinct from every existing concept in the
   codebase?** Grep first. Two `signals` was avoidable; the next
   collision is too.
4. **Is the name self-describing without opening another file?** Read
   the name in isolation. If it could mean three different things
   (`Decision`, `Bundle`, `Signal`), rename now — naming is cheap, the
   alternative is every future reader paying for it.
   - **Sub-rule: are you adding a new I/O kind?** §0 names four:
     Persistence (`CycleEventLog.append`), Display (ledger
     subscribers), Control-local (`stop_check`), Human-input
     (`HumanReviewRecord` via `Session.hitl_mode`). M12's orchestrator
     daemon will add a fifth (Control-remote). If your code
     introduces a NEW I/O kind beyond those, that's an
     architecture-spec change, not a feature change — **stop and
     amend §0 first**, then write the code.
5. **Can this ride existing infrastructure (ledger, INJECTIONS,
   `OptSearchPoint`, dispatch hub) without adding a sidecar?**
   Default: yes.
6. **Can the AI/operator read this fact from a file without running
   the CLI?** If the new code surfaces something material only via
   stdout, only via in-memory state, or only via "ask me to re-run
   with --verbose," it violates the AI-accessibility principle.
   Material facts land on disk in human-readable form.
7. **Does §0 (`docs/architecture.md`) need updating to mention
   this?** If yes, that's a separate PR landing first. Code that
   requires §0 to drift cannot land before §0 has been updated.
8. **Does this code emit a Langfuse-shape trace event for any new
   LLM call or backend match?** If yes, wrap the call site with
   `observed_node()`. New unwrapped LLM calls are an automatic block.
