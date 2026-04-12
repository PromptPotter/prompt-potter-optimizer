# Architecture

## System Overview

Four entry points over a shared hexagonal core (`domain/` → `application/` → `infrastructure/` → `presentation/`):

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `promptpotter/presentation/ui/campaign/` (display layer wrapping `application/`). No business logic in the notebook layer.
2. **CLI** — `promptpotter/presentation/cli/campaign_runner.py` — terminal-based HITL workflow: `init → [set-task] → [scan] → [show-scan] → optimize → show-results`
3. **FastAPI API** (`promptpotter/main.py` mounts `presentation/api/`) — REST at `/api/v1/`. Routers: `backends`, `campaigns`.
4. **Next.js webapp** *(planned, M10 → M11)* — browser surface consuming the FastAPI API, reading the M9 file-directory view model.

Layer contents:

- **`domain/`** — `JobSearchPoint`, `OptSearchPoint`, `PipelineSchema`, `ScoringEnv`, `TenantContext`. Pure, no I/O, no logging.
- **`application/`** — `campaign/`, `optimization/` (+ `nodes/`), `search/`, `scoring/`, `datasets/`, `pipeline_discovery.py`. Orchestration and use cases.
- **`infrastructure/`** — `store/`, `backend/`, `llm/`, `tracing/`, `persistence/` (state, control, session_emitter, round_recorder). Every I/O adapter lives here.
- **`presentation/`** — `cli/`, `api/`, `ui/`. Thin per-surface adapters.
- Leaf: `shared/` (errors, constants, scorer compiler) and `config/` (settings, logging).

## Entry Points: One Core, Four Adapters

The four entry points all consume the same `application/` core. They do **not** share render code:

- **CLI** prints to stdout via rich/plain text.
- **FastAPI** returns JSON.
- **Notebook** renders inline HTML + ipywidgets.
- **Webapp** renders React on top of FastAPI's JSON.

Each render layer is ~50 lines per verb. The *logic* lives in exactly one place (`application/<domain>/<verb>.py`); each surface adapts it to its native ergonomics. This is why maintaining four surfaces is cheap: what gets duplicated is ~20 lines of adapter boilerplate per verb, not business logic.

**Maturity order** (features land left → right): Notebook > CLI > FastAPI > webapp. Notebook is the daily driver; the webapp is a polish layer on top of what the other surfaces already expose. Do not invert this order.

## Two-Loop Architecture

```
  HUMAN LOOP                           AI LOOP (Potter)
  ──────────                           ────────────────
  Sensitivity Scan                     Critique-Guided Feedback Cycle
  ┌──────────────────┐                 ┌───────────────────────────┐
  │ Measure axes     │  select best    │ Generate candidates using │
  │ Classify by      │───starting──────►  critique + thinking      │
  │  sensitivity     │  point          │  styles + scan analytics  │
  │ Show leaderboard │                 │ Evaluate via backend,     │
  │ Query difficulty  │  scan_brief  │  select winner            │
  │ Show coverage    │─────────────────► Critique failures →       │
  └──────┬───────────┘                 │  next round               │
         │                             │ L1→L2→L3 escalation      │
         │  all eval data              └────────┬──────────────────┘
         │  feeds back                          │
         └──────────────◄───────────────────────┘
              richer landscape → better starting point → repeat

         SearchMemory *(M8 — live)* aggregates all historical
         evaluation data and feeds parameter impact, query patterns,
         and failure modes to both loops.
```

**Human Loop** — OAT scan measures which axes matter. You pick the best starting point.

**AI Loop** — Critique-guided feedback cycle. Each round: generate candidates, evaluate, critique failures, feed forward. L1→L2→L3 escalation on diminishing returns.

**SearchMemory** — Materialized view over `dataset_runs/` feeding both loops. See [SearchMemory design](../research/search-memory-intelligence.md).

## Two-Layer Tracing

Every piece of state is traced at both layers, independently reconstructable from disk:

- **Target layer**: `JobSearchPoint` → `score_search_point()` → `dataset_runs/` (content-addressed, shared across all eval paths)
- **Optimizer layer**: `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint)

## Data Models

```
SearchPoint (base)           — abstract base, "a point in a search space"
    ├── JobSearchPoint       — user's job: pipeline_params (frozen)
    └── PromptTemplate       — 8-field prompt scheme (render/compile)
            └── OptSearchPoint — + lineage + L2/L3 + memory (mutable)
```

**JobSearchPoint** — flat, frozen, content-hashable target evaluation specification: `pipeline_params` (the rendered prompt lives inside `pipeline_params` as a node config value).

**PromptTemplate** — the 8-field prompt decomposition scheme shared by job prompts and optimizer meta-prompts. Fields: `persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`, `few_shot_examples`, `plan`. Methods: `render()`, `compile_prompt()`, `prompt_field_dict()`, `from_prompt_fields()`. `load_optimizer_prompt()` returns `PromptTemplate`.

**OptSearchPoint** — inherits from PromptTemplate. Adds lineage (`id`, `parent_id`, `changes_description`) + L2 state (`optimizer_params`, `task_context`) + optimization memory (`critique_text`, `thinking_styles`, `escalation_journal`, `warning_inventory`, `l2_directive`). Mutable. `to_job_search_point()` projects into JobSearchPoint for evaluation.

**PipelineSchema** / **PipelineNode** — the node coordinate system for each dataset's pipeline. Nodes are the topmost lookup dimension: O(1) position, membership, and param-ownership queries. `prefix_keys()` computes chained cache keys; `sp_hash()` returns the terminal element (unified identity — no separate hash scheme). `prefix_through()` / `exclude()` slice valid sub-pipelines. Both pipeline backends and the optimizer pipeline parse into PipelineSchema.

**ScoringEnv** — infrastructure bundle for scoring calls (`backend_client`, `store`, `pipeline_schema`, `obs`, `scorer`, stale data protocol config). Defined in `domain/scoring.py`.

**SearchMemory** — Materialized view over `dataset_runs/`. Three pillars: parameter impact, query patterns, failure modes. Atomic accessors, no formatting. See [design doc](../research/search-memory-intelligence.md).

Universal contract: `f(JobSearchPoint, PipelineSchema, dataset) → scores`.

## Evaluation Flow

All paths converge on `score_search_point()` — single gateway for eval persistence and archival. Prompt alias groups link semantically equivalent prompts so historical data is discoverable across forms (transitive resolution).

**Chain-addressed caching:** All eval reuse is addressed by a single node chain (`prefix_keys()`). Two tiers use the same chained hashes: (1) `find_by_prefix_chain()` matches prior dataset_runs by exact or partial prefix for result-level reuse, (2) `IntermediateCache.walk_prefix()` reuses per-node outputs. Each node's output is cached independently with chained upstream dependency — changing one node's config invalidates only that node and downstream, while upstream nodes stay cached. See [Node-Level Cache](#node-level-cache) below.

## Caching & Crash Recovery

| Strategy | Mechanism | Detail |
|----------|-----------|--------|
| **Node chain cache** | `PipelineSchema.prefix_keys()` — chained per-node keys; `sp_hash` = terminal element | See [Node-Level Cache](#node-level-cache) |
| **Shared store** | Scan + cycle both write to `dataset_runs/` | All archived results discoverable |
| **Stale data protocol** | 3-step ladder: rerun → samplescan → sampleswitch | See [optimization.md](optimization.md#stale-data-load-protocol) |
| **SearchMemory** | Materialized index over `dataset_runs/` | See [SearchMemory](#searchmemory) |
| **Graceful interrupt** | First Ctrl+C saves in-flight; partial results archived | — |

**Eval flow:**

```
score_search_point()
  → per-node prefix walk (single cache path)
  → backend call with precomputed={cached upstream outputs}
  → dataset_run_store.save() (archive only)
```

## Pipeline Composability

`pipeline_params` format throughout — nested dicts keyed by node name (e.g. `{"llm_ranking": {"temperature": 0.5}}`). `BackendClient.run_match()` translates to the `node_config` wire-format key at the backend boundary. No flat param names in service code.

## Pipeline Discovery

`GET /pipeline` returns the target pipeline config with resolved registry metadata. `parse_pipeline_response()` builds `PipelineSchema` from the response. Each node carries an `optimizer` sub-object (`param_keys`, `observation_mappings`). Per-dataset `pipeline.json` files in `datasets/{name}/` provide static pipeline declarations (used for reference and future local-only datasets). Backend registration derives `backend_name`/`backend_type` from `pipeline_schema.name`. No backend-specific constants in PromptPotter service code.

### Per-Dataset Scoring

Each dataset can declare a scoring formula in `campaign.json["scoring"]`. If absent, binary exact-match (`hit`) is used. See `shared/scoring.py`.

## Optimizer Pipeline

Five nodes, declared in `promptpotter/config/optimizer_pipeline.json`:

| Node | Purpose | Trigger |
|------|---------|---------|
| `l1_generate` | Candidate generation (sole `pipeline_params` decider) | Every round |
| `l1_evaluate` | Eval + winner selection + stale data protocol | Every round |
| `critique` | Every-round intelligence hub — sole reader of raw eval results + SearchMemory consumer (tractability, axis exhaustion, value trends) | Every round (after L1 evaluate) |
| `l2_refine_strategy` | Escalation-only meta-controller — refine `task_context` + meta-settings; receives round trajectory, candidate comparison, failure group × axis | L1 patience exhausted or degradation |
| `l3_modify_plan` | Strategic replanning; receives SearchMemory aggregate picture (axis rankings, bottlenecks, clusters, persistent failures) | L2 patience exhausted |

`l1_evaluate` also carries the **stale data load protocol** config: `stale_data_load_protocol` (step sequence), `rerun_trigger_count`, `samplescan_candidates`, `samplescan_threshold`, `sampleswitch_min_degradation_rate` — all tunable via `optimizer.param_keys`.

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ critique)              │
  │       ▲                 │                                │
  │       │  critique OR l2_directive                         │
  │       │  + thinking_styles                               │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_strategy ──► resume L1        │
  │  degradation? ──► l2_refine_strategy ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

L1 Generate is the sole `pipeline_params` decider. L2 refines context and meta-settings; L3 modifies the strategic plan. Neither L2 nor L3 touches `pipeline_params` directly. Pluggable `EscalationCheck`s can short-circuit evaluation mid-round and route to L2/L3.

## EXPERIMENT_ID & Config Stability

`EXPERIMENT_ID` is the single source of truth for the notebook. When set, config MUST match the stored experiment — mismatches raise `ValueError`. When `None`, a new experiment is auto-created from the config hash.

- **Writing**: all `dataset_runs`, campaign data, Langfuse traces tagged with `experiment_id`
- **Reading**: results found by step-sequence matching + prompt hash + parameter value extraction, not `experiment_id`

## Persistence Architecture

Two independent persistence tiers with different lifecycles:

### Tier 1 — Session State (ephemeral per-run)

```
{backend_id}/sessions/{session_id}/
  session.json              # init config, phase tracking, task_context
  campaign_state.json       # live UI dashboard (emitter-owned, high-frequency)
  campaign_control.json     # HITL control surface (user/webapp-owned, low-frequency)
  campaign_output.log       # append-only query eval trace
  campaign_log.md           # human-readable round-by-round summary
  rounds/
    round_NNN.json          # action trace (LLM calls, decisions)
    round_NNN_l2.json       # L2 escalation trace (if triggered)
```

Session state survives interrupts for display continuity but is **not** the source of truth for resume.

### Tier 2 — Campaign Store (source of truth for resume)

```
{backend_id}/campaigns/
  {cycle_id}.json                   # campaign registry: config, trial index, status
  {cycle_id}/
    trial_NNNN.json                 # round checkpoint: full opt_sp, results, escalation
    round_NNNN_candidates.json      # pre-eval candidate checkpoint (saved BEFORE eval)
```

Keyed by `cycle_id` (content hash of baseline + dataset + pipeline). Resume: `lifecycle.py` counts trials → `resumed_from_round`, `cycle_init.py` restores from last trial, `round_execution.py` loads persisted candidates. Trial files written on round completion; candidate files written before eval starts.

### `campaign_state.json` — UI Dashboard

Live dashboard for `show-status` and the webapp — **not** an optimizer checkpoint. Written atomically by `CampaignPersistenceEmitter` on every phase/sample/candidate/round event. Emitter-owned: no other writer touches this file during optimization.

### `campaign_control.json` — HITL Control Surface

Bidirectional control file, seeded with defaults by the emitter on init. The CLI `control` command, the webapp, and hand-edits are the only writers; the optimizer reads it at natural checkpoints via `CampaignControlReader`. Keeping the control surface in its own file means emitter flushes never race with user intent edits.

```json
{
  "requested_state": "running",         // user sets: "pause", "resume", "stop"
  "pause_before_l2_scoring": false      // user sets: true to pause before L2
}
```

**`campaign_state.json` schema sections:**

| Section | Fields | Purpose |
|---------|--------|---------|
| Execution | `phase`, `round`, `candidate`, `query`, `patience`, `layer`, `cycle_id` | Current position in the optimization loop |
| Timing | `elapsed_s`, `round_elapsed_s`, `avg_query_time_s`, `eta_s` | Performance tracking |
| Pipeline | `active_nodes`, `excluded_nodes`, `terminated_at`, `cache_hit_rate` | Pipeline health |
| Quality | `hit_rate`, `degraded_count`, `error_count` | Eval quality metrics |
| Best | `best`, `best_round`, `improvement_streak` | Optimization progress |
| Historical | `rounds_completed`, `total_queries_scored`, `total_backend_calls` | Cumulative counters (carried across resumes for display continuity) |
| Config | `model`, `n_variants`, `sp_budget_ttest` | Snapshot of active config |

Per-query / per-candidate / per-round detail lives in `campaign_output.log` (append-only audit trail) and `rounds/round_NNN.json` (per-round action trace). The dashboard is scalar-only — no nested accumulators.

### Shared Data Stores

```
{backend_id}/
  backend.json
  sync/experiments/{id}.json
  datasets/{name}.json
  dataset_runs/{run_id}.json          # content-addressed eval archive (shared across all paths)
  dataset_runs.json
  smart_search_plans/{plan_id}.json
  obs/langfuse/events.jsonl
  search_memory.json                  # (M8) materialized view index
  suffix_cache/{suffix_key}.json        # per-query suffix-hash cache (M9, replaces intermediate_cache)
```

`dataset_runs/` is shared across all eval paths (content-addressed). `campaigns/` holds optimizer-layer trial checkpoints per round.

### SearchMemory

Cross-campaign intelligence layer. Materialized view over `dataset_runs/`, persisted at `{backend_id}/search_memory.json`, refreshed incrementally via watermark. Three pillars: parameter impact, query patterns, failure modes. Atomic data accessors only — each consumer composes its own prompt section.

Full design, accessors, and consumer matrix: [`docs/research/search-memory-intelligence.md`](../research/search-memory-intelligence.md). Implementation: `application/search/search_memory.py`.

### Node-Level Cache

**Suffix-hash cache** (M9, replaces the deprecated `IntermediateCache`). One flat KV store keyed by `suffix_key(input, [node_configs...])` with entries emitted at *every* pipeline cut point — both partial prefixes from the query and partial tails from each intermediate output. Because the backend already returns all `node_outputs` in a single response, populating every cut point costs only hashing (`n(n+1)/2` entries per run).

**Lookup:** O(1) for the common case (identical pipeline) — a single hash check on the full-pipeline suffix key. For a single changed node, either a prefix up-to-the-changed-node hits or a tail from the unchanged upstream hits — still 1–2 lookups. The prior `IntermediateCache` required an O(n) sequential walk (`walk_prefix`) and could only reuse upstream nodes, cascading invalidation through every downstream key when an upstream node changed. The suffix scheme gives symmetric reuse across both upstream and downstream config changes, and enables mid-call short-circuiting when the backend streams intermediates.

**Two layers, different jobs:** `sp_hash` (terminal element of `PipelineSchema.prefix_keys()`) and `find_by_prefix_chain()` in `dataset_run_store` still address the full-run archive layer (SearchMemory, observability, lineage, result-level reuse). The suffix cache is strictly the per-query intermediate layer.

**Disk layout:**
```
suffix_cache/
  {suffix_key}.json    ← {input_hash: {node_outputs}}
```

**Implementation:** `SuffixCache` at `infrastructure/store/suffix_cache.py`, consumed only by `measure_sample()` in `application/scoring/sample_measurement.py`. Full design and complexity analysis: [`docs/architecture/suffix-cache.md`](suffix-cache.md).

Gracefully no-ops until the target backend supports `node_outputs` in responses and `precomputed` in requests.

### Export & Reporting

Paper-ready data export follows a three-layer architecture mirroring the persistence/display split:

```
CampaignStore.load()          ← disk (campaign + trial JSON)
        │
        ▼
campaign/export.py            ← pure transforms (flatten, compare, manifest)
        │                        No I/O, no display. Returns dicts/lists.
        ▼
ui/campaign/             ← markdown rendering (tables, supplemental doc)
        │                        Formatting only, no persistence.
        ▼
cli/export_results.py         ← CLI file I/O (write .md or .json)
```

**`export.py`** functions: `flatten_campaign_trials()`, `compare_campaigns()`, `export_search_memory_summary()`, `export_failure_analysis()`, `export_query_difficulty()`, `build_reproducibility_manifest()`.

**`reporting.py`** functions: `render_comparison_table()`, `render_convergence_table()`, `render_significance_table()`, `render_parameter_impact_table()`, `generate_supplemental()`, `generate_export_json()`.

The supplemental document includes: campaign comparison, convergence, pairwise significance, parameter impact, failure analysis, query difficulty, and a reproducibility manifest. See [`docs/research/benchmarks.md`](../research/benchmarks.md) for the benchmark methodology and result table format.

## Context Object Lifecycle

Seven context/config objects flow through the system. Understanding when each is created, who owns it, and how long it lives is essential for working in the codebase.

### Data flow

```
USER INPUT (notebook / CLI)
  │
  ├─ CampaignConfig (TypedDict, mutable)     ← user-provided dict
  │     │
  │     ▼
  │  configure_pipeline() mutates pipeline_params in-place
  │     │
  ▼     ▼
init_services()
  │
  ▼
SessionEnv (dataclass)                       ← session-scoped infra bundle
  │  store, backend_client, pipeline_schema, index_terms, experiment_extract
  │
  ├──► build_run_config()
  │       │
  │       ▼
  │    RunConfig (Pydantic, immutable)        ← validated optimization config
  │       │
  ├──────►├──► init_cycle_state(config, session)
  │       │       │
  │       │       ├─ LoopState (mutable)      ← round-by-round optimizer state
  │       │       │    └─ opt_sp: OptSearchPoint (source of truth)
  │       │       │    └─ scoring_env: ScoringEnv (uses session.store directly)
  │       │       │
  │       ▼       ▼
  │    runner.run_optimization()
  │       │
  │       │  PER ROUND:
  │       ├──► ContextData (ephemeral)        ← L1 prompt formatting bundle
  │       ├──► CritiqueContext (ephemeral)     ← per-round diagnostic stats
  │       └──► LoopState mutated (current_sp, stall_count, opt_sp, ...)
  │
  ▼
[optional] prepare_scan_brief()
  └─ ScanBrief (read-only)                  ← pre-formatted scan analytics
       injected into RunConfig.scan_brief
```

### Lifecycle table

| Object | Defined in | Created by | Lifetime | Mutated? | Checkpointed? |
|--------|-----------|------------|----------|----------|---------------|
| **CampaignConfig** | `campaign/config.py` | User (notebook/CLI) | Session | Yes (`configure_pipeline` sets `pipeline_params`) | No (stored in campaign metadata) |
| **RunConfig** | `campaign/config.py` | `from_campaign_config()` | Campaign | No (immutable Pydantic model) | No |
| **SessionEnv** | `campaign/campaign_setup.py` | `init_services()` | Session | Rarely (`pipeline_schema` filtered) | No |
| **ScoringEnv** | `domain/scoring.py` | `init_cycle_state()` | Cycle | `stale_data_observations` per eval | No |
| **LoopState** | `campaign/state.py` | `cycle_init._build_baseline_state()` | Cycle | Intensely (every round) | Yes (`opt_sp` + escalation) |
| **CritiqueContext** | `campaign/nodes/critique.py` | `execute_round()` | Per-round | No (read-only stats) | No |
| **ContextData** | `campaign/nodes/formatting.py` | `l1_generate()` | Per-generation | No (formatting input) | No |
| **ScanBrief** | `search/scan_results.py` | `prepare_scan_brief()` | Campaign | No (read-only) | No |

**Key invariants:** All optimizer state lives on `LoopState.opt_sp` (only `opt_sp` is checkpointed). `ScoringEnv.store` is `SessionEnv.store` (no duplication). `CampaignConfig` → `RunConfig` is a one-way transformation; services read `RunConfig` only.
