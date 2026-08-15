# Developer

```
┌──────────────────────┐                       ┌──────────────────────┐
│  Your Backend        │  GET  /pipeline   ──► │  PromptPotter        │
│  (any pipeline)      │                       │  Optimizer           │
│                      │  POST /matches    ◄── │                      │
│  runs the task       │   {prompt, params}    │  generates candidates│
│                      │                       │  scores + critiques  │
│                      │  → predictions    ──► │  iterates            │
└──────────────────────┘                       └──────────────────────┘
```

Implementation notes for architectural seams not obvious from a single file. AI can read the code — this folder explains the wiring.

Four things every contributor needs to understand:

1. **Prompt structure** — the 8-field scheme + the dispatch hub that fills it.
2. **Dispatch** — which layer fires next, and where the decision lives.
3. **Scoring node** — the one node that's deterministic, not LLM-driven.
4. **Cross-run memory** — what persists between runs.

---

## 1. Prompt structure

Every optimizer LLM node — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — renders a `PromptTemplate` (`promptpotter/domain/opt_search_point.py`). Eight fields, in render order:

```
persona → task_intent → problem_description → instruction
→ thinking_style → answer_format → few_shot_examples → plan
```

**Invariant:** no prompt site summarizes its own data. If a name isn't in `INJECTIONS`, it doesn't enter a prompt. **The render chain, the per-layer composition paths and the per-placeholder source map are owned by** [`dispatch-hub.md`](dispatch-hub.md) — read them there.

### Field channels between layers

| Field | Writer | Reader(s) | Lifetime |
|-------|--------|-----------|----------|
| `RoundResult.critique` | L1 critique | L2, L3 (`critique` injection via `cycle.latest_round.critique`) | per round (lives on the round audit, not OSP) |
| `OSP.memory.task_context` | L2 (refines via merge) | L1, L1 critique, L2, L3 (`task_context` injection — broadcast) | persistent, accumulative; inherits through `mutate()` |
| `OSP.memory.l1_layout` | L2 | L1 generate (`fill`) | persistent (on `L2L3Memory`, copied on adopt) |
| `OSP.plan` | L3 | every prompt (`plan` injection in all 4 templates) | persistent — never cleared |
| `OSP.memory.wounds.l3_note` | L3 | L2 (`l3_to_l2_note` injection — L2 template only) | persistent until L3 next fires |
| `OSP.memory.wounds.l2_guard_breaches` | L2 parser + layout validator | L3 (rendered in the merged `guard_breaches` injection) | persistent until L3 fires |
| `OSP.memory.wounds.l3_guard_breaches` | L3 parser | L3 next fire (rendered in the merged `guard_breaches` injection) | persistent |

**Symmetric broadcast:** L3 writes `plan`; every prompt reads it via the same `_r_plan` renderer. L2 writes `task_context`; every prompt reads it via the same `_r_task_context` renderer. L1 sees both as framing inputs; L2 reads them as the strategic + task context for the next refinement.

---

## 2. Dispatch (which layer fires when)

The runner asks the escalation rules engine after every round. `EscalationFSM.observe_round` builds a frozen `EscalationInputs` snapshot and delegates to `decide_escalation` (`application/optimization/escalation/decide.py`), which sort-by-priority first-match-wins over `DEFAULT_ESCALATION_RULES`:

```
round runs L1 → EscalationInputs(improved, l1_stall_count, l1_patience, axes_with_positive_yield, …)
                  ↓
        decide_escalation(inputs) → EscalationRule
                  ↓
   {STOP_PERFECT, FIRE_L2 (yield-drought rule | patience-exhausted rule), CONTINUE}
```

**Which rules exist, and which of them preempt patience, is owned by [`dispatch-hub.md`](dispatch-hub.md) § Trigger** — read the membership there and in `escalation/rules.py`, never from a copy on this page.

Counter state lives at `Cycle.escalation` (`l1_stall_count`, `l2_stall_count`, …) — the only mutation surface is observation methods. In-memory during a cycle, persisted to `rounds/round_NNNN.json` after every round, replayed on resume by `resume_with_divergence_check()`. Every transition is checkpointed.

Self-healing fires through a different door: failures route directly to the layer *above* the failing one (validation → L2, runtime → L2, L2-output validators → L3), bypassing the escalation ladder. See [`self-healing-internals.md`](self-healing-internals.md).

---

## 3. Scoring node

`score_search_point()` (`application/scoring/search_point_scorer.py`) is the only optimizer node that's **not LLM-driven**. It:

- Runs a frozen `JobSearchPoint` (rendered prompt + `pipeline_params`) against the **backend**, not the optimizer LLM.
- Loops over the scoring dataset, calls the backend per sample, applies the scorer formula.
- Handles two-tier caching, deprecated-prior eviction, and PoBB elimination stops mid-loop.
- Returns `(list[QueryMeasurement], stats, completed, escalation_signal)`.

It's the **bridge between optimizer and target system**. Everything above it generates prompts and pipeline params; the scoring node is the only place those land in the real backend and produce a fitness number. The measurement archive is its output stream.

---

## 4. Cross-run memory

`measurements/` is the database. `MeasurementArchive` is the only gateway. Two derived views
(`SampleIndex`, `AxisIndex`) are folded from it by `refresh()`. `SampleIndex`'s per-run
derivation is persisted (`measurements/derived/sample_fold__{dataset}.jsonl`) and replayed at start, so a
process re-reads and re-scores only runs it has not folded before; the fold is revalidated
against the active formula and each detail's signature, and rebuilt whole if either moved.

```
ON DISK (the database)                DERIVED (folded from disk)
──────────────────────────            ─────────────────────────────
measurements/                       MeasurementArchive
  index.jsonl        ← append-only                   │
  runs/                                     ┌────────┼────────┐
    {run_id}.jsonl   ← one run's log         ▼        ▼        ▼
                                        SampleIdx  CfgIdx  AxisIdx
                                       (per       (per     (folds
                                        sample)    config)  both)
```

Both files are append-only logs folded last-wins (`store/read_model.py`). The index keys on `content_hash`; a run's log keys on `k` — one `"run"` header row (rewritten whole per save; it is the commit marker) and one `"m:{sample_id}"` row per measurement.

**Write path:** `score_search_point()` → `build_dataset_run_data()` (`application/datasets/loaders.py`) → `archive.append_run(run_id, data, new_measurements)` — the rows already on disk are never rewritten, so a walk of S samples costs O(S) bytes, not O(S²) — → `AxisIndex.refresh()` (`application/intelligence/indexes/axis.py`) pulls via `archive.load_since()`. `compact_run` drops superseded rows at the walk boundary; `reset_run` truncates (a `force_fresh` pass REPLACES its rows, and append-only does not overwrite); `reindex` rebuilds `index.jsonl` from `runs/`.

**Read paths** (both return `list[Measurement]`):

- `measurements_for_sample(sample_id)` — *"history of training example X"*. Exposed through `archive_views.measurements_for_sample()`; **no caller today**, and kept anyway because architecture.md §0 declares both keys first-class read surfaces of the archive.
- `measurements_for_config(predicate)` — *"runs whose config matches this subset"*. Optional `run_ids` hint keeps the scan O(K + matches).

The archive is tenant-global and **never backend-scoped** — no read or write takes a `backend_id`.

**Schema:** the frozen dataclass `domain/sample.py::Measurement` — read its fields there.

**Extension seams:**

| Change | Files |
|---|---|
| New field on every measurement | `Measurement` (`domain/sample.py`), `build_dataset_run_data()` (`application/datasets/loaders.py`), `_to_measurement()` (`infrastructure/store/measurement_archive.py`) |
| New retrieval view | Method on `MeasurementArchive` parallel to `for_sample/for_config`. Pair with an index class if filtering must stay efficient. |
| New derived index | Class with `_seen_runs` cursor + `ingest_run()` returning its per-run row, applied through ONE `replay_row()` both live and on replay; register on `AxisIndex.refresh()`. Persist via `read_model` (`infrastructure/store/read_model.py`) — never a second mechanism |

**The one rule:** `node_configs` is canonical identity — must be deterministic from pipeline params. Don't break determinism.

---

## Reading the three-layer loop

Order for a contributor who wants to follow L1/L2/L3 end-to-end:

1. [`dispatch-hub.md`](dispatch-hub.md) — signal routing, `INJECTIONS`, `L1Layout`, slot composition, the mermaid flow.
2. [`dispatch-hub.md`](dispatch-hub.md) § Outputs — what L2 writes, and the layout edits it makes.
3. [`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md) — L3 plan + per-layer agent contracts.
4. [`self-healing-internals.md`](self-healing-internals.md) — wound channels, heal-trigger ladder.

---

## Pages

| Page | Covers |
|------|--------|
| [Adding a surface](adding-a-surface.md) | Golden-path recipes per expansion point (record/injection/view-field/decision-kind/connector/node) + the guard that catches each half-wiring |
| [Dispatch hub + L1 layout](dispatch-hub.md) | `INJECTIONS` registry, `L1Layout`, `DispatchHub`, mermaid flow + per-placeholder source map |
| [Self-healing internals](self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard + `pipeline.yaml` contract](node-standard.md) | The node model, the JSON declaration format, and the strict field-level wire shape |
| [Stable API v1](stable-api.md) | Fork-readiness surface |
| [Whitelabel](whitelabel.md) **(draft)** | Running the unit under another name — the four rename tiers, what each breaks, what must never be renamed. Parked: wired and gate-green, never walked end to end |
| [DSPy optimizer](dspy-optimizer.md) **(draft)** | Driving the loop from inside someone else's DSPy program via the separate `promptpotteropt` package — what that trades away, the `compile()` swap, `Loop` / `Node`, and why it asks for a dataset name. Packaging boundary: [`ADR-0006`](../adr/0006-embeddable-core-and-extras.md) |
| [Run initialization](run-initialization.md) | The INIT phase: the four-step chain `init_services` → `populate_session_scoring` → `init_cycle` → `init_optimization_loop` with pre/postconditions and an ASCII diagram |
| [Concept map](concept-map.md) | "Where does concept X live" table |
| [Event stream](event-stream.md) | SSE Profile-A contract |
| [L1 candidate analysis checklist](l1-candidate-analysis-checklist.md) | Round-trace review checklist + the self-optimizing campaign parallel-use lookup |
| [Local repro harnesses](cycle-fixtures.md) | Freezing a buggy cycle as a test fixture, and the Dex harness for the auth-on dashboard |
| [Conventions](conventions.md) | Style + code-shape rules + the six situational reasoning doctrines (one-budget / simplify-the-problem / surface-ledger / entry-point-parity / read-once / wall-clock) |

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/the-loop.md`](../concepts/the-loop.md).
