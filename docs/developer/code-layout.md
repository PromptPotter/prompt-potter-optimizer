# Code Layout

Hexagonal layout: `domain/` (pure models) → `application/` (use cases) → `infrastructure/` (I/O adapters) → `presentation/` (entry points), plus leaf `shared/` and `config/`.

```
promptpotter/
├── domain/          # JobSearchPoint, OptSearchPoint, PipelineSchema, scoring formula compile — pure, no I/O
├── application/
│   ├── campaign/    # campaign lifecycle + thin orchestration (Session, RunListener, Decision records)
│   ├── optimization/  # THE CORE LOOP — L1/L2/L3 nodes, critique, llm_call, restructure
│   ├── intelligence/  # SHARED materialized view — AxisIndex, variant_library, scoring_set
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

- **Persistence** (shared, mandatory) — `CampaignPersistenceEmitter` in `infrastructure/persistence/session_emitter.py`. Entry points MUST NOT write campaign artifacts directly. Campaign artifacts split into two bands: **root telemetry** (`dashboard.json`, `output.log`, `phase_events.jsonl`) bind to the family root cycle (the one with no `parent_cycle_id`), so forks share one continuous live stream; **per-cycle audit** (`index.json`, `log.md`, `candidates/`, `trials/`, `rounds/`, `langfuse/`, `prompts/`, `archived/`) lives in each cycle's own dir. The allowlists (`ROOT_TELEMETRY_ARTIFACTS`, `PER_CYCLE_AUDIT_ARTIFACTS`, `CAMPAIGN_ARTIFACTS`, `SESSION_ARTIFACTS`) live in `tests/test_artifact_parity.py` — the test owns the contract.
- **Display** (per-entry-point) — caller passes a `RunListener`. MUST NOT write to disk.
- **Control** (per-entry-point) — `stop_check` callable on `Session` (CLI polls a flag set by Ctrl+C; notebook uses kernel interrupt). MUST NOT write campaign artifacts. The file-based `control.json` mechanism that predated this is gone.

---

## SearchPoint hierarchy

```
SearchPoint (abstract)
├── JobSearchPoint      — target pipeline configuration (frozen)
└── PromptTemplate      — 8-field prompt scheme (render/compile)
        └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

Every service signature follows the same shape: `f(SearchPoint, PipelineSchema, dataset) → scores`. Every state is traced at both layers:

- **Target layer** — `JobSearchPoint` → `library/measurements/` (content-addressed, shared across cycles). See [`measurement-archive-internals.md`](measurement-archive-internals.md).
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
    campaigns/{root_cycle_id}/           # family root (cycle with no parent_cycle_id)
      dashboard.json                     # live counters — telemetry shared across forks
      output.log                         # append-only HIT/MISS history — fork banners on cutover
      phase_events.jsonl                 # structured phase event stream — each record carries cycle_id
      # plus the root cycle's own per-cycle audit:
      index.json                         # campaign metadata + trial index + final summary
      log.md                             # rendered narrative digest (per-round + heatmap + winner)
      trials/trial_NNNN.json             # resume source of truth
      candidates/round_NNNN.json         # pre-scoring checkpoint
      rounds/round_NNN.json              # per-round LLM action audit
      langfuse/                          # trace persistence (incl. events.jsonl mirror)
      prompts/{family}/{version}/        # rendered optimizer prompts
      archived/resumed_at_{ts}/          # mid-cycle rewind history
      forks/                             # all forks of this family nest here
        {root_cycle_id}_fork_xxx/        # one dir per fork — per-cycle audit only
          index.json   log.md            # telemetry stays at the family root above
          trials/   candidates/   rounds/   langfuse/   prompts/
    library/                             # the measurement archive (database core)
      measurements/{run_id}.json         # MeasurementArchive: facts, append-only, content-addressed
      measurements.json                  # archive index (denormalized read-side projection)
      backends/{backend_id}/             # backend profile + datasets
      prompt_aliases.json
```

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `langfuse/events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`. For operator-level walkthrough of these files, see [../operations/persistence-and-state.md](../operations/persistence-and-state.md).

---

## Scoring pipeline

`score_search_point()` in `application/scoring/search_point_scorer.py` is the single gateway for scoring archival and observability. Three early-exit paths live in `application/optimization/nodes/l1/measure.py::score_population` — validation-failure synthetic zero, full-run cache hit, and mid-evaluation escalation — detailed in [self-healing-internals.md](self-healing-internals.md).

Per-node cache reuse happens inside `measure_sample()` in `application/scoring/sample_measurement.py`. `score_search_point` reads infrastructure (store, backend client, backend id, pipeline schema, observer, compiled scorer) directly off the `Session` argument; the previously separate `ScoringEnv` bundle was inlined when callers all converged on `Session`. The compiled scorer lives in `domain/scoring.py` (`compile_scorer`, `SCORING_FUNCTIONS`).

---

## Pipeline parameters

Always nested dicts keyed by node name. `PROMPT_STRING_FIELDS` (in `shared/constants.py`) is the canonical prompt-vs-node-param split. `PipelineSchema` is built entirely from the backend's `GET /pipeline` — zero backend-specific constants in PromptPotter. See [node-standard.md](node-standard.md) for the JSON declaration format.

---

## Entry points (maturity order)

Features land left → right.

1. **Notebook** — `notebooks/optimization_campaign.ipynb`; calls `application/` directly + `presentation/views/` for rendering. Display via the shared `LiveDisplay` (`presentation/views/live.py`); notebook orchestration in `presentation/views/notebook_run.py`.
2. **CLI** — `python -m promptpotter` at `presentation/cli/`. Core path: `init → optimize`. Reads happen by opening `campaigns/{cycle_id}/` artifacts directly; see CLAUDE.md for the mental-model framing.
3. **Claude skill `/potter-run`** — `.claude/skills/potter-run/SKILL.md`. Operator-style entry point that drives the CLI from a chat session; resume-by-default, dataset-aware.
4. **FastAPI** — `promptpotter/main.py` mounts `presentation/api/` — currently read-only.
5. **Next.js webapp** — planned; zero code today.

Post-hoc renderers (campaign summary, flip tracking, lineage, progress, dashboard, status) are shared between CLI and notebook via `presentation/views/`. Live-phase per-query output is notebook-only today.
