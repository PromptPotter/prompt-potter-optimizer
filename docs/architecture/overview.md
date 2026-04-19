# Architecture

Orientation page for `docs/architecture/`. Everything deep links out — read this top-to-bottom in three minutes, then jump.

## System Overview

Four entry points over a shared hexagonal core (`domain/` → `application/` → `infrastructure/` → `presentation/`):

1. **Notebook** — `notebooks/optimization_campaign.ipynb` (primary, daily driver).
2. **CLI** — `python -m promptpotter` (`init → [set-task] → optimize → show-results`).
3. **FastAPI** — `promptpotter/main.py` mounts `/api/v1/{backends,campaigns}`.
4. **Next.js webapp** — planned (M10 → M11), reads the file-directory view model.

Layer contents:

- **`domain/`** — `JobSearchPoint`, `OptSearchPoint`, `PipelineSchema`, `ScoringEnv`. Pure, no I/O.
- **`application/`** — `campaign/`, `optimization/` (the core loop), `intelligence/` (SearchMemory), `scoring/`, `datasets/`.
- **`infrastructure/`** — `store/`, `backend/`, `llm/`, `tracing/`, `persistence/`.
- **`presentation/`** — `cli/`, `api/`, `ui/`. Thin per-surface adapters.
- Leaf: `shared/`, `config/`.

Maturity order (features land left → right): Notebook > CLI > FastAPI > webapp. Don't invert.

## Core Loop

One core feature — the critique-guided optimization loop in `application/optimization/`.

```
  CORE  (application/optimization/)
  ────────────────────────────────
  Critique-Guided Optimization Loop
  ┌──────────────────────────────┐
  │ 5 LLMs:                      │
  │  restructure  (one-time)     │
  │  l1_generate  (every round)  │
  │  critique     (every round)  │
  │  l2_context   (on stall)     │
  │  l3_plan      (rare)         │
  └──────────────┬───────────────┘
                 │
                 ▼
      application/intelligence/ — SearchMemory
      writes evaluations; reads aggregate history
```

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

Sessions and campaigns are separate concepts with two mint points per `init`. A session is the operator workspace identifier; a campaign is one optimization cycle inside it. Today the relation is 1:1; the layout is wired so a session can host several campaigns over time (1:N) without any reorg. Each campaign records its parent in `index.json::parent_session_id`; each session points at its current cycle in `session.json::current_cycle_id`. Tenant is the outer axis; per-tenant content splits into three peer trees:

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/             # per-session: operator workspace
      session.json                     # metadata + current_cycle_id pointer
      journal.md / notes.md            # notebook ↔ Claude exchange
      control.json                     # HITL pause/resume
    campaigns/{cycle_id}/              # per-cycle: all artifacts for one optimization
      index.json                       # campaign metadata + trial index + parent_session_id
      dashboard.json                   # live counters
      output.log / log.md
      trials/trial_NNNN.json           # resume source of truth
      candidates/round_NNNN.json       # pre-scoring checkpoint
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
      mlruns/                          # MLflow SDK tracking root (opt-in)
      search_memory.json               # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
      obs/                             # orphan-event fallback for file_only emits
```

The canonical artifact sets are declared in `promptpotter/infrastructure/persistence/session_emitter.py` (`CAMPAIGN_ARTIFACTS` for per-cycle files, `SESSION_ARTIFACTS` for per-session files) and enforced by `tests/test_artifact_parity.py`. Don't add a new writer that competes with `dashboard.json`. Non-CLI entry points (notebook, smoke tool, future API/webapp) auto-mint both a session and a cycle via `run_optimization()` when the caller passes `session_id=""` — CLI `init` mints eagerly and passes both ids through, so there is no double-mint.

Reuse across runs is handled by `DatasetRunStore.load_reusable_results` — prior dataset run entries whose `node_configs` share a prefix with the current searchpoint are replayed without calling the backend.

`events.jsonl` is a **pure observability mirror** — nothing reads it for state reconstruction. Resume and the mid-cycle rewind feature (`optimize --from <round>`) are driven by `campaigns/{cycle_id}/trials/trial_NNNN.json`, which carries the full serialized `OptSearchPoint`. See [optimization.md § Resuming mid-cycle](optimization.md#resuming-mid-cycle).

## Where to Read Next

- [optimization.md](optimization.md) — L1/L2/L3 loop, critique, escalation
- [prompt-scheme.md](prompt-scheme.md) — 8-field decomposition, variant library
- [information-flow.md](information-flow.md) — prompt injection map
- [node-standard.md](node-standard.md) — node types, `llm_call()` primitive
- [search-memory-intelligence.md](search-memory-intelligence.md) — three-pillar materialized view
