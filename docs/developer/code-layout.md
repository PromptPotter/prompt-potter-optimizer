# Code Layout

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema, scoring formula compile — pure, no I/O
├── application/
│   ├── campaign/    # campaign lifecycle + thin orchestration (Session, RunListener, Decision records)
│   ├── optimization/  # THE CORE LOOP — L1/L2/L3 nodes, critique, llm_call, restructure
│   ├── intelligence/  # SHARED materialized view — SearchMemory, variant_library, scoring_set
│   ├── scoring/       # score_search_point gateway, measure_sample, stale-data protocol
│   └── datasets/
├── infrastructure/  # backend/, store/, llm/, tracing/, persistence/
├── presentation/    # cli/, api/, ui/, views/ — thin per-surface adapters
├── shared/          # leaf utilities (errors, constants, statistics)
└── config/          # settings, APP_VERSION, logging
```

**Directionality rule (strict):** `intelligence/` MUST NOT import from `optimization/` — it's shared ground. Every layer below it may import from `intelligence/`.

For the canonical symbol → file index, see [code-map.md](code-map.md).

---

## Three-layer I/O architecture (INVARIANT)

PromptPotter separates three kinds of output. Each kind has exactly one owner; violations are caught in tests.

- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. The allowlists for new artifacts (`CAMPAIGN_ARTIFACTS` per-cycle in `campaigns/{cycle_id}/`, `SESSION_ARTIFACTS` per-session in `sessions/{session_id}/`) live in `tests/test_artifact_parity.py` — the test owns the contract and enforces both sets.
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
    campaigns/{cycle_id}/                # per-cycle: all artifacts for one optimization
      index.json                         # campaign metadata + trial index + final summary
      dashboard.json                     # live counters
      log.md                             # rendered narrative digest (per-round + heatmap + winner)
      output.log                         # append-only HIT/MISS history
      phase_events.jsonl                 # structured phase event stream
      trials/trial_NNNN.json             # resume source of truth
      candidates/round_NNNN.json         # pre-scoring checkpoint
      rounds/round_NNN.json              # per-round LLM action audit
      langfuse/                          # trace persistence (incl. events.jsonl mirror)
      prompts/{family}/{version}/        # rendered optimizer prompts
      archived/resumed_at_{ts}/          # mid-cycle rewind history
    library/                             # cross-cycle: shared reference data
      backends/{backend_id}/             # backend profile + datasets
      dataset_runs/                      # content-addressed evaluation archive
      search_memory.json                 # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
```

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`. For operator-level walkthrough of these files, see [../operations/persistence-and-state.md](../operations/persistence-and-state.md).

---

## Scoring pipeline

`score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway for scoring archival and observability. Three early-exit paths live in `application/optimization/nodes/l1/measure.py::score_candidates` — full-run cache hit, validation-failure synthetic zero, and mid-evaluation escalation — detailed in [self-healing-internals.md](self-healing-internals.md).

Per-node cache reuse happens inside `measure_sample()` in `application/scoring/sample_measurement.py`. `score_search_point` reads infrastructure (store, backend client, backend id, pipeline schema, observer, compiled scorer) directly off the `Session` argument; the previously separate `ScoringEnv` bundle was inlined when callers all converged on `Session`. The compiled scorer lives in `domain/scoring.py` (`compile_scorer`, `SCORING_FUNCTIONS`).

---

## Pipeline parameters

Always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from the backend's `GET /pipeline` — zero backend-specific constants in PromptPotter. See [node-standard.md](node-standard.md) for the JSON declaration format.

---

## Entry points (maturity order)

Features land left → right.

1. **Notebook** — `notebooks/optimization_campaign.ipynb`; calls `application/` directly + `presentation/views/` for rendering. Notebook-specific listener and orchestration live in `presentation/views/notebook_display.py` and `presentation/views/notebook_run.py`.
2. **CLI** — `python -m promptpotter` at `presentation/cli/`. Core path: `init → optimize`. Reads happen by opening `campaigns/{cycle_id}/` artifacts directly; see CLAUDE.md for the mental-model framing.
3. **Claude skill `/potter-run`** — `.claude/skills/potter-run/SKILL.md`. Operator-style entry point that drives the CLI from a chat session; resume-by-default, dataset-aware.
4. **FastAPI** — `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
5. **Next.js webapp** — planned; zero code today.

Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`. Live-phase per-query output is notebook-only today.
