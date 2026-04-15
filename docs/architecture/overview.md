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

## Persistence: Two Tiers

```
{backend_id}/
  sessions/{session_id}/        # Tier 1: live UI dashboard + HITL surface
    session.json
    campaign_state.json         # canonical live state (atomic rewrite)
    campaign_control.json       # HITL pause/resume
    rounds/round_NNN.json
  campaigns/                    # Tier 2: source of truth for resume
    {cycle_id}.json
    {cycle_id}/trial_NNNN.json
  dataset_runs/{run_id}.json    # content-addressed eval archive (shared)
  search_memory.json            # materialized intelligence view
```

Sessions are ephemeral display state; campaigns are the resume source of truth. The canonical session file set is declared in `CAMPAIGN_SESSION_ARTIFACTS` (`promptpotter/infrastructure/persistence/session_emitter.py`) and enforced by `tests/test_artifact_parity.py`. Don't add a new writer that competes with `campaign_state.json`.

Reuse across runs is handled by `DatasetRunStore.load_reusable_results` — prior dataset run entries whose `node_configs` share a prefix with the current searchpoint are replayed without calling the backend.

`obs/langfuse/events.jsonl` is both a replay log and the **fork substrate**: every durable mid-round state change is an appended event carrying a self-contained `state_snapshot`, and `optimize --from <cycle_id>:<event_ref>` is a pointer into it. See [optimization.md § Forking a campaign](optimization.md#forking-a-campaign).

## Where to Read Next

- [optimization.md](optimization.md) — L1/L2/L3 loop, critique, escalation
- [prompt-scheme.md](prompt-scheme.md) — 8-field decomposition, variant library
- [information-flow.md](information-flow.md) — prompt injection map
- [node-standard.md](node-standard.md) — node types, `llm_call()` primitive
- [search-memory-intelligence.md](search-memory-intelligence.md) — three-pillar materialized view
