# Code Layout

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema, ScoringEnv — pure, no I/O
├── application/
│   ├── campaign/    # campaign lifecycle + thin orchestration (Session, RunListener, Decision records)
│   ├── optimization/  # THE CORE LOOP — L1/L2/L3 nodes, critique, llm_call, restructure
│   ├── recon/         # TEMPLATE — dormant sensitivity-scan archive, preserved for future revival
│   ├── intelligence/  # SHARED materialized view — SearchMemory, variant_library, adaptive_prefix
│   ├── scoring/       # score_search_point gateway, measure_sample, stale-data protocol
│   └── datasets/
├── infrastructure/  # backend/, store/, llm/, tracing/, persistence/
├── presentation/    # cli/, api/, ui/, views/ — thin per-surface adapters
├── shared/          # leaf utilities (errors, constants, statistics, scoring formula compile)
└── config/          # settings, APP_VERSION, logging
```

**Directionality rule (strict):** `intelligence/` MUST NOT import from `optimization/` — it's shared ground. Every layer below it may import from `intelligence/`.

For the canonical symbol → file index, see [code-map.md](code-map.md).

---

## Three-layer I/O architecture (INVARIANT)

PromptPotter separates three kinds of output. Each kind has exactly one owner; violations are caught in tests.

- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. New artifacts go in `CAMPAIGN_ARTIFACTS` (per-cycle, in `campaigns/{cycle_id}/`) or `SESSION_ARTIFACTS` (per-session, in `sessions/{session_id}/`); `tests/test_artifact_parity.py` enforces both allowlists.
- **Display** (per-entry-point) — caller passes a `RunListener`. MUST NOT write to disk.
- **Control** (per-entry-point) — file-based control surface (CLI) or kernel interrupt (notebook). MUST NOT write campaign artifacts.

Mixing these is how campaigns end up with orphan state files, display code that breaks when there's no terminal, or control signals that silently mutate the persisted tree. The separation is an invariant, not a guideline.

---

## SearchPoint hierarchy

```
SearchPoint (abstract)
├── JobSearchPoint      — target pipeline configuration (frozen)
└── PromptTemplate      — 8-field prompt scheme (render/compile)
        └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

Every service signature follows the same shape: `f(SearchPoint, PipelineSchema, dataset) → scores`. Every state is traced at both layers:

- **Target layer** — `JobSearchPoint` → `dataset_runs/` (content-addressed, shared across cycles).
- **Optimizer layer** — `OptSearchPoint` → trial JSON in `campaigns/{cycle_id}/` (per-round checkpoint).

Both layers must be independently reconstructable from disk. When adding new optimizer state, it MUST flow through `OptSearchPoint` for persistence — no sidecar state.

---

## Persistence on disk

Sessions and campaigns are separate concepts. Today the relation is 1:1; the layout is wired so a session can host multiple campaigns later (1:N) without a reorg.

```
.promptpotter/
  active_session.json                    # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/               # per-session: operator workspace
      session.json                       # session metadata
      journal.md / notes.md              # notebook ↔ Claude exchange
      control.json                       # HITL pause/resume
    campaigns/{cycle_id}/                # per-cycle: all artifacts for one optimization
      index.json                         # campaign metadata + trial index
      dashboard.json                     # live counters
      output.log / log.md
      trials/trial_NNNN.json             # resume source of truth
      candidates/round_NNNN.json         # pre-scoring checkpoint
      rounds/round_NNN.json              # per-round LLM action audit
      events.jsonl                       # observability mirror (not read for state)
      langfuse/                          # trace persistence
      prompts/{family}/{version}/        # rendered optimizer prompts
      archived/resumed_at_{ts}/          # mid-cycle rewind history
    library/                             # cross-cycle: shared reference data
      backends/{backend_id}/             # backend profile + datasets
      dataset_runs/                      # content-addressed evaluation archive
      search_memory.json                 # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
```

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`. For operator-level walkthrough of these files, see [../operations/persistence-and-state.md](../operations/persistence-and-state.md).

---

## Scoring pipeline

`score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway for scoring archival and observability. Three early-exit paths live in `application/optimization/nodes/l1/measure.py::score_candidates` — full-run cache hit, validation-failure synthetic zero, and mid-evaluation escalation — detailed in [self-healing-internals.md](self-healing-internals.md).

Per-node cache reuse happens inside `measure_sample()` in `application/scoring/sample_measurement.py`. The gateway accepts a `ScoringEnv` bundle (in `domain/scoring.py`) with backend client, store, backend id, pipeline schema, observer, and the compiled scorer.

---

## Pipeline parameters

Always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from the backend's `GET /pipeline` — zero backend-specific constants in PromptPotter. See [node-standard.md](node-standard.md) for the JSON declaration format.

---

## Entry points (maturity order)

Features land left → right.

1. **Notebook** — `notebooks/optimization_campaign.ipynb`; `presentation/ui/campaign/` is pure display.
2. **CLI** — `python -m promptpotter` at `presentation/cli/`. Core path: `init → [set-task] → optimize → show-results`.
3. **FastAPI** — `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
4. **Next.js webapp** — planned; zero code today.

Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`. Live-phase per-query output is notebook-only today.
