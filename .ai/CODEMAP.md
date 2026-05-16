# CODEMAP — AI orientation for PromptPotter

> Read this **before** grepping. Curated map of backbone symbols, hot
> workflows, and "where is X" answers. Companion: `.ai/SYMBOLS.txt`
> (flat `symbol → file:line`, grep it).
>
> Stale rows fall back gracefully — the AI re-greps and updates the
> codemap as part of the PR. Regen `SYMBOLS.txt` after refactors:
> `python scripts/build_ai_index.py`.

## What this project is

LLM-driven program evolution for prompts + pipeline params. Critique-guided generate→score→critique with PoBB elimination, cross-run memory, self-healing rails. **Orchestration is the product — backends are pluggable and read-only.** Hexagonal Python (3.13+). Architecture depth → [`docs/architecture.md`](../docs/architecture.md).

## §0 buckets (where the architecture lives)

| Bucket | Lives in |
|---|---|
| Central loop (L1 generate / measure / score / critique) | `promptpotter/application/optimization/l1/` (`generate.py`, `critique.py`, `score.py`, `execute.py`, `population.py`, `stats.py`, `resume.py`) |
| Escalation (L2/L3 routing + firing) | `promptpotter/application/optimization/escalation/`, `transitions.py` |
| Dispatch (info ingress to every prompt) | `promptpotter/application/optimization/dispatch/hub/`, `dispatch/llm_call.py` |
| State + persistence | `promptpotter/infrastructure/ledger.py`, `infrastructure/projections/`, `infrastructure/store/` |
| Resume / fork | `promptpotter/application/optimization/resume_and_fork/` |
| On-disk artifacts | `.promptpotter/{sessions,campaigns}/…`, `datasets/{name}/…` |
| Tracing (shadow / fan-out only) | `promptpotter/infrastructure/tracing/` |
| Archive (DB core) | `promptpotter/infrastructure/store/measurement_archive.py` |

## Backbone symbol index

Verified `Symbol → file:line` (line numbers as of last codemap update; re-grep if it looks stale).

### Domain
| Symbol | File:line |
|---|---|
| `SearchPoint` (abstract) | `promptpotter/domain/search_point.py:21` |
| `JobSearchPoint` (frozen target spec) | `promptpotter/domain/search_point.py:33` |
| `PromptTemplate` (8-field scheme) | `promptpotter/domain/opt_search_point.py:45` |
| `OptSearchPoint` (optimizer working state) | `promptpotter/domain/opt_search_point.py:204` |
| `PipelineSchema` | `promptpotter/domain/pipeline_schema.py:108` |
| `ResumeCheckpointKind` (exhaustive enum) | `promptpotter/domain/run_records.py:38` |
| `CycleDir` / `RootCycleDir` (path newtypes) | `promptpotter/domain/cycle_paths.py:29` / `:30` |

### Application — optimization
| Symbol | File:line |
|---|---|
| `l1_generate` | `promptpotter/application/optimization/l1/generate.py` |
| `l1_score` | `promptpotter/application/optimization/l1/score.py` |
| `execute_round` (round loop) | `promptpotter/application/optimization/l1/execute.py` |
| `run_l1_critique` | `promptpotter/application/optimization/l1/critique.py:29` |
| `run_layer_transition` (L2/L3 dispatch) | `promptpotter/application/optimization/transitions.py:72` |
| `LayerStrategy` | `promptpotter/application/optimization/escalation/firing.py:136` |
| `escalate_l2` | `promptpotter/application/optimization/escalation/firing.py:464` |
| `EscalationState` | `promptpotter/application/optimization/escalation/state.py:88` |
| `EscalationRule` | `promptpotter/application/optimization/escalation/rules.py:28` |
| `DEFAULT_ESCALATION_RULES` | `promptpotter/application/optimization/escalation/rules.py:55` |
| `EscalationInputs` | `promptpotter/application/optimization/escalation/decide.py:36` |
| `decide_escalation` | `promptpotter/application/optimization/escalation/decide.py:64` |
| `DispatchHub` | `promptpotter/application/optimization/dispatch/hub/facade.py:63` |
| `DispatchHub.fill_l1` / `.fill_fixed` | `dispatch/hub/facade.py:78` / `:105` |
| `build_bundle` | `promptpotter/application/optimization/dispatch/hub/builder.py:27` |
| `INJECTIONS` (slot registry) | `promptpotter/application/optimization/dispatch/hub/injections.py:436` |
| `validate_template` | `promptpotter/application/optimization/dispatch/hub/facade.py:42` |
| `load_optimizer_prompt` | `promptpotter/application/optimization/dispatch/llm_call.py:449` |
| `RunCallbacks` (typed event constructor) | `promptpotter/application/run_observers.py:49` |
| `RESUME_CHECKPOINT_GATING` | `promptpotter/application/optimization/resume_and_fork/decisions.py:53` |

### Application — scoring / bootstrap
| Symbol | File:line |
|---|---|
| `score_search_point` (gateway) | `promptpotter/application/scoring/search_point_scorer.py:398` |
| `measure_sample` | `promptpotter/application/scoring/sample_measurement.py:242` |
| `compile_scorer` | `promptpotter/application/scoring/formula/compiler.py` |
| `compute_composite_fitness` | `promptpotter/application/scoring/metrics.py:210` |
| `Session` | `promptpotter/application/bootstrap/session.py:83` |
| `ScoringContext` | `promptpotter/application/bootstrap/session.py:48` |

### Infrastructure
| Symbol | File:line |
|---|---|
| `CycleEventLog` (sole ledger ingress) | `promptpotter/infrastructure/ledger.py:41` |
| `DerivedView` (projection base) | `promptpotter/infrastructure/projections/base.py:28` |
| `LiveDashboardView` (root-only) | `promptpotter/infrastructure/projections/live_dashboard/view.py:95` |
| `AuditTrailView` (per-cycle) | `promptpotter/infrastructure/projections/audit_trail.py:99` |
| `PoBBStreamView` | `promptpotter/infrastructure/projections/pobb_stream.py:32` |
| `Stores` (frozen composite) | `promptpotter/infrastructure/store/stores.py:96` |
| `build_stores` | `promptpotter/infrastructure/store/stores.py:115` |
| `BackendClient` | `promptpotter/infrastructure/backend.py:70` |
| `OpenAICompatibleClient` | `promptpotter/infrastructure/llm/openai_compat.py` |
| `AnthropicClient` | `promptpotter/infrastructure/llm/anthropic.py` |

### Connectors
| Symbol | File:line |
|---|---|
| `Connector` (protocol) | `promptpotter/connectors/protocol.py:29` |
| `TermNorm` `CONNECTOR` instance | `promptpotter/connectors/termnorm.py:214` |
| `promptpotter` self-`CONNECTOR` instance | `promptpotter/connectors/promptpotter.py` (M12 inner-cycle) |
| Connector registry (`CONNECTORS` + `get`) | `promptpotter/connectors/__init__.py:16` / `:22` |

### Presentation
| Symbol | File:line |
|---|---|
| `_run_init_body` (fresh-mode helper) | `promptpotter/presentation/cli/commands/init.py:78` |
| `cmd_optimize` | `promptpotter/presentation/cli/commands/optimize.py:300` |
| `main()` (entry point) | `promptpotter/presentation/cli/campaign_runner.py:50` |
| `--from` flag | `promptpotter/presentation/cli/parsers.py:84` |
| `--fork-on-divergence` flag | `promptpotter/presentation/cli/parsers.py:100` |
| `--sweep` flag | `promptpotter/presentation/cli/parsers.py:108` |
| `LiveDisplay` | `promptpotter/presentation/views/live/display.py:58` |

### Config
| Symbol | File:line |
|---|---|
| `APP_VERSION` | `promptpotter/config/settings.py:11` |
| `PROMPT_STRING_FIELDS` | `promptpotter/config/settings.py:26` |

## Module map (1 line each)

**`promptpotter/domain/`** — frozen models, pure types
- `search_point.py` — `SearchPoint` base + `JobSearchPoint` (frozen target)
- `opt_search_point.py` — `PromptTemplate` (8-field) + `OptSearchPoint` (lineage, L2/L3, memory)
- `pipeline_schema.py` — pipeline shape parsed from `GET /pipeline`
- `run_records.py` — cycle event-log records + `ResumeCheckpointKind`
- `cycle_paths.py` — `CycleDir` / `RootCycleDir` path newtypes
- `escalation_signals.py` — `EscalationSignal`, `ValidationFailure`, `RuntimeFailure` types
- `l1_layout.py` — L1 layout / panel value object
- `scoring.py`, `validators.py`, `phases.py`, `backend.py`, `sample.py`, `connector.py`, `round_diagnostics.py` — leaf value types

**`promptpotter/application/optimization/`** — orchestration
- `cycle.py` — `Cycle` state container (rounds / population / stall counters)
- `l1/` — round loop: `generate.py`, `score.py`, `resume.py`, `execute.py`, `critique.py`, `population.py`, `stats.py`
- `dispatch/` — info-flow ingress: `hub/` (`injections.py` for `INJECTIONS`, `facade.py` for `DispatchHub` + `validate_template`, `builder.py` for `build_bundle`, `bundle.py` for `InjectionBundle` types), `llm_call.py`, `schemas.py` (manifest at `datasets/_optimizer/pipeline.json`)
- `pobb/` — `elimination.py` (PoBBCheck/Config, DegradationCheck), `elevation.py` (compare-cycle arbitration)
- `validators/` — `l1_strict.py` (schema), `l1_behavior.py` (soft checks), `l2_l3.py` (L2/L3 output)
- `escalation/` — `state.py` (FSM), `decide.py` (router), `rules.py`, `firing.py` (L2/L3 strategies)
- `resume_and_fork/` — `decisions.py` (gating), `resume.py`, `replayers.py`, `fork_siblings.py`
- top-level — `task_context.py` (decompose), `transitions.py` (L2/L3 transitions), `round_analysis.py` (compute_round_diagnostics)

**`promptpotter/application/scoring/`** — single-gateway scoring
- `search_point_scorer.py` — `score_search_point()` gateway
- `sample_measurement.py` — per-sample backend measurement
- `formula.py` — scorer compilation + rescoring
- `metrics.py` — composite fitness computation
- `evaluators.py` — query-level + sample-level evaluators

**`promptpotter/application/intelligence/`** — discovery views
- `exploration.py` — Rasch-sort exploration
- `hard_sample_sorter.py`, `hard_sample_archive.py` — hard-sample discovery + archive
- `indexes/` — `AxisIndex`, `SampleIndex`, `ConfigIndex` materialized views

**`promptpotter/application/bootstrap/`** — wiring
- `session.py` — `Session` container, `ScoringContext`
- `wiring.py` — service init: stores, LLM clients, connectors → `Session`

**`promptpotter/application/sweep/`** — cheap L1 candidate A/B (`--sweep` mode)
**`promptpotter/application/datasets/`** — dataset loaders + per-dataset pipeline overlay
**`promptpotter/application/origin.py`** — origin cycle resolution

**`promptpotter/infrastructure/`**
- `ledger.py` — `CycleEventLog` (sole ingress, fork-aware)
- `backend.py` — `BackendClient` wire adapter + session lifecycle
- `llm/` — `OpenAICompatibleClient`, `AnthropicClient`, provider registry (`base.py`, `openai_compat.py`, `anthropic.py`, `registry.py`, `models.py`, `rate_limit.py`, `json_parse.py`)
- `projections/` — `DerivedView` subclasses: `LiveDashboardView` (`live_dashboard/`, root-only, writes `dashboard.json`), `AuditTrailView` (per-cycle, writes `round_NNNN.json`), `PoBBStreamView`, `live_state.py`
- `store/` — `Stores` composite + leaf stores: `campaign_store/` (store + ledger_scan + index_helpers), `backend_store`, `session_store`, `sweep_store`, `measurement_archive` (DB core), `archive_views`
- `tracing/` — observability fan-out: file/MLflow/Langfuse sinks, replay, bridge, events

**`promptpotter/connectors/`** — pluggable wire adapters
- `protocol.py` — `Connector` shape + `WireAdapter` / `SessionProtocol`
- `termnorm.py` — TermNorm `CONNECTOR` (production backend)
- `promptpotter.py` — self-connector for M12 optimizer-of-the-optimizer (registered; inner-cycle dispatch pending)
- `__init__.py` — `CONNECTORS` dict + `get(name)`

**`promptpotter/presentation/`**
- `cli/campaign_runner.py` — `main()` entry; per-command bodies live in `cli/commands/{optimize,init,sweep,compare}.py`
- `cli/parsers.py` — argparse schema (`--from`, `--fork-on-divergence`, `--sweep`)
- `cli/session.py` — `SessionCtx`, `load_session`, `load_campaign_config`
- `api/` — read-only FastAPI app (`routers/{backends,campaigns,active,datasets}.py`, `deps.py`)
- `views/` — `live/` (`LiveDisplay` + per-sample/candidate/phase formatters), `render/` (text + markdown + heatmap + sweep + sp_diff), view models, `notebook_run.py`

**`promptpotter/shared/`** — `statistics.py`, `hashing.py`, `spend.py`, `errors.py`
**`promptpotter/config/`** — `settings.py` (`APP_VERSION`, `PROMPT_STRING_FIELDS`), `logging.py`, `log_redaction.py`

## Hot workflows (recipes)

Six high-frequency multi-file recipes. Touch the listed files **in order**.

**1. Register a new connector**
1. `promptpotter/connectors/protocol.py` — confirm `Connector` shape covers needs
2. `promptpotter/connectors/{newname}.py` — implement wire adapter + session + `CONNECTOR = Connector(name="{newname}", …)`
3. `promptpotter/connectors/__init__.py` — add to `_REGISTRY`
4. `datasets/{dataset}/pipeline.json` — set `backend_type: "{newname}"`

**2. Add a new INJECTIONS slot**
1. `promptpotter/application/optimization/dispatch/hub/injections.py` — add `_Injection` row to `INJECTIONS` (`:436`); write its renderer fn
2. Caller side: if a new bundle field is needed, extend the `InjectionBundle` (`dispatch/hub/bundle.py`) / `build_bundle()` (`dispatch/hub/builder.py:27`)
3. `datasets/{name}/prompts/{node}.json` — add `{{slot_name}}` to the relevant prompt
4. Validation runs automatically at template-load time via `validate_template` (`dispatch/hub/facade.py:42`)

**3. Add an escalation rule**
1. `promptpotter/application/optimization/escalation/rules.py` — append `EscalationRule(...)` row to `DEFAULT_ESCALATION_RULES` (`:55`); set priority + predicate + action
2. If predicate reads new state: extend `EscalationInputs` (`decide.py:36`) and snapshot construction in `decide_escalation` (`:64`)
3. If new firing logic: `escalation/firing.py` (currently only `escalate_l2` at `:464`)

**4. Add a new projection**
1. `promptpotter/infrastructure/projections/base.py` — confirm `DerivedView.on_record` covers the records you'll consume
2. `promptpotter/infrastructure/projections/{new}.py` — subclass `DerivedView`, override `on_record` hooks, declare write allowlist
3. `promptpotter/application/bootstrap/session.py` — bind to ledger in `Session` init
4. `promptpotter/infrastructure/store/stores.py` — if a new on-disk artifact, add path/store
5. `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` — update allowlist if you added a new artifact root

**5. Add a new evaluator**
1. `promptpotter/application/scoring/evaluators.py` — add `Evaluator` class + register
2. `promptpotter/domain/scoring.py` — extend if a new `Scorer` type is needed
3. `datasets/{name}/campaign.json` — list evaluator under `scoring.evaluators`

**6. Extend L1 evidence panel / signal**
1. `promptpotter/domain/opt_search_point.py` — add field to `OptSearchPoint` if persistent
2. `promptpotter/application/optimization/dispatch/hub/injections.py` — add renderer fn + `INJECTIONS` row for the new signal
3. `promptpotter/application/optimization/l1/` — populate / use in panel-building logic
4. `datasets/{name}/prompts/l1_generate.json` — add `{{slot_name}}` placeholder
5. Per the **writer-needs-vocabulary** rule: any layer that *writes* this field needs its prompt to render the type/value space — wire that injection too

## Invariant landmarks

Self-enforcing primitives — if you're about to add a sidecar, check the matching primitive first.

- `ResumeCheckpointKind` exhaustive enum + `RESUME_CHECKPOINT_GATING` registry — `domain/run_records.py:38` + `application/optimization/resume_and_fork/decisions.py:53` (import-time exhaustiveness check on `:74`)
- `DerivedView.on_record` — `infrastructure/projections/base.py:28` — sole projection dispatch path
- `DispatchHub.INJECTIONS` + `validate_template` — `application/optimization/dispatch/hub/injections.py:436` + `dispatch/hub/facade.py:42` — typo in a template fails at module load
- `EscalationState` private counters / observation-only mutation — `application/optimization/escalation/state.py:88`
- `EscalationRule` priority + first-match-wins — `escalation/rules.py:28` / `decide.py:64`
- Layer purity tests — `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations`, `::test_cycle_does_not_import_prompt_surface`, `::test_no_direct_artifact_writes_outside_stores`, `::test_artifact_sets_are_disjoint_and_well_formed`
- Frozen domain types — `JobSearchPoint`, `PromptTemplate`, `OptSearchPoint` are Pydantic frozen models; mutate via `.derive()`
- Path newtypes — `CycleDir` / `RootCycleDir` in `domain/cycle_paths.py:29-30` guard projection writes

## File-naming conventions (guess paths, don't grep)

- **Per-dataset config:** `datasets/{name}/{pipeline.json,campaign.json,task_description.md,scan_variants.json}`
- **Per-dataset prompts:** `datasets/{name}/prompts/{l1_generate,l1_critique,l2_context,l3_plan}.json` (also per-node prompts: `datasets/{name}/prompts/{node}.json`)
- **Per-layer contracts:** `promptpotter/{layer}/CLAUDE.md` (layer ∈ `application`, `domain`, `infrastructure`, `presentation`, `connectors`)
- **Per-cycle on-disk:** `.promptpotter/campaigns/{root_cycle_id}/[forks|diag|sweeps/]{cycle_id}/{index.json,log.md,rounds/,prompts/,.runtime/{cache/,streams/,events.jsonl}}`
- **Cross-cycle archive:** `.promptpotter/campaigns/{root_cycle_id}/archive/measurements/{hash}.json`
- **Operator workspace:** `.promptpotter/sessions/{session_id}/active_session.json`

## Anti-shim graveyard (delete on sight)

The codebase has **zero backward-compatibility surface**. These patterns are bugs:

- `// removed`, `# old name`, `# kept for parity`, `# kept for callers`
- Re-export aliases (`OldName = NewName`, `from .x import NewName as OldName`)
- `try/except ImportError` shims for renamed modules
- `dict.get(new, dict.get(old, default))` for renamed keys
- `getattr(obj, "new", getattr(obj, "old", default))` for renamed fields
- Methods existing solely to map old → new names
- `# legacy …` branches and their justifying comments
- `(formerly module.x)` breadcrumbs in comments
- "Phase 2 cleanup will replace this" docstrings
- No-op stubs with "kept for X" docstrings
- `dict.get("new") or dict.get("old", default)` fallbacks

The word **`legacy`** in code = code smell. The word **`deprecated`** is only sanctioned for the fatal-warning sample lifecycle (`is_deprecated`, `deprecated_samples`, `retry_of_deprecated_cache`, `RoundResult.deprecated`).

## Where is X (lookup table)

| Question | Answer |
|---|---|
| Single LLM-call site for optimizer prompts? | `application/optimization/dispatch/llm_call.py::load_optimizer_prompt` then `DispatchHub.fill_*` then `compile_prompt` |
| Where does the loop decide to escalate? | `application/optimization/escalation/decide.py::decide_escalation` |
| Where are prompt slots registered? | `application/optimization/dispatch/hub/injections.py::INJECTIONS` (`:436`) |
| Where is the ledger appended? | `infrastructure/ledger.py::CycleEventLog.append` (sole ingress) |
| Where do CLI flags live? | `presentation/cli/parsers.py` |
| Where is the scoring gateway? | `application/scoring/search_point_scorer.py::score_search_point` |
| Where is the composite fitness formula compiled? | `application/scoring/formula/compiler.py::compile_scorer` |
| Where is the round loop? | `application/optimization/l1/execute.py::execute_round` |
| Where do L2/L3 transitions dispatch? | `application/optimization/transitions.py::run_layer_transition` |
| Where is `dashboard.json` written? | `infrastructure/projections/live_dashboard/view.py::LiveDashboardView` |
| Where are per-round audit JSONs written? | `infrastructure/projections/audit_trail.py::AuditTrailView` |
| Where is the MeasurementArchive (DB core)? | `infrastructure/store/measurement_archive.py` |
| Where is the connector registry? | `connectors/__init__.py::CONNECTORS` (`:16`) + `::get` (`:22`) |
| Registered connectors today? | `termnorm` (`connectors/termnorm.py::CONNECTOR` `:214`) and `promptpotter` self-connector (`connectors/promptpotter.py`, M12 inner-cycle, wiring pending) |
| Where is provider/model resolved per call? | `infrastructure/llm/openai_compat.py::OpenAICompatibleClient` / `llm/anthropic.py::AnthropicClient` |
| Where is the dataset overlay merged onto wire payloads? | `application/datasets.py::load_dataset_node_overlay` (`:405`) → `application/config.py::configure_and_apply_pipeline` (`:283`) |
| Where is `Session` wired? | `application/bootstrap/wiring.py` + `application/bootstrap/session.py::Session` |
| Where do resume divergence decisions live? | `application/optimization/resume_and_fork/decisions.py` (`RESUME_CHECKPOINT_GATING` at `:53`) |
| Where is the typed event constructor for ledger writes? | `application/run_observers.py::RunCallbacks` |
| Where is the live terminal display? | `presentation/views/live/display.py::LiveDisplay` (subscribes to ledger via `DerivedView.on_record`) |
| Where is `PROMPT_STRING_FIELDS`? | `config/settings.py:26` |
| Where is the L1 critique call? | `application/optimization/l1/critique.py::run_l1_critique` |
| Where is L1 generate? | `application/optimization/l1/generate.py::l1_generate` |
| Where is the read-only HTTP API? | `presentation/api/` (routers: `backends`, `campaigns`, `active`, `datasets`) |
| Where is the webapp Next.js source? | `webapp-react/` (static export at `webapp/out/`, served under `/ui`) |
| Hard-sample views on disk (parity with the webapp toggle)? | Campaign-only: `campaigns/{cycle_id}/hard_samples_campaign.json`. Workspace (= campaign + cross-cycle archive): `campaigns/{cycle_id}/hard_samples_workspace.json`. Tenant-wide archive snapshot for one backend: `archive/measurements/hard_samples_workspace_{backend_id}.json`. All three written at every round-end finalize by `presentation/writers.py::write_hard_samples_artifacts`; webapp serves the per-cycle ones via `GET /campaigns/{cycle_id}/hard_samples?view=campaign|workspace`. |
| Opt-in: seed a cycle's Rasch from the cross-cycle archive? | Four boolean flags on `application/config.py::ExplorationConfig`: `seed_initial_scoring_set_from_archive` (round-0 picks hardest from archive δ_s), `seed_evolve_from_archive` (per-candidate sorter folds archive in), `seed_heatmap_from_archive` (log.md heatmap uses workspace fit), `heatmap_show_archive_candidates` (whether to include archive `content_hash[:12]` IDs on the heatmap Y-axis). All default off; archive observations themselves are always harvested at `Cycle.start` for the always-written workspace JSON. |

## Per-layer deep dives (progressive disclosure)

Load only when touching the layer:

- `promptpotter/CLAUDE.md` — L1/L2/L3 agent contracts
- `promptpotter/application/CLAUDE.md` — orchestration shape
- `promptpotter/domain/CLAUDE.md` — frozen models
- `promptpotter/infrastructure/CLAUDE.md` — ledger + projections + stores
- `promptpotter/presentation/CLAUDE.md` — CLI + API + views
- `promptpotter/connectors/CLAUDE.md` — connector contract
- `tests/CLAUDE.md` — test charter

Topical docs (`docs/`): `docs/architecture.md` (§0 + load-bearing surface), `docs/developer/conventions.md` (full style + code-shape), `docs/developer/dispatch-hub.md` (canonical info-flow), `docs/developer/stable-api.md` (v1 fork-readiness surface), `docs/glossary.md` (domain vocabulary + canonical file pointers), `docs/operations/persistence-and-state.md` (tree + recovery).
