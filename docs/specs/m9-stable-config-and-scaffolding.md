# M9: Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0

**Version:** 0.2.0
**Date:** 2026-04-28
**Status:** Complete (2026-04-28). Tracks 2, 3, 6, 7 shipped; Tracks 4 and 5 superseded by cleaner outcomes (renderer unification via `LiveDisplay` + artifact tree; recon archival eliminated the third seed source). Track 1 (optimizer-prompt tuning) lifted to M10.
**Depends on:** M8 Campaign Intelligence (Complete)

> **Post-ship note (2026-04-19).** Track 7 shipped with `ReconConfig` + `SessionEnv.recon_brief` wired through so the recon path kept working. A follow-up cleanup on the same branch removed those seams along with the CLI `recon`/`show-recon` subcommands, the notebook UI wrappers, and the `recon_brief` parameter through L1. `application/recon/` remains as a dormant code archive (see CLAUDE.md).

---

## Context

M9 is foundation work. The optimization loop (L1/L2/L3) is functionally complete through M8 with SearchMemory cross-campaign intelligence, but three gaps block publication and production:

1. **Flat service layout.** `promptpotter/services/` mixes orchestration with I/O and has files up to 37KB. A multi-tenant webapp lands on top of this as duplication or leakage.
2. **Single dataset/pipeline assumption.** Nothing in store paths or campaign state cleanly distinguishes HotPotQA from GSM8K from TermNorm running in the same project.
3. **No shared view model across entry points.** Notebook renders from in-memory state. CLI dashboard polls live state. The future webapp would be a third independent renderer. Artifact-write parity is closed (`run_optimization` auto-mints a session+cycle pair when the caller passes `session_id=""`, so notebook/smoke/future-API produce the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`); **view-model unification remains** — Track 4 below.

> **Stable optimizer-prompt configuration moved to M10** — see [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md). The "tune the four optimizer meta-prompts" track was lifted out of M9 in favor of a dedicated milestone with a behavior-ledger framework, a `rounds_to_95` headline metric, and a manual-iteration cadence. M9 is now infrastructure-only.

M9 delivers the foundation. M10 tunes the optimizer prompts on top of it. M11 populates the foundation with benchmark results. M12 generalizes the connector.

## Tracks

### Track 2: Hierarchy Refactor (Hexagonal Layout)

See standalone spec (archived as DONE): [`archive/m9-hierarchy-refactor.md`](archive/m9-hierarchy-refactor.md).

Shape `promptpotter/` into `domain / application / infrastructure / presentation / shared / config`. Move-only; fat-file splits deferred to follow-up specs. Tenant seam shaped (`domain/tenant.py` + optional `SessionEnv.tenant`) but not enforced.

### Track 3: Multi-Dataset / Multi-Pipeline Support — **DONE (outcome met; spec deliverables superseded by Track 6)**

**Outcome:** Five datasets coexist as peers under `datasets/` (`lca-termnorm/`, `bbeh/`, `gsm8k/`, `hotpotqa/`, `aime_2025/`), each with its own `campaign.json`, `pipeline.json`, `prompts/`, and `task_description.md`. The original deliverable shape (path components for dataset/pipeline, five-field active-session pointer, separate `pipeline_name` field, `--pipeline` CLI flag) was abandoned in favor of Track 6's cleaner two-tree + content-hash cycle identity.

**What shipped:**

1. `dataset_name` lives on `Session` (`application/bootstrap.py`) and `CampaignConfig` (`application/config.py`). `pipeline_name` is **not** a separate field — pipeline identity is carried by `pipeline_schema.name` + `JobSearchPoint.content_hash(dataset)` (truncated, `cycle_` prefix).
2. Store layout is `{tenant_id}/campaigns/{cycle_id}/` flat — datasets and pipelines became identity inputs to `cycle_id` instead of path components. Cleaner outcome than the originally-planned `{backend_id}/{dataset}/{pipeline}/...` nesting.
3. `active_session.json` carries `{tenant_id, session_id, cycle_id}` (`infrastructure/store/stores.py:312`). Backend, dataset, and pipeline identity are reconstructed from `session.json` state and the live `pipeline.json`. `cmd_optimize` recomputes the cycle hash on every run; if it differs, a fresh session+cycle auto-mints before baseline runs.
4. CLI: `--dataset-name` exists on `init` (`presentation/cli/campaign_runner.py`). `--pipeline` was not built — pipeline selected by editing `datasets/{name}/pipeline.json`; revisit in M12 when the connector swap motivates it.
5. Multi-dataset coexistence demonstrated across five datasets including TermNorm + BBEH.

**Closed deliverables (drop or defer):** `pipeline_name` separate field — covered by content-hash identity; `--pipeline` CLI flag — defer to M12; five-field `active_session.json` payload — closed as covered by `session.json` state.

### Track 4: File-Directory UI v0 (Webapp Preparation) — **DONE (renderer unified; the literal `views/` subtree was abandoned)**

**Outcome:** Renderer unification happened. There is no `sessions/{session_id}/views/` subtree — instead, the existing `campaigns/{cycle_id}/` artifact tree (`dashboard.json` + `log.md` + `trials/`) became the shared view model, and `LiveDisplay` (`promptpotter/presentation/views/live.py`) became the shared renderer that both CLI and notebook call. Same outcome as the spec's intent (one render path, one view model, future webapp can read both), different layout.

**What shipped:**

1. Shared view model lives at `campaigns/{cycle_id}/` rather than under sessions: `dashboard.json` (live counters, in-flight payload, per-round node I/O snapshot), `log.md` (derived markdown digest, regenerated on every round-complete + finalize), `index.json` (campaign metadata + final block), `trials/trial_NNNN.json` (per-round optimizer checkpoint). For forked cycles, `dashboard.json` + `output.log` bind to the family root cycle so a single tail covers the whole family.
2. Mix of small JSON + Markdown matches the spec's format intent. Append-only `output.log` for raw HIT/MISS history; pure-render `log.md` (safe to delete and recompute).
3. `LiveDisplay` is the single renderer. CLI uses it (`presentation/cli/campaign_runner.py`); notebook uses it (`presentation/views/notebook_run.py`). No parallel render pipelines.
4. Notebook orchestration (`init_notebook_session`, `prepare_scoring_context_notebook`, `run_optimization_notebook`) is a thin wrapper around the shared `_run_optimization` + `LiveDisplay`. Renderer divergence closed.
5. Webapp-reads contract documented informally in `CLAUDE.md § Superuser Monitoring`. **A frozen view-schema doc is deferred to M11** as the webapp's first entry-criterion.

**Closed deliverables (drop):** `sessions/{session_id}/views/` subtree — abandoned in favor of the artifact tree as view model.

**Non-goal:** pretty HTML, React, or any JS. That's M11/M12.

### Track 5: CLI Unification — Unified Seed Sources — **SUPERSEDED**

**Status:** Superseded by Track 7's recon archival. The typed three-value vocabulary (`fresh` / `resume` / `recon`) collapsed to two real seed sources after recon was deleted from `main`: fresh-default (no flag = load baseline from `datasets/{name}/prompts/`) and `--from <int>` resume (`presentation/cli/campaign_runner.py`). With only two values, a typed `--from` vocabulary buys nothing. Cross-cycle lineage shipped separately as `--fork-on-divergence` (see `docs/operations/rewind-and-fork.md`).

**What replaced it:** The CLI is two write verbs (`init` + `optimize`) with `--from <ROUND>` for in-cycle rewind and `--fork-on-divergence` for sibling cycles. Notebook reaches parity through the shared `run_optimization` orchestration in `presentation/views/notebook_run.py`. FastAPI parity is deferred to M11/M12 webapp work.

---

### Track 6: Directory Reorganization — 2-Dir Layout with Convention Compliance

**Problem:** Today the project store under `{backend_id}/` has ~8 top-level directories (`obs/`, `campaigns/`, `sessions/`, `dataset_runs/`, `adaptive_recon_plans/`, `sync/`, `executions/`, `datasets/`) plus loose files. That doesn't map cleanly to the future webapp dashboard, which should have a 1:1 relationship with directories (each top-level dir ≈ one main tab). Three specific problems compound the mess:

1. **`obs/langfuse/events.jsonl` is mislabeled as Langfuse-native.** Langfuse has no on-disk convention — it's cloud-API only. `events.jsonl` is a PromptPotter-custom human navigation log that happens to live under `obs/langfuse/` today, which implies (falsely) that it's part of the Langfuse schema.
2. **MLflow layout is non-compliant.** `obs/experiments/{campaign_id}/{run_id}/` hand-rolls `meta.yaml` / `params/` / `metrics/` files but uses UUID-like `campaign_id` at the experiment level (MLflow requires numeric experiment IDs) and diverges from MLflow's line formats (`artifacts/` is empty, metric step-timestamp encoding may not match). `mlflow ui` doesn't work out of the box.
3. **Session and campaign identities need explicit separation.** `init` mints a session; `optimize` mints a cycle. They are *not* the same thing — a session is the operator workspace and may host multiple campaigns over time (1:N), even if today the relation is 1:1. The directory layout makes this explicit by giving each its own top-level tree, and by recording the parent link on every campaign.

**Target:** Three top-level directories under a tenant partition: per-session, per-cycle, and cross-run reference.

```
.promptpotter/projects/{tenant_id}/
├── active_session.json                  # pointer: { tenant_id, session_id, cycle_id }
├── sessions/{session_id}/
│   ├── session.json                     # operator metadata
│   ├── journal.md                       # user narrative (notebook ↔ Claude exchange)
│   └── notes.md                         # Claude notes
├── campaigns/{cycle_id}/
│   ├── trials/trial_{round:04d}.json    # resume WAL (state)
│   ├── .cache/candidates/round_{round:04d}.json  # pre-scoring checkpoint (internal)
│   ├── .cache/rounds/round_{round:03d}.json      # per-round LLM action audit (internal)
│   ├── dashboard.json                   # live counters
│   ├── events.jsonl                     # human navigation log
│   ├── output.log                       # per-query audit
│   ├── recon.json                       # this campaign's scan result (was recon_results.json)
│   ├── index.json                       # cycle metadata + trial index + parent_session_id
│   ├── prompts/{family}/{version}/      # rendered optimizer prompts
│   ├── langfuse/                        # PromptPotter's shadow of Langfuse data model
│   │   ├── traces/{trace_id}.json
│   │   ├── observations/{trace_id}/{obs_id}.json
│   │   ├── scores/{trace_id}.jsonl
│   │   ├── datasets/{name}/{item_id}.json
│   │   └── state.json                   # cloud id mappings
│   └── archived/resumed_at_{ts}/        # rewind history
└── library/
    ├── datasets/{name}/                 # canonical prompts, pipeline, items, config
    ├── backends/{backend_id}/           # backend.json, connector_profile.json, executions/, datasets/, sync/
    ├── dataset_runs/{run_id}.json       # content-addressed query cache
    ├── dataset_runs.json                # locked index
    ├── mlruns/{numeric_exp_id}/{run_id}/...   # MLflow SDK-managed tracking root
    ├── recon_plans/{plan_id}.json       # reusable plan definitions (was adaptive_recon_plans/)
    ├── search_memory.json
    ├── prompt_aliases.json
    └── restructure_cache.json
```

**Three convention fixes:**

1. **MLflow via the SDK at `library/mlruns/`.** Delete the hand-rolled `FileSink._write_mlflow_run()` / `_ensure_experiment()`. Call `mlflow.set_tracking_uri(f"file://{library}/mlruns")` + `mlflow.set_experiment(name=f"{tenant_id}/{campaign_id}")`, then `mlflow.start_run()` / `log_params` / `log_metrics` / `set_tags` per round. MLflow assigns numeric experiment IDs itself; campaign identity lives in the experiment *name*. Gated on `settings.MLFLOW_ENABLED` (default `False`) with `mlflow` as an optional `pip extra`. `mlflow ui --backend-store-uri file://…/library/mlruns` works out of the box.
2. **Tenant partition as outer axis.** `{backend_id}` drops out of the outer path entirely — backends become peers under `library/backends/{backend_id}/`. The outer axis becomes `{tenant_id}`, mandatory, defaulting to `"default"` for the single-user CLI. Multi-tenant webapp picks tenant at auth time. `.promptpotter/projects/{tenant_id}/active_session.json` is the per-tenant pointer.
3. **Per-cycle artifacts in `CAMPAIGN_ARTIFACTS` land in `campaigns/{cycle_id}/`; per-session artifacts in `SESSION_ARTIFACTS` land in `sessions/{session_id}/`.** The two sets are disjoint and parity is enforced by `tests/test_artifact_parity.py`.

**Semantic invariant: sessions and campaigns are separate.** Two mint points per `init` (one for each tree); the parent pointer in `index.json::parent_session_id` keeps the link addressable. Code shape:
- `SessionStore` (`infrastructure/store/stores.py`) owns `sessions/{session_id}/`.
- `CampaignStore` owns `campaigns/{cycle_id}/`; its `create()` records `parent_session_id` on the campaign.
- `Stores` dataclass exposes both `sessions` and `campaigns` fields.
- `active_session.json` payload is `{tenant_id, session_id, cycle_id}`.

**Deliverables:**

1. **All store classes repath.** `CampaignStore`, `DatasetRunStore`, `BackendStore`, `PlanStore` (renamed conceptually to recon-plans), `FileSink`, `LangfuseSink`, `session_emitter`, `control`, `round_recorder` — every path string updated.
2. **Tenant threading.** `build_stores(base_dir, tenant_id="default")`; `FileSink.__init__(tenant_id=...)`; `--tenant` CLI flag on every subcommand; `TenantContext` from Track 2 wired through.
3. **MLflow SDK adoption.** `mlflow` added as optional extra in `pyproject.toml`; `FileSink` MLflow block rewritten to use the SDK; hand-rolled writers deleted.
4. **Migration script.** `promptpotter migrate` verb that walks existing `.promptpotter/projects/default/{backend_id}/…` (v2) trees and rewrites them to the new shape (v3). Writes `.promptpotter/schema_version=3` marker. Refuse to start on unmigrated v2 dirs without explicit `migrate` invocation.
5. **Docs updated.** `CLAUDE.md § Architecture`, `docs/operations/persistence-and-state.md`, `docs/operations/rewind-and-fork.md` (path citations), `docs/operations/observability.md` (full sink mapping rewrite + navigation starting points), `docs/operations/cli-reference.md` (new `--tenant` flag).
6. **Test updates.** `tests/test_artifact_parity.py` split into `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` parity assertions plus a check that every campaign records a `parent_session_id`. Any test fixture with a path string in it.

**Sequencing:** Wave 1, alongside Track 2. Both are foundational move-only work; other tracks build on both. Track 4 (file-directory UI v0) should render the *new* tree, not rework around the old one later. Track 5 (CLI unification) needs the two-tree layout in place to know where `init` writes session state versus where `optimize` writes campaign state.

**Non-goals:**
- The webapp itself — that's M11/M12. This plan builds the filesystem the webapp will read.
- Cross-tenant sharing of datasets or backends. Datasets stay per-tenant. If multi-tenant sharing is needed later, add a `library/` sibling to `projects/` at `.promptpotter/` root.
- Touching what the optimization loop does — purely a persistence / layout refactor.

---

### Track 7: Config Aggregate Redesign — **DONE**

Shipped: `LoopConfig` deleted, `CampaignConfig` is Pydantic with nested sub-models, runtime context lives on `Session` (formerly `SessionEnv`). Recon seams (`ReconConfig`, `recon_brief`, `--from recon:<id>`, `recon_report.py`) were removed in the same branch when the recon path itself was archived; `application/recon/` is gone from `main` and preserved at the `recon-archive` git tag.

<details>
<summary>Original migration spec (kept for archaeology)</summary>

**Problem:** Three objects carry state into the optimization loop and their boundaries are accidental:

1. **`LoopConfig`** (`application/campaign/config.py`) — Pydantic, ~25 fields. Mixes user knobs (`l1_patience`, `creativity`, `elimination_alpha`, …) with runtime context (`session_id`, `backend_id`, `project_root`, `pipeline_schema`, `recon_brief`, `dataset_name`). Two lifecycles, one object.
2. **`SessionEnv`** (`application/campaign/campaign_setup.py`) — dataclass. Session-scoped identity, infrastructure handles, and eval data. Missing the runtime fields `LoopConfig` duplicates today (`session_id`, `project_root`, `recon_brief`, derived `pipeline_params`).
3. **`LoopEnv`** (`application/optimization/loop_env.py`) — dataclass. Loop-specific infrastructure handles (`scoring_ctx`, `degradation_checks`, `scoring_dataset`, `cycle_id`, `zero_signal_filter_*`, `resumed_from_round`).

Consequences:
- `CampaignConfig` (TypedDict, user-authored) is silently mutated at runtime — `configure_and_apply_pipeline()` writes derived `pipeline_params` into the user dict at `config.py:450`. `apply_stored_overrides()` and `recon_report.py` do the same. User config is no longer user config.
- `LoopConfig.from_campaign_config()` silently drops unknown keys — `zero_signal_filter_enabled` is the documented case (routed via `LoopEnv`), but any typo (`zero_signal_filtre_enabled`) is also silently dropped. The TypedDict → Pydantic bridge is the only boundary and it's lossy.
- Some fields exist only on `LoopConfig` with defaults (`max_consecutive_errors`, `hard_cap`, `stale_data_load_protocol`, `critique_degradation_threshold`, `critique_near_miss_ratio`) — reachable from code but not from the authored JSON, so users can't override without a code change.
- `pipeline_schema` lives on *both* `LoopConfig` and `SessionEnv`; `backend_id`, `dataset_name` likewise. Call-sites read from whichever happens to be in scope.

**Target aggregate shape:**

```
CampaignConfig  (Pydantic, persisted as datasets/{name}/campaign.json)
    User-authored knobs only. Every field overridable from JSON.
    ├── scoring (string | {per_query, per_round})
    ├── starting_prompt, sp_budget_ttest, recon_sample_size
    ├── exclude_nodes, pipeline_overrides
    ├── optimization (nested sub-model — ALL loop knobs: thresholds, patience,
    │   n_variants, creativity, elimination_alpha, critique_*, stale_data_*,
    │   max_consecutive_errors, hard_cap, zero_signal_filter_*, …)
    ├── optimizer_llm (nested sub-model: provider, model, temperature, max_tokens)
    └── adaptive_recon (nested sub-model: scan settings)

SessionEnv  (runtime context, not persisted)
    Everything session-scoped: identity, infrastructure, data.
    ├── tenant_id, session_id, project_root, backend_id, dataset_name, experiment_id
    ├── store, backend_client
    ├── pipeline_schema                 # derived from GET /pipeline, filtered + overrides baked
    ├── pipeline_params                 # NEW — derived, lives here (was mutated on CampaignConfig)
    ├── recon_brief | None              # NEW — was on LoopConfig
    ├── queries, index_terms, experiment_extract
    └── synced

LoopEnv  (per-run infrastructure, transient)
    Unchanged in shape; gains nothing from redesign.
    ├── scoring_ctx, campaign_store, cycle_id, obs_campaign_id
    ├── scoring_dataset, degradation_checks, resumed_from_round
    └── (zero_signal_filter_* fields MOVE to CampaignConfig.optimization)
```

**Service signatures:** every function that takes `config: LoopConfig` takes `config: CampaignConfig` instead. Runtime context is read from `session: SessionEnv` (already threaded in most paths). Where a service reads both knobs and runtime, both are parameters — no more one-object-that-has-everything.

**Why nested sub-models on `CampaignConfig`:** the persisted `campaign.json` files today already use the nested layout (`campaign.json.optimization.l1_patience`). Keep that shape so existing configs parse unchanged. Access is `config.optimization.l1_patience` at runtime — matches the JSON structure, survives Pydantic validation, type-checked by mypy.

**Approach:**

1. **`CampaignConfig` becomes Pydantic** with three nested sub-models (`OptimizationConfig`, `OptimizerLLMConfig`, `ReconConfig`) plus top-level scalars. All current `LoopConfig` user-knob fields migrate into `OptimizationConfig`. `max_consecutive_errors`, `hard_cap`, `stale_data_load_protocol`, `critique_*_threshold` become overridable via JSON (or deleted if truly no one needs them — audit case-by-case).
2. **A `model_validator(mode='before')` accepts legacy flat OR nested input** — migration is zero-effort for existing `campaign.json` files. Unknown keys raise (no silent drops).
3. **`SessionEnv` extended** with `session_id`, `project_root`, `recon_brief`, `pipeline_params`. `configure_and_apply_pipeline()` returns pipeline_params and sets it on the session; no longer mutates `campaign_config`.
4. **`LoopConfig` deleted.** `from_campaign_config()` deleted. Replace all `config: LoopConfig` with `config: CampaignConfig` across 12 files / 50+ call-sites. Fields previously read from `LoopConfig`-as-context (`config.backend_id`, `config.pipeline_schema`, `config.recon_brief`) now come from `session`.
5. **`apply_stored_overrides()` and `recon_report.py::_merge_recon_pipeline_params()` stop mutating user config.** They return the merged `pipeline_params` and the caller sets `session.pipeline_params`.
6. **Silent-drop bug fixed** as a consequence — there is no bridge to drop through.

**Deliverables:**

1. `CampaignConfig` Pydantic model (nested sub-models, validator accepting flat/nested/legacy input, rejects unknown keys).
2. `SessionEnv` extended with `session_id`, `project_root`, `recon_brief`, `pipeline_params`.
3. `LoopConfig` and `from_campaign_config()` deleted. All 12 files updated to the new signature.
4. `configure_and_apply_pipeline()`, `apply_stored_overrides()`, and recon-result merging stop mutating user config.
5. `LoopEnv.zero_signal_filter_*` fields moved to `CampaignConfig.optimization`; `LoopEnv` reads from `CampaignConfig` at `run_optimization` construction time.
6. Existing `campaign.json` files in `datasets/*/` parse unchanged (validator handles legacy shape). Test fixture coverage extends to both shapes.
7. `docs/developer/code-layout.md` + `CLAUDE.md` updated with the new three-object boundary: *user knobs / session identity / loop infrastructure*.

**Sequencing:** Wave 2, alongside Track 3 (multi-dataset). Track 3 adds `dataset_name` and `pipeline_name` as required session fields — the same surgery that Track 7 is doing on `session_id` / `project_root` / `recon_brief`. Landing them together keeps `SessionEnv`'s shape stable afterwards. Depends on Track 2 (hexagonal layout) being complete so the imports don't move again mid-refactor.

**Non-goals:**
- Changing how `LoopEnv` is built or consumed inside the round loop — apart from moving zero-signal flags, its shape is already clean.
- Reshaping `CampaignConfig`'s JSON layout — persisted files keep the nested shape so users don't edit anything.
- Introducing a facade `RunContext(config, session, loop_env)` object. Callers take whichever of the three they actually need; bundling adds indirection without removing objects.

**Risk:**

| Risk | Impact | Mitigation |
|------|--------|------------|
| 50+ signature flips across `application/` and `presentation/` | Merge conflicts, partial-state bugs during the refactor | Commit per-file or per-module. `LoopConfig` alias kept as a deprecated re-export during the transition, deleted in the final commit. |
| `model_validator` accepting legacy shape hides input errors | User typos land silently in "unknown section" | Validator raises `ValidationError` on unknown top-level keys; within sub-models Pydantic's `extra='forbid'` catches typos. |
| `pipeline_params` off user config breaks notebook cells that inspect it | Notebook UX regressions not caught by tests | Add a smoke test that reads `session.pipeline_params` where `campaign_config["pipeline_params"]` was previously read. |

</details>

---

## Wave Sequencing

```
Wave 1: Track 2 (hierarchy refactor, move-only) + Track 6 (directory reorg, move-only)
        — parallel; both foundational. Track 4's UI should target the post-reorg tree,
        and Track 5's init+optimize collapse depends on Track 6's two-tree layout.

Wave 2: Track 3 (multi-dataset/pipeline) + Track 7 (config aggregate redesign)
        — parallel; Track 3 and Track 7 both reshape SessionEnv — landing together keeps its
        final shape stable.

Wave 3: Track 4 (file-directory UI v0)
        — UI draft happens in the presentation/views/ renderer surface, renders the Track 6 tree

Wave 4: Track 5 (CLI unification — collapse init+optimize, unify seed sources)
        — runs last; depends on Track 2 (hexagonal layout), Track 4
        (stable active-session-pointer semantics), and Track 6 (two-tree layout
        with parent_session_id link)
```

## Entry Criteria

- M8 exit gate passed ✅
- All existing tests pass

## Exit Criteria

- [x] Hexagonal layout in place; all tests green; no `from promptpotter.services` imports remain
- [x] `TenantContext` importable from `promptpotter.domain.tenant`; `Session.tenant` exists (`SessionEnv` was renamed to `Session` in Track 7)
- [x] Multi-dataset coexistence demonstrated across five datasets (`lca-termnorm`, `bbeh`, `gsm8k`, `hotpotqa`, `aime_2025`); pipeline identity carried by `pipeline_schema.name` + `JobSearchPoint.content_hash`, not as a path component
- [x] Renderer unification: `LiveDisplay` (`promptpotter/presentation/views/live.py`) is the shared renderer; CLI live output and notebook both call it; the artifact tree (`campaigns/{cycle_id}/dashboard.json` + `log.md` + `trials/`) is the shared view model. Frozen webapp-reads schema doc deferred to M11
- [x] Seed sources collapsed to two after recon archival: fresh-default and `--from <int>` resume. Typed three-value `--from` vocabulary not built — moot. `--fork-on-divergence` covers cross-cycle lineage
- [x] Three-tree layout: `sessions/{session_id}/` (per-session metadata + journal/notes), `campaigns/{cycle_id}/` (per-cycle artifacts with `parent_session_id`; family-root cycles also carry `dashboard.json` + `output.log` shared with their forks), and `library/` (all cross-run reference). Tenant partition at `.promptpotter/projects/{tenant_id}/`; MLflow via SDK at `library/mlruns/`. `CAMPAIGN_ARTIFACTS` further split into `ROOT_TELEMETRY_ARTIFACTS` + `PER_CYCLE_AUDIT_ARTIFACTS`; parity enforced by `tests/test_artifact_parity.py`
- [x] `LoopConfig` deleted; `CampaignConfig` is Pydantic with nested sub-models (`OptimizationConfig`, `OptimizerLLMConfig`, `ScoringSetConfig`, `HardSampleSorterConfig`) + `extra='forbid'`; runtime fields live on `Session`; `configure_and_apply_pipeline` writes to `session.pipeline_params` not `campaign_config`; all on-disk `campaign.json` files use the nested shape (legacy flat-form `model_validator` not built — no flat-form configs exist in practice)
- [x] `CLAUDE.md` Architecture section updated to reflect new hierarchy

## Key Existing Code

| Area | Files |
|------|-------|
| Optimizer pipeline | `promptpotter/application/optimization/pipeline.py`, `optimizer_pipeline.json` |
| LLM client | `promptpotter/infrastructure/llm.py` |
| Scoring | `promptpotter/application/scoring/formula.py` |
| Dataset builder | `promptpotter/application/datasets/datasets.py` |
| Measurement archive | `promptpotter/infrastructure/store/measurement_archive.py` |
| Session + Campaign stores | `promptpotter/infrastructure/store/stores.py` |
| CLI live output | `promptpotter/presentation/cli/campaign_runner.py`, `promptpotter/presentation/views/live.py` |
| Notebook | `notebooks/optimization_campaign.ipynb` |
| Renderers | `promptpotter/presentation/views/` |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Hierarchy refactor touches every file | High churn, merge risk | Move-only (no splits). Single-commit-per-step. Tree compiles between steps |
| View model over-design | Architectural astronautics before there's a real consumer | Agile: write files, look at them, adjust. Mirror notebook exactly in v0 |
| Multi-dataset path migration | Legacy data becomes inaccessible | Decide migration vs coexistence early in the track; document the rule |
