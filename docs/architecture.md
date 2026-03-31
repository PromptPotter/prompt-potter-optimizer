# Architecture

## System Overview

Two entry points, shared service core:

1. **Jupyter notebook** — `notebooks/optimization_campaign.ipynb` uses `notebooks/campaign_lib/` (display layer wrapping services). No business logic in the notebook layer.
2. **FastAPI API** (`api/main.py`) — REST at `/api/v1/`. Routers: `backends`, `campaigns`, `health`.

All core logic lives in `api/services/`.

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

         SearchMemory (materialized view) aggregates all historical
         evaluation data and feeds parameter impact, query patterns,
         and failure modes to both loops.  *(M8 Wave 3)*
```

**Human Loop** — OAT perturbation scan measures which axes matter. You pick the best starting point.

**AI Loop** — Critique-guided feedback cycle. Each round produces a critique (structured failure analysis) feeding the next round's candidate generation alongside sampled thinking styles. `scan_context` provides leaderboard and difficulty analytics when available. L1→L2→L3 escalation on diminishing returns.

**SearchMemory** *(M8)* — cross-campaign intelligence layer. A materialized view over all historical `dataset_runs/` that compounds evaluation data across campaigns. Feeds both loops via atomic data accessors: parameter impact rankings, top-5 historically-best values per axis, query tractability, bottleneck distribution, and failure clusters. Refreshed lazily on watermark staleness.

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

**EvalContext** — infrastructure bundle for evaluation calls (`backend_client`, `store`, `pipeline_schema`, `obs`, etc.).

**SearchMemory** *(M8 Wave 3)* — materialized view over all historical search points and results. Three pillars: parameter impact (effect size + top-5 values per axis), query patterns (tractability, discriminative power), failure modes (bottleneck distribution, failure clusters). Atomic data accessors, no formatting — each consumer composes what it needs. Incrementally updated via watermark. See [SearchMemory section](#searchmemory-m8-wave-3) below.

Universal contract: `f(JobSearchPoint, PipelineSchema, eval_data) → scores`.

## Evaluation Flow

All paths converge on `eval_search_point()` — single gateway for eval persistence. Content-addressed dedup via `eval_content_hash()`. Prompt alias groups link semantically equivalent prompts so historical data is discoverable across forms (transitive resolution).

## Caching & Crash Recovery

- **Content-hash dedup** — same JobSearchPoint + eval data returns cached results instantly
- **Shared store** — sensitivity scan and feedback cycle both write to `dataset_runs/`; coverage advisor discovers all cached results regardless of source
- **Write by experiment_id, read by config similarity** — provenance tagged, but reads use alias groups + `pipeline_params` matching. Data shared across experiments.
- **SearchMemory** *(M8)* — materialized statistical index over `dataset_runs/`, refreshed lazily on watermark staleness. Provides cross-campaign parameter impact, query patterns, and failure mode analysis. See [SearchMemory section](#searchmemory-m8-wave-3)
- **Intermediate Cache** *(M8 Wave 4)* — per-node output cache keyed by `(upstream_config_hash, query)`. Avoids re-running stable upstream nodes when only ranker params change. See [Intermediate Cache section](#intermediate-cache-m8-wave-4)
- **Graceful interrupt** — first Ctrl+C finishes in-flight call and saves (`"partial": True`); content-hash cache bridges partial results on re-run

## Pipeline Composability

`pipeline_params` format throughout — nested dicts keyed by node name (e.g. `{"llm_ranking": {"temperature": 0.5}}`). `BackendClient.run_match()` translates to the `node_config` wire-format key at the TermNorm boundary. No flat param names in service code.

## Pipeline Discovery

`GET /pipeline` returns the target pipeline config with resolved registry metadata. `parse_pipeline_response()` builds `PipelineSchema` entirely from the live response. Each node carries an `optimizer` sub-object (`param_keys`, `override_map`, `observation_mappings`). Zero backend-specific constants in PromptPotter code.

## Optimizer Pipeline

Five nodes, declared in `api/config/optimizer_pipeline.json`:

| Node | Purpose | Trigger |
|------|---------|---------|
| `l1_generate` | Candidate generation (sole `pipeline_params` decider) | Every round |
| `l1_evaluate` | Eval + winner selection | Every round |
| `critique` | Sole intelligence bridge — structured analysis of eval results (summary, priority_fix, axes, highlights) | After L1 evaluate |
| `l2_refine_context` | Refine `task_context` + meta-settings (creativity, n_variants, sample_size) | L1 patience exhausted or degradation |
| `l3_modify_plan` | Strategic replanning | L2 patience exhausted |

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
- **Reading**: results found by config similarity (alias groups + `pipeline_params` matching), not `experiment_id`

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
  intermediate_cache/{hash}.json               # (M8 Wave 4) per-node output cache
```

`dataset_runs/` is shared across all eval paths (content-addressed). `campaigns/` holds optimizer-layer trial checkpoints per round.

### SearchMemory (M8 Wave 3)

A materialized view over all historical `dataset_runs/` that compounds evaluation data across campaigns. Persisted at `{backend_id}/search_memory.json` and updated incrementally via watermark (only new dataset runs since last refresh are processed).

**Three analysis pillars:**

| Pillar | What it tracks | Key accessors |
|--------|---------------|---------------|
| Parameter Impact | Effect size + consistency per axis, top-5 historically-best values | `axis_rankings()`, `top_k_values(axis)`, `axis_impact(axis)` |
| Query Patterns | Hit rate, variance (discriminative power), dominant failure mode per query | `query_tractability()`, `discriminating_queries()`, `dead_queries()` |
| Failure Modes | Bottleneck distribution by terminated_at step, failure clusters | `bottleneck_distribution()`, `failure_clusters()` |

**Design:** Atomic data accessors only -- no LLM text formatting. Each consumer (scan_advisor, L1, L2, critique) queries the subset it needs and composes its own prompt section. This avoids coupling SearchMemory to any particular prompt format.

**Consumer matrix:**

| Consumer | What it reads | Purpose |
|----------|--------------|---------|
| Scan advisor | `axis_rankings()`, `top_k_values()`, `bottleneck_distribution()` | Prioritize impactful axes, suggest historically-best values |
| L1 Generate | `failure_clusters()`, `dead_queries()`, `top_k_values()` | Focus candidates on failure modes, avoid dead-end values |
| L2 Refine | `axis_rankings()`, `bottleneck_distribution()` | Inform context refinement with parameter landscape |
| Critique | `discriminating_queries()`, `failure_clusters()` | Enrich failure analysis with cross-campaign patterns |

Cohort analysis (`cohort_analysis.py`) extends the model: per-query hit/miss results from scans are sliced by failure mode cohort to determine which axes matter most for which failure types. Results are ingested via `ingest_cohort_analysis()`.

Implementation: `api/services/search/search_memory.py`, `api/services/search/cohort_analysis.py`.

### Intermediate Cache (M8 Wave 4)

Partial pipeline caching that avoids re-running upstream nodes when only the ranker config changes. The `IntermediateCache` (`api/services/stores/intermediate_cache.py`) stores per-node outputs keyed by `(upstream_config_hash, query)`.

When a pipeline has stable upstream nodes (e.g., `fuzzy_matching`, `web_search`) and only the ranker (`llm_ranking`) parameters vary across candidates, cached upstream outputs can be injected as `precomputed` inputs, skipping redundant computation. `PipelineSchema.upstream_config_hash()` computes the cache key from upstream node configs.

Gracefully no-ops until the target backend supports `node_outputs` in responses and `precomputed` in requests.
