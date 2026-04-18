# Architecture

Orientation page for `docs/architecture/`. Everything deep links out — read this top-to-bottom in three minutes, then jump.

## System Overview

Four entry points over a shared hexagonal core (`domain/` → `application/` → `infrastructure/` → `presentation/`):

1. **Notebook** — `notebooks/optimization_campaign.ipynb` (primary, daily driver).
2. **CLI** — `python -m promptpotter` (`init → [set-task] → [recon] → optimize → show-results`).
3. **FastAPI** — `promptpotter/main.py` mounts `/api/v1/{backends,campaigns}`.
4. **Next.js webapp** — planned (M10 → M11), reads the file-directory view model.

Layer contents:

- **`domain/`** — `JobSearchPoint`, `OptSearchPoint`, `PipelineSchema`, `ScoringEnv`. Pure, no I/O.
- **`application/`** — `campaign/`, `optimization/` (the core loop), `recon/` (optional scan), `intelligence/` (SearchMemory), `scoring/`, `datasets/`.
- **`infrastructure/`** — `store/`, `backend/`, `llm/`, `tracing/`, `persistence/`.
- **`presentation/`** — `cli/`, `api/`, `ui/`. Thin per-surface adapters.
- Leaf: `shared/`, `config/`.

Maturity order (features land left → right): Notebook > CLI > FastAPI > webapp. Don't invert.

## Core Loop + Optional Recon

One core feature — the critique-guided optimization loop — and one optional pre-step — the recon pass. Independent packages, no cross-imports. The only runtime handoff is `ReconBrief` through `RunConfig.recon_brief`.

```
  OPTIONAL  (application/recon/)       CORE  (application/optimization/)
  ─────────────────────────            ────────────────────────────────
  Sensitivity Scan                     Critique-Guided Optimization Loop
  ┌──────────────────┐                 ┌──────────────────────────────┐
  │ 1 LLM:           │                 │ 5 LLMs:                      │
  │  recon_advisor   │                 │  restructure  (one-time)     │
  │                  │                 │  l1_generate  (every round)  │
  │ OAT perturbation │    ReconBrief   │  critique     (every round)  │
  │ Per-axis Δ       │───(optional)──► │  l2_context   (on stall)     │
  │ Coverage report  │   starting-hint │  l3_plan      (rare)         │
  └────────┬─────────┘                 └──────────┬───────────────────┘
           │                                      │
           │   application/intelligence/ — SearchMemory (shared)
           └──────────────────► ◄─────────────────┘
                  both write evaluations; both read aggregate history
```

Skipping the recon pass leaves `optimize` fully functional — you lose a starting prior on which axes to move first, save the recon LLM cost, and skip a step.

Directionality and import rules: see `CLAUDE.md § Architecture`. Loop internals: [optimization.md](optimization.md). SearchMemory: [search-memory-intelligence.md](search-memory-intelligence.md).

## Two-Layer Tracing

Every piece of state is traced at both layers, independently reconstructable from disk:

- **Target layer** — `JobSearchPoint` → `score_search_point()` → `dataset_runs/` (content-addressed, shared).
- **Optimizer layer** — `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint).

## SearchPoint Hierarchy

```
SearchPoint (abstract)
  ├── JobSearchPoint       — frozen target spec (pipeline_params)
  └── PromptTemplate       — 8-field prompt decomposition
        └── OptSearchPoint — + lineage, L2/L3, memory (mutable)
```

Universal contract: `f(JobSearchPoint, PipelineSchema, dataset) → scores`. Field-by-field detail lives in the model files themselves (`promptpotter/domain/`). Prompt scheme: [prompt-scheme.md](prompt-scheme.md).

## Persistence

Session ≡ campaign — one mint point per cycle. Tenant is the outer axis; everything splits into two peer trees: per-cycle (`campaigns/`) and cross-cycle (`library/`).

```
.promptpotter/
  active_session.json                  # { tenant_id, cycle_id } pointer
  projects/{tenant_id}/
    campaigns/{cycle_id}/              # per-cycle: all artifacts for one run
      index.json                       # metadata + live session state (atomic rewrite)
      dashboard.json                   # live counters
      control.json                     # HITL pause/resume
      output.log / log.md / journal.md / notes.md
      recon.json                       # optional recon result
      trial_NNNN.json                  # resume source of truth
      round_NNNN_candidates.json       # pre-scoring checkpoint
      rounds/round_NNN.json            # per-round LLM action audit
      events.jsonl                     # human navigation log (observability mirror)
      langfuse/
        state.json                     # id maps persisted across resume
        traces/{trace_id}.json
        observations/{trace_id}/{obs_id}.json
        scores/{trace_id}.jsonl
        datasets/{name}/{item_id}.json
      prompts/{family}/{version}/      # rendered optimizer prompts
      archived/resumed_at_{ts}/        # mid-cycle rewind history
    library/                           # cross-cycle: shared reference data
      backends/{backend_id}/{backend.json, connector_profile.json, sync/, executions/, datasets/}
      datasets/{name}/                 # tenant-global datasets (future)
      dataset_runs/{run_id}.json       # content-addressed eval archive
      dataset_runs.json
      recon_plans/{plan_id}.json
      mlruns/                          # MLflow SDK tracking root (opt-in)
      search_memory.json               # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
      obs/                             # orphan-event fallback for file_only emits
```

The canonical per-cycle file set is declared in `CAMPAIGN_SESSION_ARTIFACTS` (`promptpotter/infrastructure/persistence/session_emitter.py`) and enforced by `tests/test_artifact_parity.py`. Don't add a new writer that competes with `dashboard.json`. Non-CLI entry points (notebook, smoke tool, future API/webapp) auto-mint a cycle via `run_optimization()` when the caller passes `session_id=""` — CLI `init` mints eagerly and passes the id through, so there is no double-mint.

Reuse across runs is handled by `DatasetRunStore.load_reusable_results` — prior dataset run entries whose `node_configs` share a prefix with the current searchpoint are replayed without calling the backend.

`events.jsonl` is a **pure observability mirror** — nothing reads it for state reconstruction. Resume and the mid-cycle rewind feature (`optimize --from <round>`) are driven by `campaigns/{cycle_id}/trial_NNNN.json`, which carries the full serialized `OptSearchPoint`. See [optimization.md § Resuming mid-cycle](optimization.md#resuming-mid-cycle).

## Where to Read Next

- [optimization.md](optimization.md) — L1/L2/L3 loop, critique, escalation
- [prompt-scheme.md](prompt-scheme.md) — 8-field decomposition, variant library
- [information-flow.md](information-flow.md) — prompt injection map
- [node-standard.md](node-standard.md) — node types, `llm_call()` primitive
- [search-memory-intelligence.md](search-memory-intelligence.md) — three-pillar materialized view
