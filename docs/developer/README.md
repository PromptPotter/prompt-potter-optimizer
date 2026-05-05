# Developer

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

**Render chain:** `Cycle → build_bundle(layer) → DispatchHub.fill_{l1,fixed} → compile_prompt → LLM`. Signal renderers in `SIGNALS` (`dispatch_hub.py`) are pure `(Bundle) → str`; layer-agnostic. The hub has no state.

**Invariant:** no prompt site summarizes its own data. If a name isn't in `SIGNALS`, it doesn't enter a prompt. The registry is code-derived; capabilities can't silently disappear.

### Per-layer composition

| Layer | Composition path | What L2 controls |
|-------|------------------|------------------|
| L1 generate | `fill_l1(template, opt_sp.l1_layout, bundle)` — appends signals to slots | the layout (per-slot signal lists) |
| L1 critique | `fill_fixed(template, bundle)` — resolves `{{name}}` placeholders | (none — internal) |
| L2 context | `fill_fixed(template, bundle)` | (none) |
| L3 plan | `fill_fixed(template, bundle)` | (none) |

L1 is the only layer with an L2-mutable layout; the rest run on fixed templates whose placeholders are all in `SIGNALS`. Same hub, same registry, same `Bundle` for every call.

### Field channels between layers

| OSP field | Writer | Reader(s) | Lifetime |
|-----------|--------|-----------|----------|
| `l1_critique_text` | L1 critique | L1 generate, L2 (via signals on the bundle) | one round (cleared by `clear_volatile`) |
| `l2_brief` | L2 | L1 generate (`l2_directive` signal) | one round (cleared by `clear_volatile`) |
| `l1_layout` | L2 | L1 generate (`fill_l1`) | persistent (in `MEMORY_FIELDS`) |
| `plan` | L3 | L1 generate, L2 (`plan` signal in both templates) | persistent — never cleared |
| `l2_output_failures` | L2 parser + layout validator | L3 (`failures` signal) | persistent until L3 fires |
| `l3_output_failures` | L3 parser | L3 next fire (`failures` signal) | persistent |

**Symmetric plan injection:** L3 writes `plan`; both L1 generate and L2 read it via the same `_r_plan` renderer. L1 sees it as a strategic constraint; L2 as the operating context for its directive. (`l2_brief` flows L2→L1; `plan` flows L3→{L1, L2}.)

### Signal registry — what's in `SIGNALS`

Layer-agnostic by contract. Every renderer reads off `Bundle` and returns `str` (empty when the source field is empty — empty signals are skipped by the fillers).

| Signal | Reads from `Bundle` | Used by |
|--------|---------------------|---------|
| `plan` | `opt_sp.plan` | L1, L2, L3 |
| `l2_directive` | `opt_sp.l2_brief` | L1, L3 (preview), L2 (own prior brief) |
| `rendered_prompt` | `opt_sp.render()` | L1 (parent prompt), L3 (preview) |
| `pipeline_axes` | `pipeline_schema` | L1 (mutation surface) |
| `diagnostics` | `latest_diagnostics` (`RoundDiagnostics`) | L1, L2, L3 |
| `failures` | `opt_sp.{validation,runtime,escalation_log,warning_inventory,l2_output,l3_output}_failures` | L1, L2, L3 |
| `task_context` | `opt_sp.task_context` | L1, L2 |
| `critique` | `latest_critique` | L1, L2, L3 |
| `current_params` | `opt_sp.optimizer_params` | L2 |
| `l1_signal_catalogue` | `L1_POSSIBLE` | L2 (menu) |
| `l1_rendered_prompt` | filled L1 template (recursive into `fill_l1`) | L2, L3 |
| `cycle_position` | `cycle_slice` (round / stall / best counters) | L2, L3 |
| `l2_history` | `cycle_slice` + `opt_sp.optimizer_params` | L3 only |

L2 owns the L1-only signal subset via `l1_layout`; see [`l1-generate-surface.md`](l1-generate-surface.md). L1-internal signals (`current_params`, `l1_signal_catalogue`, `l1_rendered_prompt`, `l2_history`) are absent from `L1_POSSIBLE` so L2 cannot inject its own state into L1.

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

State lives at `Cycle.escalation` (`l1_stall_count`, `l2_stall_count`, …). In-memory during a cycle, persisted to `rounds/round_NNNN.json` after every round, replayed on resume by `resume_with_divergence_check()`. Dispatch state is part of the in-memory state subset, but never *only* in memory — every transition is checkpointed.

Decision logic: `_check_stop_or_escalate()` (`application/runner.py:439`). Stop conditions live here too (`accuracy ≥ 1.0 → PERFECT`, exhausted patience → `PATIENCE_EXHAUSTED`).

Self-healing fires through a different door: failures route directly to the layer *above* the failing one (validation → L2, runtime → L2, L2-output validators → L3), bypassing the patience ladder. See [`self-healing-internals.md`](self-healing-internals.md).

---

## 3. Scoring node

`score_search_point()` (`application/scoring/search_point_scorer.py:422`) is the only optimizer node that's **not LLM-driven**. It:

- Runs a frozen `JobSearchPoint` (rendered prompt + `pipeline_params`) against the **backend**, not the optimizer LLM.
- Loops over the scoring dataset, calls the backend per sample, applies the scorer formula.
- Handles two-tier caching, deprecated-prior eviction, and PoBB elimination stops mid-loop.
- Returns `(list[QueryMeasurement], stats, completed, escalation_signal)`.

It's the **bridge between optimizer and target system**. Everything above it generates prompts and pipeline params; the scoring node is the only place those land in the real backend and produce a fitness number. The measurement archive is its output stream.

---

## 4. Cross-run memory

`archive/` is the database. `MeasurementArchive` is the only gateway. Three in-memory views (`SampleIndex`, `ConfigIndex`, `AxisIndex`) are rebuilt from disk on every `refresh()` and never persisted.

```
ON DISK (the database)                IN MEMORY (rebuilt from disk)
──────────────────────────            ─────────────────────────────
archive/                            MeasurementArchive
  measurements/                                      │
    {run_id}.json     ← one batch          ┌────────┼────────┐
  measurements_index.json                  ▼        ▼        ▼
  prompt_aliases.json                  SampleIdx  CfgIdx  AxisIdx
                                       (per       (per     (folds
                                        sample)    config)  both)
```

**Write path:** `score_search_point()` → `build_dataset_run_data()` (`application/datasets/datasets.py:347`) → `archive.save(run_id, data)` (`infrastructure/store/measurement_archive.py:97`) → `AxisIndex.refresh()` pulls via `load_since()` (`application/intelligence/indexes.py:721`).

**Read paths** (both return `list[Measurement]`):

- `measurements_for_sample(backend_id, sample_id)` — *"history of training example X"*. Caller: `SampleIndex.measurements()` (`indexes.py:205`).
- `measurements_for_config(backend_id, predicate)` — *"runs whose config matches this subset"*. Front-routed through `ConfigIndex.run_ids_matching()` so the scan stays O(unique_configs).

**Schema** (`domain/sample.py:77`, frozen dataclass): `run_id, content_hash, sample_id, query, ground_truth, predicted, hit, score, run_scores, node_configs, pipeline_data, created_at`.

**Extension seams:**

| Change | Files |
|---|---|
| New field on every measurement | `Measurement` (`domain/sample.py`), `build_dataset_run_data()` (`datasets.py:347`), `_to_measurement()` (`measurement_archive.py:418`); bump `MEASUREMENTS_SCHEMA_VERSION` (`config/settings.py:45`) |
| New retrieval view | Method on `MeasurementArchive` parallel to `for_sample/for_config`. Pair with an index class if filtering must stay efficient. |
| New derived index | Class with `_seen_runs` cursor + `ingest_run()`, register on `AxisIndex.refresh()` |

**The one rule:** `node_configs` is canonical identity — must be deterministic from pipeline params. Don't break determinism.

---

## Pages

| Page | Covers |
|------|--------|
| [L2 internals](l2-internals.md) | L2 firing, output, OSP mutations, layout edits |
| [L1 layout + dispatch hub](l1-generate-surface.md) | `SIGNALS` registry, `L1Layout`, `DispatchHub` |
| [Self-healing internals](self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](node-standard.md) | Node JSON declaration format |

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/`](../concepts/README.md).
