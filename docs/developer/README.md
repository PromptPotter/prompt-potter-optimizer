# Developer

Implementation notes for architectural seams not obvious from a single file. AI can read the code — this folder explains the wiring.

Four things every contributor needs to understand:

1. **Prompt structure** — the 8-field scheme + per-layer surface registries.
2. **Dispatch** — which layer fires next, and where the decision lives.
3. **Scoring node** — the one node that's deterministic, not LLM-driven.
4. **Cross-run memory** — what persists between runs.

---

## 1. Prompt structure

Every optimizer LLM node — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — renders a `PromptTemplate` (`domain/opt_search_point.py:64`). Eight fields:

```
persona → task_intent → problem_description → instruction
→ thinking_style → answer_format → few_shot_examples → plan
```

**Render chain:** `archive → AxisIndex → DispatchState → sections → surface → LLM`. Sections are pure formatters `(state) → str`; the surface is the typed payload that lands in `{{variable}}` holes.

**Invariant:** no prompt site summarizes its own data. If a field isn't in the surface registry, it doesn't enter a prompt. L2 owns L1's surface; L3 owns L2's. The catalogue is code-derived, so capabilities can't silently disappear.

### Per-layer surfaces

| Layer | Surface | Mutator |
|-------|---------|---------|
| L1 generate | `L1GenerateSurface` (8 sections + 4 scalars) | L2 (via OSP overrides) |
| L1 critique | `{{dispatch_msg}}` blob built by `compile_l1_critique_blob` | (none — internal) |
| L2 context | `L2Surface` (incl. L1's field catalogue) | L3 |
| L3 plan | multi-hole template (6 holes) | (top of stack) |

Every optimizer LLM call follows the same path: `DispatchState (per-call) → LAYER_CONFIGS[layer] ({var: section_renderer}) → compile_prompt_vars (applies OSP overrides, merges per-call extras) → run_optimizer_node (renders, calls LLM)`.

### Field channels between layers

| Field | Writer | Reader(s) | Lifetime |
|-------|--------|-----------|----------|
| `dispatch_msg` | `compile_l1_critique_blob` | L1-critique | per-call, not persisted |
| `l1_critique_text` (+ critique fields) | L1-critique | L1-generate, L2 | one round (cleared by `clear_volatile`) |
| `l2_brief` | L2 | L1-generate | one round (cleared by `clear_volatile`) |
| `l1_section_overrides` / `_text` | L2 | L1-generate `read_overrides` | persistent (memory) |
| `plan` | L3 | L1-generate, L2 | persistent until next L3 (never cleared) |

**Symmetric plan injection:** L3 writes `plan` to `OptSearchPoint`; both L1-generate **and** L2 read it. L1 receives it as a strategic constraint; L2 as the operating context for its brief. (`l2_brief` flows L2→L1; `plan` flows L3→{L1, L2}.)

### L1 / L2 surface field reference

Retention legend: `memory` (checkpointed with the candidate), `opt_sp` (on the optimizer state, checkpointed), `transient` (computed per-round), `config` (immutable within a cycle), `axes` (cross-campaign).

| Field | L1 | L2 | Retention | Description |
|-------|----|----|-----------|-------------|
| `pipeline_schema_text` | ✓ | — | config | Pipeline node/param catalogue. |
| `failure_analysis` | ✓ | — | transient | Top-3 clustered failure patterns. |
| `axes_l1` | ✓ | — | axes | Cross-campaign digest: clusters, dead queries, top axes / values. |
| `task_context` | ✓ | — | opt_sp | Structured domain context (read-only from L1; L2 edits). |
| `escalation_probe` | ✓ | — | memory | Probe-round per-query warning dump. |
| `escalation_alert` | ✓ | — | memory | Aggregated escalation alert; suppressed by an active `l2_brief`. |
| `l2_brief` | BRIEF: | PREVIOUS BRIEF: | memory | One-round window; cleared on improvement. The only guidance channel into L1 generate. |
| `plan` | ✓ | ✓ | opt_sp | L3's strategic plan; symmetric read. |
| `escalation_section` | — | ✓ | transient | Aggregated pipeline stability report. |
| `warning_inventory` | — | ✓ | memory | Per-query warning breakdown; L2 fallback. |
| `validation_failures` | — | ✓ | transient | L1 parse-time invariant violations — Loop 1 input. |
| `runtime_failures` | — | ✓ | memory | Mid-eval degradation records — Loop 2 input. |
| `l2_output_failures` | — | (L3) | memory | Validator outcomes on L2's output — Loop 4 input. |
| `axes_l2` | — | ✓ | axes | Cross-campaign strategic digest. |

L2 also receives an `l1_generate_field_catalogue` hole — a code-derived menu of every L1-generate registry entry with current visibility / override state. See [`l1-generate-surface.md`](l1-generate-surface.md).

L2 writes back a flat dict — any subset of `brief`, `optimizer_params`, `task_context`, `scheme_overrides`, `text_overrides`, `template_override`, `action`. Each field lands directly on the corresponding `OptSearchPoint` field. See [`l2-internals.md`](l2-internals.md).

### L1 critique blob composites

The critique phase runs inside L1 after scoring. Four composite sections in order, joined with `\n\n`:

| Section | Inner blocks |
|---------|--------------|
| `ROUND_REPORT` | scoring summary, anomaly flags, pipeline health, candidate rank analysis, round evolution, this-round trajectory + diff |
| `PER_QUERY_REPORT` | runtime failures, query categories, failure details, successes |
| `HISTORICAL_CONTEXT` | `AxisIndex`: discriminating queries, failure clusters, tractability, exhausted axes, value trends |
| `AVAILABLE_SCHEMA_MUTATIONS` | Pipeline nodes with mutable output schemas |

Assembled by `compile_l1_critique_blob(state)` and passed as the `dispatch_msg` extra. Output flows into L2 refine, which compresses it into a brief that L1 generate reads.

### L3 multi-hole template

L3 fires when L2 stalls. Six explicit holes:

| Hole | Source |
|------|--------|
| `{{current_plan}}` | Current strategic plan |
| `{{l2_summary}}` | Last 3 L2 rounds — what changed, whether accuracy moved |
| `{{rendered_prompt}}` | Current prompt rendered as a single string |
| `{{pipeline_section}}` | Current pipeline parameters |
| `{{runtime_failures_section}}` | Runtime failures across rounds |
| `{{axes_digest}}` | `AxisIndex.digest_for_l3()`: rankings, bottlenecks, clusters, persistent failures |

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
| [L2 internals](l2-internals.md) | L2 firing, surface, output, OSP mutations |
| [L1-generate surface](l1-generate-surface.md) | `L1GenerateField` registry, override order |
| [Self-healing internals](self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](node-standard.md) | Node JSON declaration format |

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/`](../concepts/README.md).
