# Architecture

Orientation page for `docs/architecture/`. Everything deep links out — read this top-to-bottom in three minutes, then jump.

## System Overview

Four entry points over a shared hexagonal core (`domain/` → `application/` → `infrastructure/` → `presentation/`):

1. **Notebook** — `notebooks/optimization_campaign.ipynb` (primary, daily driver).
2. **CLI** — `python -m promptpotter` (`init → [set-task] → optimize → show-results`).
3. **FastAPI** — `promptpotter/main.py` mounts `/api/v1/{backends,campaigns}`.
4. **Next.js webapp** — planned (M10 → M11), reads the file-directory view model.

Layer responsibilities:

- **`domain/`** — `JobSearchPoint`, `OptSearchPoint`, `PipelineSchema`, `ScoringEnv`. Pure, no I/O.
- **`application/`** — `campaign/`, `optimization/` (the core loop), `intelligence/` (SearchMemory), `scoring/`, `datasets/`.
- **`infrastructure/`** — `store/`, `backend/`, `llm/`, `tracing/`, `persistence/`.
- **`presentation/`** — `cli/`, `api/`, `ui/`. Thin per-surface adapters.
- Leaf: `shared/`, `config/`.

Maturity order (features land left → right): Notebook > CLI > FastAPI > webapp. Don't invert.

## Core Loop

One core feature — the L1-critique-guided optimization loop in `application/optimization/`.

```
  CORE LOOP
  ────────────────────────────────────────────
  5 LLM call sites:
    restructure    (one-time setup)
    l1_generate    (every round)
    l1_critique    (every round)
    l2_context     (on stall)
    l3_plan        (rare)
                │
                ▼
    SearchMemory — cross-campaign intelligence
    writes evaluations; reads aggregate history
```

## Two-Layer Tracing

Every piece of state is traced at both layers, independently reconstructable from disk:

- **Target layer** — `JobSearchPoint` → `score_search_point()` → `dataset_runs/` (content-addressed, shared).
- **Optimizer layer** — `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint).

## SearchPoint Hierarchy

```
SearchPoint (abstract)
  ├── JobSearchPoint       — frozen target spec (pipeline_params)
  └── PromptTemplate       — 8-field prompt decomposition
        └── OptSearchPoint — + lineage, L2/L3, optimizer memory (mutable)
```

All scoring services share one contract: given `JobSearchPoint + PipelineSchema + dataset` → produce scores. Prompt scheme: [prompt-scheme.md](prompt-scheme.md).

## Persistence

Sessions and campaigns are separate concepts. A session is the operator workspace; a campaign is one optimization cycle inside it. Today the relation is 1:1; the layout is wired so a session can host several campaigns over time without reorg. The workspace-wide active cycle is recorded in `.promptpotter/active_session.json` (single source of truth).

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/             # per-session: operator workspace
      session.json                     # session metadata
      journal.md / notes.md            # notebook ↔ Claude exchange
      control.json                     # HITL pause/resume
    campaigns/{cycle_id}/              # per-cycle: all artifacts for one optimization
      index.json                       # campaign metadata + trial index
      dashboard.json                   # live counters
      output.log / log.md
      trials/trial_NNNN.json           # resume source of truth
      candidates/round_NNNN.json       # pre-scoring checkpoint
      rounds/round_NNN.json            # per-round LLM action audit
      events.jsonl                     # observability mirror (not read for state)
      langfuse/                        # trace persistence
      prompts/{family}/{version}/      # rendered optimizer prompts
      archived/resumed_at_{ts}/        # mid-cycle rewind history
    library/                           # cross-cycle: shared reference data
      backends/{backend_id}/           # backend profile + datasets
      dataset_runs/                    # content-addressed evaluation archive
      search_memory.json               # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
```

Prior evaluation results are automatically replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run.

Reuse across runs is handled by `DatasetRunStore.load_reusable_results` — prior dataset run entries whose `node_configs` share a prefix with the current searchpoint are replayed without calling the backend.

`events.jsonl` is a **pure observability mirror** — nothing reads it for state reconstruction. Resume and the mid-cycle rewind are driven entirely by `trials/trial_NNNN.json`, which carries the full serialized optimizer state. See [optimization.md § Resuming mid-cycle](optimization.md#resuming-mid-cycle).