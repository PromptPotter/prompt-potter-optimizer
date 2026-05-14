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

**Render chain:** `Cycle → build_bundle(layer) → DispatchHub.fill_{l1,fixed} → compile_prompt → LLM`. Injection renderers in `INJECTIONS` (`dispatch/hub/injections.py`) are pure `(InjectionBundle) → str`; layer-agnostic. `INJECTIONS` is a typed `dict[str, _Injection]` carrying `name`, `kind`, `render`, and a docstring per entry. The hub has no state. Visual reference + per-placeholder source map: [`dispatch-hub.md`](dispatch-hub.md).

**Invariant:** no prompt site summarizes its own data. If a name isn't in `INJECTIONS`, it doesn't enter a prompt. The registry is code-derived; capabilities can't silently disappear. `validate_template()` (called from `load_optimizer_prompt`) raises at module load on any `{{slot}}` name not in `INJECTIONS` — typos fail loud, not silent.

### Per-layer composition

| Layer | Composition path | What L2 controls |
|-------|------------------|------------------|
| L1 generate | `fill_l1(template, opt_sp.l1_layout, bundle)` — appends injections to slots | the layout (per-slot injection lists) |
| L1 critique | `fill_fixed(template, bundle)` — resolves `{{name}}` placeholders | (none — internal) |
| L2 context | `fill_fixed(template, bundle)` | (none) |
| L3 plan | `fill_fixed(template, bundle)` | (none) |

L1 generate is the only layer with an L2-mutable layout; the rest run on fixed templates whose placeholders are all in `INJECTIONS`. Same hub, same registry, same `InjectionBundle` for every call.

### Field channels between layers

| Field | Writer | Reader(s) | Lifetime |
|-------|--------|-----------|----------|
| `RoundResult.critique` | L1 critique | L2, L3 (`critique` injection via `cycle.latest_round.critique`) | per round (lives on the round audit, not OSP) |
| `OSP.task_context` | L2 (refines via merge) | L1, L1 critique, L2, L3 (`task_context` injection — broadcast) | persistent, accumulative |
| `OSP.l1_layout` | L2 | L1 generate (`fill_l1`) | persistent (in `MEMORY_FIELDS`) |
| `OSP.plan` | L3 | every prompt (`plan` injection in all 4 templates) | persistent — never cleared |
| `OSP.l3_note` | L3 | L2 (`l3_to_l2_note` injection — L2 template only) | persistent until L3 next fires |
| `OSP.l2_guard_breaches` | L2 parser + layout validator | L3 (`l2_guard_breaches` injection) | persistent until L3 fires |
| `OSP.l3_guard_breaches` | L3 parser | L3 next fire (`l3_guard_breaches` injection) | persistent |

**Symmetric broadcast:** L3 writes `plan`; every prompt reads it via the same `_r_plan` renderer. L2 writes `task_context`; every prompt reads it via the same `_r_task_context` renderer. L1 sees both as framing inputs; L2 reads them as the strategic + task context for the next refinement.

### Injection registry — what's in `INJECTIONS`

Layer-agnostic by contract. Every renderer reads off `InjectionBundle` and returns `str` (empty when the source field is empty — empty injections are skipped by the fillers).

| Injection | Reads from `InjectionBundle` | Used by |
|-----------|------------------------------|---------|
| `plan` | `opt_sp.plan` | L1, L1 critique, L2, L3 |
| `task_context` | `opt_sp.task_context` | L1, L1 critique, L2, L3 (broadcast) |
| `rendered_prompt` | `opt_sp.render()` | L1 (parent prompt) |
| `pipeline_param_catalogue` | `pipeline_schema` | L1 (search-space menu) |
| `diagnostics` | STATUS prefix from `cycle_slice` (round / stall / best counters) + `digest.diagnostics` (`RoundDiagnostics`) body | L1, L1 critique, L2, L3 |
| `validation_failures` | `opt_sp.validation_failures` (Wound 1, fenced) | L1, L1 critique, L2, L3 |
| `runtime_failures` | `opt_sp.runtime_failures` (Wound 2, fenced) | L1, L1 critique, L2, L3 |
| `l2_guard_breaches` | `opt_sp.l2_guard_breaches` (Wound 4, plain) | L3 only |
| `l3_guard_breaches` | `opt_sp.l3_guard_breaches` (L3 self-heal, plain) | L3 only |
| `critique` | `digest.critique` | L1, L2, L3 |
| `l3_to_l2_note` | `opt_sp.l3_note` | L2 only |
| `l1_overrides` | `opt_sp.l1_overrides` | L1 (caller extras `n_variants`/`creativity`), L2 |
| `l1_signal_catalogue` | `L1_POSSIBLE` | L2 (menu) |
| `axis_memory` | `cycle.axes.digest()` (DERIVED) | L1, L2, L3 |

L2 owns the L1-only injection subset via `l1_layout`; see [`l1-generate-surface.md`](l1-generate-surface.md). L2-internal injections (`l1_overrides`, `l1_signal_catalogue`) are absent from `L1_POSSIBLE` so L2 cannot inject its own state into L1 as a layout entry — `l1_overrides`'s contents reach L1 only via the `n_variants`/`creativity` caller extras.

---

## 2. Dispatch (which layer fires when)

The runner asks the escalation rules engine after every round. `EscalationState.observe_round` builds a frozen `EscalationInputs` snapshot and delegates to `decide_escalation` (`application/optimization/escalation/decide.py`), which sort-by-priority first-match-wins over `DEFAULT_ESCALATION_RULES`:

```
round runs L1 → EscalationInputs(improved, l1_stall_count, l1_patience, axes_with_positive_yield, …)
                  ↓
        decide_escalation(inputs) → EscalationRule
                  ↓
   {STOP_PERFECT, FIRE_L2 (yield-drought rule | patience-exhausted rule), CONTINUE, STOP_L1_PATIENCE}
```

Default rules in `escalation/rules.py` reproduce the prior FSM exactly (`perfect_accuracy`, `l1_continue`, `l1_stop_no_l2`, `l1_to_l2`); plus opt-in `l2_axis_yield_drought` (priority 60) — fires L2 early when L1 has stalled at least one round AND AxisIndex shows zero axes with effect above the noise floor. Gated by `campaign.json::optimization.escalate_on_yield_drought`.

Counter state lives at `Cycle.escalation` (`l1_stall_count`, `l2_stall_count`, …) — the only mutation surface is observation methods. In-memory during a cycle, persisted to `rounds/round_NNNN.json` after every round, replayed on resume by `resume_with_divergence_check()`. Every transition is checkpointed.

Self-healing fires through a different door: failures route directly to the layer *above* the failing one (validation → L2, runtime → L2, L2-output validators → L3), bypassing the escalation ladder. See [`self-healing-internals.md`](self-healing-internals.md).

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

**Write path:** `score_search_point()` → `build_dataset_run_data()` (`application/datasets.py:347`) → `archive.save(run_id, data)` (`infrastructure/store/measurement_archive.py:97`) → `AxisIndex.refresh()` pulls via `load_since()` (`application/intelligence/indexes.py:721`).

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
| [L1 layout + dispatch hub](l1-generate-surface.md) | `INJECTIONS` registry, `L1Layout`, `DispatchHub` |
| [Dispatch hub visual + index](dispatch-hub.md) | Mermaid flow diagram + per-placeholder source map |
| [Self-healing internals](self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](node-standard.md) | Node JSON declaration format |
| [Stable API v1](stable-api.md) | Fork-readiness surface |
| [Conventions](conventions.md) | Style + code-shape rules |
| [Glossary](../glossary.md) | Domain vocabulary + canonical file pointers |

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/`](../concepts/README.md).
