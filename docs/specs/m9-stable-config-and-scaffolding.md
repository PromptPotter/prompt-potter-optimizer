# M9: Stable Config, Hierarchy Refactor, Multi-Dataset/Pipeline, File-Directory UI v0

**Version:** 0.1.0
**Date:** 2026-04-12
**Status:** Track 7 shipped 2026-04-19 (convergence-efficiency); other tracks ongoing
**Depends on:** M8 Campaign Intelligence (Complete)

> **Post-ship note (2026-04-19).** Track 7 shipped with `ReconConfig` + `SessionEnv.recon_brief` wired through so the recon path kept working. A follow-up cleanup on the same branch removed those seams along with the CLI `recon`/`show-recon` subcommands, the notebook UI wrappers, and the `recon_brief` parameter through L1. `application/recon/` remains as a dormant code archive (see CLAUDE.md).

---

## Context

M9 is foundation work. The optimization loop (L1/L2/L3) is functionally complete through M8 with SearchMemory cross-campaign intelligence, but four gaps block publication and production:

1. **Meta-prompts are proof-of-concept.** `promptpotter/config/optimizer_prompts/` are functional but untuned. They were developed against a multi-node retrieval pipeline and need systematic evaluation before any benchmark number is meaningful.
2. **Flat service layout.** `promptpotter/services/` mixes orchestration with I/O and has files up to 37KB. A multi-tenant webapp lands on top of this as duplication or leakage.
3. **Single dataset/pipeline assumption.** Nothing in store paths or campaign state cleanly distinguishes HotPotQA from GSM8K from TermNorm running in the same project.
4. **No shared view model across entry points.** Notebook renders from in-memory state. CLI dashboard polls live state. The future webapp would be a third independent renderer. Artifact-write parity is closed (`run_optimization` auto-mints a session+cycle pair when the caller passes `session_id=""`, so notebook/smoke/future-API produce the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`); **view-model unification remains** — Track 4 below.

M9 delivers the foundation. M10 populates it with benchmark results. M11 generalizes the connector.

## Tracks

### Track 1: Stable Optimizer Configuration

**Problem:** Meta-prompts in `promptpotter/config/optimizer_prompts/` are functional but proof-of-concept:

| Prompt | File | Temperature | Max Tokens | State |
|--------|------|-------------|------------|-------|
| L1 Generate | `meta_scan_aware.json` | 0.7 | 8192 | Working, tuned for multi-node pipeline references |
| Critique | `critique.json` | 0.3 | 4096 | Working, extensive stat assembly |
| Critique (negative) | `critique_negative.json` | 0.3 | 4096 | Fallback for low accuracy |
| L2 Refine | `l2_refine_strategy.json` | 0.3 | 2048 | Working, clean layer transition |
| L3 Replan | `l3_modify_plan.json` | 0.5 | 2048 | Working, strategic pivots |

Current optimizer model: `openai/gpt-oss-120b` via Groq.

**Approach:** Generic prompts adapt via `task_context` injection — no task-specific sets. The `problem_description` and `instruction` fields use template variables; task details flow through `task_context`.

**Deliverables:**

1. **Evaluation protocol.** Second-order metrics measured at campaign level:

   | Metric | What It Measures | Better = |
   |--------|-----------------|----------|
   | Rounds to convergence | How quickly optimizer finds a good prompt | Lower |
   | Final accuracy | Best accuracy achieved | Higher |
   | L2/L3 escalation frequency | How often L1 stalls and needs meta-intervention | Lower |
   | Candidate diversity | Variety of generated candidates per round | Higher (avoids mode collapse) |
   | Optimizer cost | Total tokens spent on optimizer LLM calls | Lower |

2. **Systematic improvements.** Prompt language refinement, temperature/max_tokens tuning per node, `thinking_style` variants, `answer_format` schema variations, model selection.
3. **Final configs committed** to `promptpotter/config/optimizer_prompts/` with rationale. Feeds paper's "method" section.

**Bootstrap cost mitigation:** Tune meta-prompts against **BBEH mini** (10/task train subset, seed=42 — same split as M10's head-to-head). Small sample, diverse reasoning tasks, known non-saturated at `gpt-oss-120b`. Reserve the full 3-seed protocol for M10's publication numbers. GSM8K and AIME are saturated at this model and are not useful signal for meta-prompt tuning; HotPotQA's saturation is unknown and decided in M10 Wave 1.

**Risk:** Multi-node meta-prompts on LLM-only tasks — pipeline references are irrelevant for benchmarks. Mitigation: generic prompts via `task_context` injection.

### Track 2: Hierarchy Refactor (Hexagonal Layout)

See standalone spec: [`m9-hierarchy-refactor.md`](m9-hierarchy-refactor.md).

Shape `promptpotter/` into `domain / application / infrastructure / presentation / shared / config`. Move-only; fat-file splits deferred to follow-up specs. Tenant seam shaped (`domain/tenant.py` + optional `SessionEnv.tenant`) but not enforced.

### Track 3: Multi-Dataset / Multi-Pipeline Support

**Problem:** A project today implicitly assumes one dataset and one pipeline per backend. Multi-dataset benchmark work (HotPotQA + GSM8K + TermNorm sharing a project) needs dataset/pipeline to be first-class identifiers in campaign state, store paths, and the active-session pointer.

**Deliverables:**

1. `dataset_name` and `pipeline_name` become required fields on campaign state and session env, propagated through `SessionEnv`.
2. Store paths extend to `{backend_id}/{dataset}/{pipeline}/campaigns/{cycle_id}/...`. Legacy paths migrate or coexist (decision open).
3. `active_session.json` carries `{backend_id, dataset_name, pipeline_name, session_id}`.
4. CLI commands accept `--dataset` and `--pipeline` overrides; default comes from the active session.
5. Two datasets demonstrably coexist (`datasets/lca-termnorm/` + one benchmark dataset) in a single project store without collision.

**Open decisions during the track:** migration vs coexistence for legacy data, how `show-status` aggregates across datasets.

### Track 4: File-Directory UI v0 (Webapp Preparation)

**Problem:** Three entry points (notebook, CLI, FastAPI) and a fourth coming (webapp). Notebook renders from in-memory state; CLI dashboard polls live state; webapp would be a third independent renderer. No shared view model. (Artifact-write parity is closed — `run_optimization` auto-mints a session when `session_id=""`. Track 4 is about renderer unification only.)

**Approach:** Instead of each entry point building its own render pipeline, the session writes a flat file-directory "view model" to disk. The CLI, the notebook, and the eventual webapp all read from the same files. The first cut mirrors exactly what the Jupyter notebook already displays — vanilla, no new information surfaces. Think: what a human sees when they `cd` into the session folder and `cat` a few files.

**Deliverables:**

1. A file-directory view model under `sessions/{session_id}/views/` (exact path open). Content is a superset of what the notebook currently displays: round summary, candidate leaderboard, current trajectory, critique text, active SearchPoint.
2. Format TBD during the track — likely a mix of small JSON files for structured data and pre-rendered Markdown snippets for human-readable dashboards. Open: temp vs permanent files, rolling vs append-only.
3. CLI `show-status` becomes a thin renderer that reads the view directory and pretty-prints. No live-state polling.
4. Notebook output becomes a thin renderer that reads the view directory. This closes the remaining notebook ↔ CLI parity gap (renderer divergence); artifact-write parity is already closed via the `run_optimization` auto-mint.
5. Documented "this is what the future webapp reads" contract. M10 Track 3 picks up from here.

**Intentionally open:** exact file layout, whether intermediate/temp views exist alongside permanent ones, how to version the view schema. Decided during the track via agile iteration — write the files, look at them, adjust.

**Non-goal:** pretty HTML, React, or any JS. That's M10/M11.

### Track 5: CLI Unification — Collapse `init` + `optimize`, Unify Seed Sources

**Problem:** `init` and `optimize` are two CLI verbs for what is conceptually one workflow. Nobody runs `init` alone — it sets up a session and sits there; `optimize` is always the next command. The split is an implementation artifact (session creation vs loop execution), not a user-facing distinction.

On top of that, there are three ways a cycle can start, scattered across different flags and implicit behaviors:

- Fresh baseline from `datasets/{name}/prompts/` (implicit, default)
- Resume from last checkpoint in the active session (`optimize` with no args, or `optimize --from <round>` to rewind within the active cycle — see `docs/architecture/optimization.md § Resuming mid-cycle`)
- Recon-brief-seeded start (implicit, lives in session state)

All three are "where does the baseline `OptSearchPoint` come from?" but each one is surfaced differently. Fork-across-cycles (new `cycle_id`, parent pointer, independent trajectory) is explicitly out of scope — `optimization.md § Resuming mid-cycle` records the decision that the WAL complexity it would require is not worth it.

**Why now (M9, not earlier):** Doing this as a standalone change would thrash the notebook UI layer, the API routers, and the active-session-pointer semantics for a gain that's mostly aesthetic. M9's stable-config / hierarchy / file-directory UI refactor is already touching all of these surfaces — Track 5 is cheap when it rides on top of Tracks 2 + 4, and expensive if it lands on its own.

**Deliverables:**

1. **Single loop verb.** Collapse `init` + `optimize` into one command. Working name: `run` (or keep `optimize` and remove `init` as a standalone verb — decided during the track). Creates the session if needed, then runs the loop. The three-command invocation `init → set-task → optimize` collapses to one (with `set-task` staying as an orthogonal concern, optionally merged via flag).
2. **Unified `--from` / `--seed` argument** with a typed vocabulary covering the three real starting conditions:
   - `--from fresh` (default) — load baseline prompt from `datasets/{name}/prompts/`
   - `--from resume[:<round>]` — resume the active cycle; optional `:<round>` rewinds within it (current `optimize --from <int>` behavior, generalized)
   - `--from recon:<recon_id>` — recon-brief-seeded (currently implicit from session state)
   One concept, one knob, discoverable in `--help`.
3. **Notebook + API parity.** The notebook's `run_optimization_notebook()` and FastAPI's `/api/v1/campaigns` routes both need to accept the unified seed vocabulary. This is the part that would thrash the other entry points if done in isolation — M9 Track 4's shared view model and Track 2's hexagonal layout make it tractable.

**Sequencing:** Runs in Wave 3 or later, after Track 2 (hexagonal layout) and Track 4 (file-directory UI v0) are in place. Depends on the active-session pointer semantics being stable, which Track 4 clarifies.

**Non-goal:** reshaping what the loop itself does, or introducing fork-across-cycles. This is a CLI / entry-point refactor — the L1→L2→L3 mechanics are untouched, and cross-cycle lineage stays explicitly out of scope per `optimization.md § Resuming mid-cycle`.

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
│   ├── session.json                     # operator metadata + current_cycle_id pointer
│   ├── journal.md                       # user narrative (notebook ↔ Claude exchange)
│   ├── notes.md                         # Claude notes
│   └── control.json                     # HITL signal
├── campaigns/{cycle_id}/
│   ├── trials/trial_{round:04d}.json    # resume WAL (state)
│   ├── candidates/round_{round:04d}.json # pre-scoring checkpoint (state)
│   ├── rounds/round_{round:03d}.json    # per-round LLM action audit
│   ├── dashboard.json                   # live counters
│   ├── events.jsonl                     # human navigation log
│   ├── output.log                       # per-query audit
│   ├── log.md                           # round-by-round summary
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
- `SessionStore` (`infrastructure/store/session_store.py`) owns `sessions/{session_id}/`.
- `CampaignStore` owns `campaigns/{cycle_id}/`; its `create()` records `parent_session_id` on the campaign.
- `Stores` dataclass exposes both `sessions` and `campaigns` fields.
- `active_session.json` payload is `{tenant_id, session_id, cycle_id}`.

**Deliverables:**

1. **All store classes repath.** `CampaignStore`, `DatasetRunStore`, `BackendStore`, `PlanStore` (renamed conceptually to recon-plans), `FileSink`, `LangfuseSink`, `session_emitter`, `control`, `round_recorder` — every path string updated.
2. **Tenant threading.** `build_stores(base_dir, tenant_id="default")`; `FileSink.__init__(tenant_id=...)`; `--tenant` CLI flag on every subcommand; `TenantContext` from Track 2 wired through.
3. **MLflow SDK adoption.** `mlflow` added as optional extra in `pyproject.toml`; `FileSink` MLflow block rewritten to use the SDK; hand-rolled writers deleted.
4. **Migration script.** `promptpotter migrate` verb that walks existing `.promptpotter/projects/default/{backend_id}/…` (v2) trees and rewrites them to the new shape (v3). Writes `.promptpotter/schema_version=3` marker. Refuse to start on unmigrated v2 dirs without explicit `migrate` invocation.
5. **Docs updated.** `CLAUDE.md § Architecture`, `docs/architecture/overview.md § Persistence`, `docs/architecture/optimization.md § Resuming mid-cycle` (path citations), `docs/architecture/observability-audit.md` (full sink mapping rewrite), `docs/observability.md` (navigation starting points), `docs/cli-workflow.md` (new `--tenant` flag).
6. **Test updates.** `tests/test_artifact_parity.py` split into `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` parity assertions plus a check that every campaign records a `parent_session_id`. Any test fixture with a path string in it.

**Sequencing:** Wave 1, alongside Track 2. Both are foundational move-only work; other tracks build on both. Track 4 (file-directory UI v0) should render the *new* tree, not rework around the old one later. Track 5 (CLI unification) needs the two-tree layout in place to know where `init` writes session state versus where `optimize` writes campaign state.

**Non-goals:**
- The webapp itself — that's M10+. This plan builds the filesystem the webapp will read.
- Cross-tenant sharing of datasets or backends. Datasets stay per-tenant. If multi-tenant sharing is needed later, add a `library/` sibling to `projects/` at `.promptpotter/` root.
- Touching what the optimization loop does — purely a persistence / layout refactor.

---

### Track 7: Config Aggregate Redesign — **DONE**

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
7. `docs/architecture/overview.md` + `CLAUDE.md` updated with the new three-object boundary: *user knobs / session identity / loop infrastructure*.

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

---

## Wave Sequencing

```
Wave 1: Track 2 (hierarchy refactor, move-only) + Track 6 (directory reorg, move-only)
        — parallel; both foundational. Track 4's UI should target the post-reorg tree,
        and Track 5's init+optimize collapse depends on Track 6's two-tree layout.

Wave 2: Track 3 (multi-dataset/pipeline) + Track 7 (config aggregate redesign) + Track 1 (meta-prompt eval protocol)
        — parallel; Track 3 and Track 7 both reshape SessionEnv — landing together keeps its
        final shape stable. Multi-dataset is prerequisite for meta-prompt evaluation on 2+ tasks.

Wave 3: Track 4 (file-directory UI v0) + Track 1 (systematic improvements + final configs)
        — parallel; UI draft happens in the new presentation/ui/ location, renders the Track 6 tree

Wave 4: Track 5 (CLI unification — collapse init+optimize, unify seed sources)
        — runs last; depends on Track 2 (hexagonal layout), Track 4
        (stable active-session-pointer semantics), and Track 6 (two-tree layout
        with parent_session_id link)
```

## Entry Criteria

- M8 exit gate passed ✅
- All existing tests pass

## Exit Criteria

- [ ] Stable meta-prompts documented with rationale, committed to `promptpotter/config/optimizer_prompts/`
- [ ] Hexagonal layout in place; all tests green; no `from promptpotter.services` imports remain
- [ ] `TenantContext` importable from `promptpotter.domain.tenant`; `SessionEnv.tenant` exists
- [ ] Multi-dataset/pipeline working on at least two datasets in a single project store
- [ ] File-directory UI v0 readable by a human browsing the session folder; CLI `show-status` and notebook both render from it
- [ ] Single loop verb (`init` + `optimize` collapsed); unified `--from {fresh,resume[:<round>],recon:<id>}` seed vocabulary; notebook + API accept the same vocabulary
- [ ] Three-tree layout in place: `sessions/{session_id}/` (per-session metadata + journal/notes/control), `campaigns/{cycle_id}/` (per-cycle artifacts with `parent_session_id`), and `library/` (all cross-run reference); tenant partition at `.promptpotter/projects/{tenant_id}/`; MLflow via SDK at `library/mlruns/`; campaigns and sessions cleanly separated with parity tests for both sets
- [ ] `LoopConfig` deleted; `CampaignConfig` is Pydantic with nested sub-models; runtime fields (`session_id`, `project_root`, `recon_brief`, `pipeline_params`) live on `SessionEnv`; no service mutates user config; legacy `campaign.json` files parse unchanged
- [ ] `CLAUDE.md` Architecture section updated to reflect new hierarchy

## Key Existing Code

| Area | Files |
|------|-------|
| Meta-prompts | `promptpotter/config/optimizer_prompts/*.json` |
| Optimizer pipeline | `promptpotter/services/optimizer/pipeline.py`, `optimizer_pipeline.json` |
| LLM client | `promptpotter/services/llm_client.py` |
| Scoring | `promptpotter/shared/scoring.py` |
| Dataset builder | `promptpotter/services/dataset_builder.py` |
| Dataset store | `promptpotter/services/store/dataset_run_store.py` |
| Session store | `promptpotter/services/store/session_store.py` |
| CLI dashboard | `promptpotter/cli/campaign_runner.py` (show-status) |
| Notebook | `notebooks/optimization_campaign.ipynb` |
| UI layer | `promptpotter/ui/campaign/` |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Meta-prompt bootstrap cost | 15K+ LLM calls per variant at full size | Tune on BBEH mini (10/task train, 230 samples); full 3-seed protocol deferred to M10 |
| Multi-node meta-prompts on LLM-only tasks | Pipeline references irrelevant for benchmarks | Generic prompts via `task_context` injection |
| Hierarchy refactor touches every file | High churn, merge risk | Move-only (no splits). Single-commit-per-step. Tree compiles between steps |
| View model over-design | Architectural astronautics before there's a real consumer | Agile: write files, look at them, adjust. Mirror notebook exactly in v0 |
| Multi-dataset path migration | Legacy data becomes inaccessible | Decide migration vs coexistence early in the track; document the rule |
