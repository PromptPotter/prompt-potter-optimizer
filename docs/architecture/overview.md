# Architecture

## System Overview

Three entry points, shared service core:

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `promptpotter/ui/campaign/` (display layer wrapping services). No business logic in the notebook layer.
2. **CLI** — `promptpotter/cli/campaign_runner.py` — terminal-based HITL workflow: `init → [set-task] → [scan] → [show-scan] → optimize → show-results`
3. **FastAPI API** (`promptpotter/main.py`) — REST at `/api/v1/`. Routers: `backends`, `campaigns`.

All core logic lives in `promptpotter/services/`.

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

- **Target layer**: `JobSearchPoint` → `eval_search_point()` → `dataset_runs/` (content-addressed, shared across all eval paths)
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

**PipelineSchema** / **PipelineNode** — describes a pipeline (target or optimizer). Both pipeline backends and the optimizer pipeline parse into PipelineSchema.

**EvalContext** — infrastructure bundle for evaluation calls (`backend_client`, `store`, `pipeline_schema`, `obs`, stale data protocol config).

**SearchMemory** — Materialized view over `dataset_runs/`. Three pillars: parameter impact, query patterns, failure modes. Atomic accessors, no formatting. See [design doc](../research/search-memory-intelligence.md).

Universal contract: `f(JobSearchPoint, PipelineSchema, dataset) → scores`.

## Evaluation Flow

All paths converge on `eval_search_point()` — single gateway for eval persistence and archival. Prompt alias groups link semantically equivalent prompts so historical data is discoverable across forms (transitive resolution).

**Per-node cache:** All eval reuse is handled by the per-node intermediate cache inside `evaluate_query()`. Each node's output is cached independently with chained upstream dependency — changing one node's config invalidates only that node and downstream, while upstream nodes stay cached. See [Node-Level Cache](#node-level-cache) below.

## Caching & Crash Recovery

| Strategy | Mechanism | Detail |
|----------|-----------|--------|
| **Per-node cache** | `node_cache_key(node, config, upstream_hash)` with chained dependency | See [Node-Level Cache](#node-level-cache) |
| **Shared store** | Scan + cycle both write to `dataset_runs/` | All archived results discoverable |
| **Stale data protocol** | 3-step ladder: rerun → samplescan → sampleswitch | See [optimization.md](optimization.md#stale-data-load-protocol) |
| **SearchMemory** | Materialized index over `dataset_runs/` | See [SearchMemory](#searchmemory) |
| **Graceful interrupt** | First Ctrl+C saves in-flight; partial results archived | — |

**Eval flow:**

```
eval_search_point()
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
  campaign_state.json       # live UI dashboard + HITL control surface
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

### `campaign_state.json` — UI & Control Surface

Live dashboard for `show-status` and HITL checkpoints — **not** an optimizer checkpoint. Written by `CampaignPersistenceEmitter` (read-merge-write on every event). The `control` section is bidirectional: emitter writes execution state, user/webapp writes control signals (`pause`, `resume`, `stop`).

```json
{
  "control": {
    "requested_state": "running",       // user sets: "pause", "resume", "stop"
    "pause_before_l2_eval": false       // user sets: true to pause before L2
  }
}
```

**Schema sections:**

| Section | Fields | Purpose |
|---------|--------|---------|
| Execution | `phase`, `round`, `candidate`, `query`, `patience`, `layer`, `cycle_id` | Current position in the optimization loop |
| Timing | `elapsed_s`, `round_elapsed_s`, `avg_query_time_s`, `eta_s` | Performance tracking |
| Pipeline | `active_nodes`, `excluded_nodes`, `terminated_at`, `cache_hit_rate` | Pipeline health |
| Quality | `hit_rate`, `degraded_count`, `error_count` | Eval quality metrics |
| Best | `best`, `best_round`, `improvement_streak` | Optimization progress |
| Historical | `rounds_completed`, `total_queries_evaluated`, `total_backend_calls` | Cumulative counters (carried across resumes for display continuity) |
| Config | `model`, `n_variants`, `sp_budget_ttest` | Snapshot of active config |
| Accumulators | `current_queries`, `round_candidates`, `last_round` | Ephemeral per-round/candidate data (reset on transitions) |
| Control | `requested_state`, `pause_before_l2_eval` | Bidirectional HITL surface |

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
  intermediate_cache/{node}_{key}.json  # per-node output cache
```

`dataset_runs/` is shared across all eval paths (content-addressed). `campaigns/` holds optimizer-layer trial checkpoints per round.

### SearchMemory

Cross-campaign intelligence layer. Materialized view over `dataset_runs/`, persisted at `{backend_id}/search_memory.json`, refreshed incrementally via watermark. Three pillars: parameter impact, query patterns, failure modes. Atomic data accessors only — each consumer composes its own prompt section.

Full design, accessors, and consumer matrix: [`docs/methods/search-memory-intelligence.md`](../research/search-memory-intelligence.md). Implementation: `services/search/search_memory.py`.

### Node-Level Cache

Per-node output caching with chained dependency keys. Each node's output is keyed by `node_cache_key(node_name, node_config, upstream_key)` where `upstream_key` is the cache key of the previous node. This creates a dependency chain: if fuzzy_matching config is unchanged, its cache key is stable, making web_search's key stable too (since web_search depends on fuzzy_matching output).

**Prefix walk:** `walk_prefix()` walks the node chain from pipeline start. At each node, checks if `{node}_{key}.json` has the query. Stops at the first miss. Sends all cached outputs as `precomputed` to the backend — it only runs the remaining nodes. When ALL nodes are cached, `_build_local_result()` constructs the result locally without any backend call.

**Single cache layer:** Replaces the former 3-layer lookup (content-hash dedup → config_hash per-query → step-sequence). `dataset_run_store` is archive-only (SearchMemory/campaigns read from it, eval does not).

**Disk layout:**
```
intermediate_cache/
  {node_name}_{cache_key}.json    ← {query: node_output}
```

**Implementation:** `compute_prefix_keys()` and `node_cache_key()` in `intermediate_cache.py`. Wired through `evaluate_query()` in `eval_query.py`.

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

Eight context/config objects flow through the system. Understanding when each is created, who owns it, and how long it lives is essential for working in the codebase.

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
BackendContext (dataclass)                    ← session-scoped infra bundle
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
  │       │       │    └─ eval_ctx: EvalContext (uses session.store directly)
  │       │       │
  │       ▼       ▼
  │    optimization_loop.run_optimization()
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
| **BackendContext** | `campaign/campaign_setup.py` | `init_services()` | Session | Rarely (`pipeline_schema` filtered) | No |
| **EvalContext** | `models/eval_context.py` | `cycle_init._setup_eval_context()` | Cycle | `candidate_idx`, `stale_data_observations` per eval | No |
| **LoopState** | `campaign/state.py` | `cycle_init._build_baseline_state()` | Cycle | Intensely (every round) | Yes (`opt_sp` + escalation) |
| **CritiqueContext** | `campaign/critique.py` | `execute_round()` | Per-round | No (read-only stats) | No |
| **ContextData** | `campaign/formatting.py` | `l1_generate()` | Per-generation | No (formatting input) | No |
| **ScanBrief** | `search/scan_results.py` | `prepare_scan_brief()` | Campaign | No (read-only) | No |

**Key invariants:** All optimizer state lives on `LoopState.opt_sp` (only `opt_sp` is checkpointed). `EvalContext.store` is `BackendContext.store` (no duplication). `CampaignConfig` → `RunConfig` is a one-way transformation; services read `RunConfig` only.
