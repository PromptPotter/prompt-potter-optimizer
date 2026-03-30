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
         and failure modes to both loops.  *(M8 Wave 5)*
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
    ├── JobSearchPoint       — user's job: model + temp + pipeline_params (frozen)
    └── OptSearchPoint       — optimizer state: prompt fields + L2/L3 + memory (mutable)
```

**JobSearchPoint** — flat, frozen, content-hashable target evaluation specification: `pipeline_params` (the rendered prompt lives inside `pipeline_params` as a node config value).

**OptSearchPoint** — inherits from SearchPoint. Prompt decomposition fields (`persona`, `task_intent`, etc.) + lineage (`id`, `parent_id`, `changes_description`) + L2 state (`optimizer_params`, `task_context`) + L3 state (`plan`) + optimization memory (`critique_text`, `thinking_styles`, `escalation_journal`). Mutable. `render()` assembles fields; `to_job_search_point()` projects into JobSearchPoint for evaluation.

**PipelineSchema** / **PipelineNode** — describes a pipeline (target or optimizer). Both TermNorm and the optimizer pipeline parse into PipelineSchema.

**EvalContext** — infrastructure bundle for evaluation calls (`backend_client`, `store`, `pipeline_schema`, `obs`, etc.).

**SearchMemory** *(planned — M8 Wave 5, not yet implemented)* — materialized view over all historical search points and results. Three pillars: parameter impact (effect size + top-5 values per axis), query patterns (tractability, discriminative power), failure modes (bottleneck distribution, failure clusters). Atomic data accessors, no formatting — each consumer composes what it needs. Incrementally updated via watermark.

Universal contract: `f(JobSearchPoint, PipelineSchema, eval_data) → scores`.

## Evaluation Flow

All paths converge on `eval_search_point()` — single gateway for eval persistence. Content-addressed dedup via `eval_content_hash()`. Prompt alias groups link semantically equivalent prompts so historical data is discoverable across forms (transitive resolution).

## Caching & Crash Recovery

- **Content-hash dedup** — same JobSearchPoint + eval data returns cached results instantly
- **Shared store** — sensitivity scan and feedback cycle both write to `dataset_runs/`; coverage advisor discovers all cached results regardless of source
- **Write by experiment_id, read by config similarity** — provenance tagged, but reads use alias groups + `pipeline_params` matching. Data shared across experiments.
- **SearchMemory** *(M8)* — materialized statistical index over `dataset_runs/`, refreshed lazily on watermark staleness. Provides cross-campaign parameter impact, query patterns, and failure mode analysis
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
| `critique` | 5-field analysis agent (strengths, weaknesses, thinking styles, warnings) | After L1 evaluate |
| `l2_refine_context` | Refine `task_context` + meta-settings (creativity, n_variants, sample_size) | L1 patience exhausted or degradation |
| `l3_modify_plan` | Strategic replanning | L2 patience exhausted |

```
  ┌──────────────────────────────────────────────────────────┐
  │  l1_generate ────► l1_evaluate (+ critique)              │
  │       ▲                 │                                │
  │       │  critique (5 fields)                             │
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
  search_memory.json                          # (M8, planned) materialized view index
```

`dataset_runs/` is shared across all eval paths (content-addressed). `campaigns/` holds optimizer-layer trial checkpoints per round.
