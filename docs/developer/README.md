# Developer

Implementation notes for the architectural seams not obvious from a single file. AI can read the code — this folder explains the wiring.

Four things every contributor needs to understand:

1. **Prompt structure** — the 8-field scheme shared by every LLM node.
2. **Dispatch** — which layer fires next, and where that decision lives.
3. **Scoring node** — the one node that's deterministic, not LLM-driven.
4. **Cross-run memory** — what persists between runs.

---

## 1. Prompt structure (shared by every LLM node)

Every optimizer LLM node — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — renders a `PromptTemplate` (`domain/opt_search_point.py:64`). The template has 8 fields:

```
persona → task_intent → problem_description → instruction
→ thinking_style → answer_format → few_shot_examples → plan
```

Each layer wraps the template with a typed *surface* compiled from a closed registry:

| Layer | Surface | Mutator |
|-------|---------|---------|
| L1 generate | `L1GenerateSurface` (8 sections + 4 scalars) | L2 (via OSP overrides) |
| L1 critique | `{{dispatch_msg}}` legacy blob | (none — internal) |
| L2 context | `L2Surface` (includes L1's field catalogue) | L3 |
| L3 plan | multi-hole template (6 holes) | (top of stack) |

Render chain: `archive → AxisIndex → DispatchState → sections → surface → LLM`. Sections are pure formatters `(state) → str`; the surface is the typed payload that lands in `{{variable}}` holes.

**Invariant:** no prompt site summarizes its own data. If a field isn't in the surface registry, it doesn't enter a prompt. L2 owns L1's surface; L3 owns L2's. The catalogue is code-derived, so capabilities can't silently disappear.

Per-field reference tables in [`information-flow.md`](information-flow.md). L1-generate registry: [`l1-generate-surface.md`](l1-generate-surface.md). L2 mechanics: [`l2-internals.md`](l2-internals.md).

---

## 2. Dispatch (which layer fires when)

The runner walks a stall-and-escalate ladder between rounds:

```
round runs L1 → improved?
                  yes: reset l1_stall_count → next round
                  no:  l1_stall_count++;
                       if stall ≥ l1_patience: fire L2 → reset → continue L1
                       (same ladder L2 → L3 with l2_patience)
```

**State lives at `Cycle.escalation`** (`l1_stall_count`, `l2_stall_count`, …). It's in-memory during a cycle, persisted to `trials/trial_NNNN.json` after every round, and replayed on resume by `resume_with_divergence_check()`. So yes — dispatch state is part of the in-memory state subset, but it's never *only* in memory; every transition is checkpointed to disk.

Decision logic: `_check_stop_or_escalate()` (`application/runner.py:439`). Dispatch is not a separate node — it's a check between rounds. Stop conditions live here too (`accuracy ≥ 1.0 → PERFECT`, exhausted patience → `PATIENCE_EXHAUSTED`).

Self-healing fires through a different door: failures route directly to the layer *above* the failing one (validation → L2, runtime → L2, L2-output validators → L3), bypassing the patience ladder. See [`self-healing-internals.md`](self-healing-internals.md).

---

## 3. Scoring node (the one that's different)

`score_search_point()` (`application/scoring/search_point_scorer.py:422`) is the only optimizer node that's **not LLM-driven**. It:

- Runs a frozen `JobSearchPoint` (rendered prompt + `pipeline_params`) against the **backend**, not the optimizer LLM.
- Loops over the scoring dataset, calls the backend per sample, applies the scorer formula.
- Handles two-tier caching, deprecated-prior eviction, and PoBB elimination stops mid-loop.
- Returns `(list[QueryResult], stats, completed, escalation_signal)`.

Why it matters architecturally: it's the **bridge between optimizer and target system**. Everything above it (L1/L2/L3) generates prompts and pipeline params; the scoring node is the only place those land in the real backend and produce a fitness number. The measurement archive is its output stream — every `(sample × config → outcome)` row in `library/.archive/` is born here.

---

## 4. Cross-run memory

`library/.archive/` is the database. `MeasurementArchive` is the only gateway — every read and write goes through it. Three in-memory views (`SampleIndex`, `ConfigIndex`, `AxisIndex`) are rebuilt from disk on every `refresh()` and never persisted.

```
ON DISK (the database)                IN MEMORY (rebuilt from disk)
──────────────────────────            ─────────────────────────────
library/.archive/                            MeasurementArchive
  measurements/                                      │
    {run_id}.json     ← one batch          ┌────────┼────────┐
  measurements_index.json                  ▼        ▼        ▼
  prompt_aliases.json                  SampleIdx  CfgIdx  AxisIdx
                                       (per       (per     (folds
                                        sample)    config)  both)
```

**Write path:**

`score_search_point()` (`application/scoring/search_point_scorer.py:422`) → `build_dataset_run_data()` (`application/datasets/datasets.py:347`) → `archive.save(run_id, data)` (`infrastructure/store/measurement_archive.py:97`) → `AxisIndex.refresh()` pulls via `load_since()` (`application/intelligence/indexes.py:721`).

**Read paths** (both return `list[Measurement]`):

- **`measurements_for_sample(backend_id, sample_id)`** — *"history of training example X"*. Caller: `SampleIndex.measurements()` (`indexes.py:205`).
- **`measurements_for_config(backend_id, predicate)`** — *"runs whose config matches this subset"*. Front-routed through `ConfigIndex.run_ids_matching()` so the scan stays O(unique_configs).

**Schema** (`domain/sample.py:77`, frozen dataclass): `run_id, content_hash, sample_id, query, ground_truth, predicted, hit, score, run_scores, node_configs, pipeline_data, created_at`.

**Extension seams:**

| Change | Files |
|---|---|
| New field on every measurement | `Measurement` (`domain/sample.py`), `build_dataset_run_data()` (`datasets.py:347`), `_to_measurement()` (`measurement_archive.py:418`); bump `MEASUREMENTS_SCHEMA_VERSION` (`config/settings.py:45`) |
| New retrieval view | Method on `MeasurementArchive` parallel to `for_sample/for_config`. If filtering must stay efficient, pair with an index class. |
| New derived index | Class with `_seen_runs` cursor + `ingest_run()`, register on `AxisIndex.refresh()` |

**The one rule:** `node_configs` is canonical identity — must be deterministic from pipeline params. Don't break determinism.

---

## Pages

| Page | What it covers |
|------|----------------|
| [Surface field reference](information-flow.md) | Per-field tables for L1/L2/L3 surfaces |
| [L2 internals](l2-internals.md) | L2 firing, surface, output, OSP mutations |
| [L1-generate surface](l1-generate-surface.md) | `L1GenerateField` registry, override order |
| [Node standard](node-standard.md) | Node JSON declaration format |
| [Self-healing internals](self-healing-internals.md) | Failure classification, escalation wiring |

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/`](../concepts/README.md).
