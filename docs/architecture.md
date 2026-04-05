# Architecture

## System Overview

Two entry points, shared service core:

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `notebooks/campaign_lib/` (display layer wrapping services). No business logic in the notebook layer.
2. **FastAPI API** (`promptpotter/main.py`) — REST at `/promptpotter/v1/`. Routers: `backends`, `campaigns`, `health`.

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
  │ Query difficulty  │  scan_context  │  select winner            │
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

**Human Loop** — OAT perturbation scan measures which axes matter. You pick the best starting point.

**AI Loop** — Critique-guided feedback cycle. Each round produces a critique (structured failure analysis) feeding the next round's candidate generation alongside sampled thinking styles. `scan_context` provides leaderboard and difficulty analytics when available. L1→L2→L3 escalation on diminishing returns.

**SearchMemory** *(M8 — live)* — cross-campaign intelligence layer. A materialized view over all historical `dataset_runs/` that compounds evaluation data across campaigns. Feeds both loops via atomic data accessors: parameter impact rankings, top-5 historically-best values per axis, query tractability, bottleneck distribution, and failure clusters. Refreshed lazily on watermark staleness.

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

**PipelineSchema** / **PipelineNode** — describes a pipeline (target or optimizer). Both TermNorm and the optimizer pipeline parse into PipelineSchema.

**EvalContext** — infrastructure bundle for evaluation calls (`backend_client`, `store`, `pipeline_schema`, `obs`, etc.). Also carries stale data protocol config (`stale_data_load_protocol`, `search_memory`, `stale_data_observations`).

**SearchMemory** *(M8 — live)* — materialized view over all historical search points and results. Three pillars: parameter impact (effect size + top-5 values per axis), query patterns (tractability, discriminative power), failure modes (bottleneck distribution, failure clusters). Also tracks per-query degradation counts (`query_degradation_rate()`), consumed by the stale data protocol's sampleswitch step. Atomic data accessors, no formatting — each consumer composes what it needs. Incrementally updated via watermark. See [SearchMemory section](#searchmemory-m8-wave-3) below.

Universal contract: `f(JobSearchPoint, PipelineSchema, eval_data) → scores`.

## Evaluation Flow

All paths converge on `eval_search_point()` — single gateway for eval persistence and archival. Prompt alias groups link semantically equivalent prompts so historical data is discoverable across forms (transitive resolution).

**Per-node cache:** All eval reuse is handled by the per-node intermediate cache inside `eval_query_via_backend()`. Each node's output is cached independently with chained upstream dependency — changing one node's config invalidates only that node and downstream, while upstream nodes stay cached. See [Node-Level Cache](#node-level-cache) below.

## Caching & Crash Recovery

| Strategy | Mechanism | Detail |
|----------|-----------|--------|
| **Per-node cache** | `node_cache_key(node, config, upstream_hash)` with chained dependency | See [Node-Level Cache](#node-level-cache) below |
| **Shared store** | Scan + cycle both write to `dataset_runs/` | Coverage discovers all archived results regardless of source |
| **Stale data protocol** | 3-step ladder for degraded queries: rerun → samplescan → sampleswitch | See [optimization.md § Stale Data Load Protocol](optimization.md#stale-data-load-protocol) |
| **SearchMemory** *(M8 — live)* | Materialized statistical index over `dataset_runs/` | See [SearchMemory section](#searchmemory-m8-wave-3) |
| **Graceful interrupt** | First Ctrl+C finishes in-flight call and saves (`"partial": True`) | Partial results archived for SearchMemory |

**Eval flow:**

```
eval_search_point()
  → per-node prefix walk (single cache path)
  → backend call with precomputed={cached upstream outputs}
  → dataset_run_store.save() (archive only)
```

## Pipeline Composability

`pipeline_params` format throughout — nested dicts keyed by node name (e.g. `{"llm_ranking": {"temperature": 0.5}}`). `BackendClient.run_match()` translates to the `node_config` wire-format key at the TermNorm boundary. No flat param names in service code.

## Pipeline Discovery

`GET /pipeline` returns the target pipeline config with resolved registry metadata. `parse_pipeline_response()` builds `PipelineSchema` entirely from the live response. Each node carries an `optimizer` sub-object (`param_keys`, `observation_mappings`). Zero backend-specific constants in PromptPotter code.

## Optimizer Pipeline

Five nodes, declared in `promptpotter/config/optimizer_pipeline.json`:

| Node | Purpose | Trigger |
|------|---------|---------|
| `l1_generate` | Candidate generation (sole `pipeline_params` decider) | Every round |
| `l1_evaluate` | Eval + winner selection + stale data protocol | Every round |
| `critique` | Sole intelligence bridge — structured analysis of eval results (summary, priority_fix, axes, highlights) | After L1 evaluate |
| `l2_refine_context` | Refine `task_context` + meta-settings (creativity, n_variants, sample_size) | L1 patience exhausted or degradation |
| `l3_modify_plan` | Strategic replanning | L2 patience exhausted |

`l1_evaluate` also carries the **stale data load protocol** config: `stale_data_load_protocol` (step sequence), `rerun_trigger_count`, `samplescan_candidates`, `samplescan_threshold`, `sampleswitch_min_degradation_rate` — all tunable via `optimizer.param_keys`.

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ critique)              │
  │       ▲                 │                                │
  │       │  critique OR l2_directive                         │
  │       │  + thinking_styles                               │
  │       └──────── ◄───────┘                                │
  │                                                          │
  │  stall?       ──► l2_refine_context ──► resume L1        │
  │  degradation? ──► l2_refine_context ──► resume L1        │
  │  l2 stall?    ──► l3_modify_plan    ──► resume L2+L1     │
  └──────────────────────────────────────────────────────────┘
```

L1 Generate is the sole `pipeline_params` decider. L2 refines context and meta-settings; L3 modifies the strategic plan. Neither L2 nor L3 touches `pipeline_params` directly. Pluggable `EscalationCheck`s can short-circuit evaluation mid-round and route to L2/L3.

## EXPERIMENT_ID & Config Stability

`EXPERIMENT_ID` is the single source of truth for the notebook. When set, config MUST match the stored experiment — mismatches raise `ValueError`. When `None`, a new experiment is auto-created from the config hash.

- **Writing**: all `dataset_runs`, campaign data, Langfuse traces tagged with `experiment_id`
- **Reading**: results found by step-sequence matching + prompt hash + parameter value extraction, not `experiment_id`

## ProjectStore Disk Layout

```
.promptpotter/projects/{backend_id}/
  backend.json
  sync/experiments/{id}.json
  datasets/{name}.json
  dataset_runs/{run_id}.json
  dataset_runs.json
  smart_search_plans/{plan_id}.json
  campaigns/{campaign_id}.json
  campaigns/{campaign_id}/trial_NNNN.json
  obs/langfuse/events.jsonl
  search_memory.json                          # (M8) materialized view index
  intermediate_cache/{node}_{key}.json          # per-node output cache
```

`dataset_runs/` is shared across all eval paths (content-addressed). `campaigns/` holds optimizer-layer trial checkpoints per round.

### SearchMemory (M8 Wave 3)

> **M8 — live.** Degradation tracking and cross-campaign intelligence operational; incremental refresh via watermark planned.

A materialized view over all historical `dataset_runs/` that compounds evaluation data across campaigns. Persisted at `{backend_id}/search_memory.json` and updated incrementally via watermark (only new dataset runs since last refresh are processed).

**Three analysis pillars + degradation tracking:**

| Pillar | What it tracks | Key accessors |
|--------|---------------|---------------|
| Parameter Impact | Effect size + consistency per axis, top-5 historically-best values | `axis_rankings()`, `top_k_values(axis)`, `axis_impact(axis)` |
| Query Patterns | Hit rate, variance (discriminative power), dominant failure mode per query | `query_tractability()`, `discriminating_queries()`, `dead_queries()` |
| Failure Modes | Bottleneck distribution by terminated_at step, failure clusters | `bottleneck_distribution()`, `failure_clusters()` |
| Degradation | Per-query degradation count (warnings in `pipeline_data.diagnostics`) | `query_degradation_rate(query)` |

Degradation tracking feeds the stale data protocol's `sampleswitch` step: queries with historically high degradation rates are candidates for exclusion from the eval set.

**Design:** Atomic data accessors only -- no LLM text formatting. Each consumer (scan_advisor, L1, L2, critique) queries the subset it needs and composes its own prompt section. This avoids coupling SearchMemory to any particular prompt format.

**Consumer matrix:**

| Consumer | What it reads | Purpose |
|----------|--------------|---------|
| Scan advisor | `axis_rankings()`, `top_k_values()`, `bottleneck_distribution()` | Prioritize impactful axes, suggest historically-best values |
| L1 Generate | `failure_clusters()`, `dead_queries()`, `top_k_values()` | Focus candidates on failure modes, avoid dead-end values |
| L2 Refine | `axis_rankings()`, `bottleneck_distribution()` | Inform context refinement with parameter landscape |
| Critique | `discriminating_queries()`, `failure_clusters()` | Enrich failure analysis with cross-campaign patterns |

Cohort analysis (`cohort_analysis.py`) extends the model: per-query hit/miss results from scans are sliced by failure mode cohort to determine which axes matter most for which failure types. Results are ingested via `ingest_cohort_analysis()`.

Implementation: `promptpotter/services/search/search_memory.py`, `promptpotter/services/search/cohort_analysis.py`.

### Node-Level Cache

Per-node output caching with chained dependency keys. Each node's output is keyed by `node_cache_key(node_name, node_config, upstream_key)` where `upstream_key` is the cache key of the previous node. This creates a dependency chain: if fuzzy_matching config is unchanged, its cache key is stable, making web_search's key stable too (since web_search depends on fuzzy_matching output).

**Prefix walk:** `walk_prefix()` walks the node chain from pipeline start. At each node, checks if `{node}_{key}.json` has the query. Stops at the first miss. Sends all cached outputs as `precomputed` to the backend — it only runs the remaining nodes. When ALL nodes are cached, `_build_local_result()` constructs the result locally without any backend call.

**Single cache layer:** Replaces the former 3-layer lookup (content-hash dedup → config_hash per-query → step-sequence). `dataset_run_store` is archive-only (SearchMemory/campaigns read from it, eval does not).

**Disk layout:**
```
intermediate_cache/
  {node_name}_{cache_key}.json    ← {query: node_output}
```

**Implementation:** `compute_prefix_keys()` and `node_cache_key()` in `intermediate_cache.py`. Wired through `eval_query_via_backend()` in `prompt_eval.py`.

Gracefully no-ops until the target backend supports `node_outputs` in responses and `precomputed` in requests.
