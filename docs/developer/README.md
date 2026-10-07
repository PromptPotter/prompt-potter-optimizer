<h1 align="center">Developer — how the engine is wired</h1>

<p align="center">
  Implementation notes for architectural seams not obvious from a single file.<br>
  AI can read the code — this folder explains the wiring.
</p>

<p align="center">
  <a href="#1-prompt-structure"><b>1 Prompt structure</b></a> ·
  <a href="#2-dispatch-which-layer-fires-when"><b>2 Dispatch</b></a> ·
  <a href="#3-scoring-node"><b>3 Scoring node</b></a> ·
  <a href="#4-cross-run-memory"><b>4 Cross-run memory</b></a> ·
  <a href="#reading-the-three-layer-loop"><b>Reading order</b></a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/dev-map-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/dev-map-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/dev-map-light.svg" alt="Map of the optimizer: dispatch decides which layer fires; the layer renders its prompt through the dispatch hub; the scoring node runs the resulting target prompt and pipeline params against your backend; measurements land in the cross-run archive and feed the next round." width="880">
  </picture>
</p>

<p align="center"><sub>The numbers are this page's sections.</sub></p>

```bash
pip install -e ".[all,dev]"            # into the repo venv
git config core.hooksPath .githooks    # once per clone: the gate on what you staged
python scripts/gate.py                 # every check CI runs (--py | --web | --only NAME)
```

Use the repo venv's interpreter for everything: a bare `python` imports `promptpotter` but not its dependencies. This page owns the four seams below and is not an index — which doc answers which question is [`../README.md`](../README.md), and what every PR is measured against is [`../architecture.md`](../architecture.md) §0 + §0.5.

Four things every contributor needs to understand:

1. **Prompt structure** — the six-field scheme, its shots, and the dispatch hub that fills it.
2. **Dispatch** — which layer fires next, and where the decision lives.
3. **Scoring node** — the one node that's deterministic, not LLM-driven.
4. **Cross-run memory** — what persists between runs.

---

## 1. Prompt structure

Every optimizer LLM node — `l1_generate`, `l1_critique`, `l2_context`, `l3_plan` — renders an `OptimizerPromptTemplate`; the target prompt the optimizer produces renders a `PromptTemplate`. Both live in `promptpotter/domain/opt_search_point.py`, and **each class's `RENDER_ORDER` is the field order** — they differ on purpose (the optimizer's is cut for the provider prefix cache, the target's is the archive key), so read both there, never from a copy here.

**Shots are part of the target prompt, carried by id and rendered last.** An individual's `shot_ids` name rows of the campaign's demo pool (`DatasetSplit.demo`); `OptSearchPoint.target_fields(framing, demo=)` resolves each to its query and ground truth and appends the block after the fields, so the rendered prompt the archive hashes is one function of the fields, the ids and the pool — two individuals naming the same ids in the same order render and key identically. Every render of a scored prompt is handed the pool; the individual never carries a row's text, and the export carries the resolved block because its reader has no pool. A demo row is never scored and never reaches a round's panel or the bench set. `l1_generate` edits the list through the `shot_ids` slot, bounded by its node's `k_max` and offered only while the `demo_pool` panel shows the menu; `validators/l1_strict.py::L1_SHOTS_IN_DEMO_POOL` rejects an id outside the pool, a repeat, or a list longer than `k_max`. An `algorithm` member edits shots without a model call — CAPO's `optimizers/capo/members.py::FewShot` mutates each individual's list, and `cross_shots` is what a recombining node calls.

**Invariant:** no prompt site summarizes its own data. If a name isn't in `injection_table()`, it doesn't enter a prompt. **The render chain, the per-layer composition paths and the per-placeholder source map are owned by** [`dispatch-hub.md`](dispatch-hub.md) — read them there.

### Field channels between layers

| Field | Writer | Reader(s) | Lifetime |
|-------|--------|-----------|----------|
| `RoundResult.critique` | L1 critique | L1 generate, L2, L3 (`critique` injection via `bundle.digest.critique`) | per round (lives on the round audit, not the memory) |
| `Cycle.framing` | operator, at check-in (frozen for the run) | L1 generate (`task_context` injection — on that floor only) | persistent; never overwritten by any layer |
| `PotterState.memory.l1_layout` | L2 | L1 generate (`fill`); L2 (`l1_layout` injection) | persistent (banked per round as `optimizer_state`) |
| `PotterState.memory.plan` | L3 | L1 generate, L2, L3 (`plan` injection; not on `l1_critique`'s layout) | persistent — never cleared |
| `PotterState.memory.wounds.l3_note` | L3 | L2 (`l3_to_l2_note` injection — L2 template only) | persistent until L3 next fires |
| `PotterState.memory.wounds.l2_guard_breaches` | L2 parser + layout validator | L3 (rendered in the merged `guard_breaches` injection) | persistent until L3 fires |
| `PotterState.memory.wounds.l3_guard_breaches` | L3 parser | L3 next fire (rendered in the merged `guard_breaches` injection) | persistent |

**One renderer per field:** L3 writes `plan`, and every node whose layout places it reads it through the same `_r_plan`. `task_context` is operator-authored framing, frozen for the run and rendered by `_r_task_context`, but no layer writes it. (`L2ContextOutput` explicitly carries neither `task_context` nor `action` — see `dispatch/schemas.py`.) Which node places which panel is `dispatch/layout.py::NODE_LAYOUTS`.

---

## 2. Dispatch (which layer fires when)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-dark.png" alt="The optimizer's own pipeline as the dashboard draws it: adaptive_queue, l1_generate, pobb and l1_score on the round's spine, with l3_plan, l2_context and escalation beneath" width="640">
  </picture>
</p>

The runner asks the escalation rules engine after every round. `EscalationFSM.observe_round` builds a frozen `EscalationInputs` snapshot and delegates to `decide_escalation`, which sort-by-priority first-match-wins over `DEFAULT_ESCALATION_RULES`. All three live in `application/optimizers/potter/escalation/rules.py` — the input vocabulary, the rules and the router are one file, so the policy reads without a hop:

```
round runs L1 → EscalationInputs(current_objective, l1_stall_count, l1_patience, separable, axes_with_positive_yield, …)
                  ↓
        decide_escalation(inputs) → EscalationRule
                  ↓
   {STOP_PERFECT, FIRE_L2 (yield-drought rule | patience-exhausted rule), CONTINUE}
```

**Which rules exist, and which of them preempt patience, is owned by [`dispatch-hub.md`](dispatch-hub.md) § Trigger** — read the membership there and in `escalation/rules.py`, never from a copy on this page.

Counter state lives at `PotterState.escalation` (`l1_stall_count`, `l2_stall_count`, …) — it moves at a closed round and at a fire that LANDED, each a ledger record; an L2 ask (`ask_l2_escalation`) reads a verdict and mutates nothing. In-memory during a cycle and rebuilt on resume by `EscalationFSM.from_ledger` — a fold over the rounds the resume KEEPS (a rewind's discarded rounds stay on the ledger and are cut), not re-derived from one round. Every transition is checkpointed.

Self-healing fires through a different door, bypassing the escalation ladder. **Which layer heals which wound** — owned by [`self-healing-internals.md`](self-healing-internals.md) § The wounds, mapped to the two axes.

---

## 3. Scoring node

The scoring gateway (`application/scoring/search_point_scorer.py`: `open_walk` → `run_walks` → `close_walk`, or `score_search_point()` for one walk) is the only optimizer node that's **not LLM-driven**. It:

- Runs frozen `JobSearchPoint`s (rendered prompt + `pipeline_params`) against the **backend**, not the optimizer LLM.
- Walks the scoring dataset — one loop drives every walk of a phase — calling the backend per sample and applying the scorer formula.
- Handles two-tier caching, deprecated-prior eviction, and PoBB elimination stops mid-walk.
- Returns a `ScoredWalk` per walk — rows, scores, escalation signal, and `stopped`: why it ended before its last cell, which each caller answers for itself.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/backend-contract-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/backend-contract-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/backend-contract-light.svg" alt="Your backend (any pipeline) runs the task. The optimizer reads GET /pipeline, sends {prompt, params} to POST /matches and receives predictions; it generates candidates, scores, critiques and iterates." width="760">
  </picture>
</p>

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
  runs/                                     ┌─────────┴─────────┐
    {run_id}.jsonl   ← one run's log         ▼                   ▼
                                        SampleIdx            AxisIdx
                                       (per sample)    (folds both axes)
```

Both files are append-only logs folded last-wins (`store/read_model.py`). The index keys on `run_id`; a run's log keys on `k` — one `"run"` header row (rewritten whole per save; it is the commit marker) and one `"m:{sample_id}"` row per measurement.

**Every row is FACTS, never a grade.** `append_run` writes each row through `domain/scoring.py::measured_facts`, and neither the header nor the index carries a score, so every read path — replay, the δ ruler, the indexes, the cell reads, a bench pairing — grades rows under the `CellScorer` it names (`rescore_results`). Why a grade is not a fact: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md).

**Write path:** a taken cell (`Walk.take`) → `build_dataset_run_data()` (`application/datasets/loaders.py`) → `archive.append_run(run_id, data, new_measurements)` — the rows already on disk are never rewritten, so a walk of S samples costs O(S) bytes, not O(S²) — → `AxisIndex.refresh()` (`application/intelligence/indexes/axis.py`) pulls via `archive.load_since()`. `compact_run` drops superseded rows at the walk boundary; `reset_run` truncates (a `force_fresh` pass REPLACES its rows, and append-only does not overwrite); `reindex` rebuilds `index.jsonl` from `runs/`.

**Read paths** (both return `list[Measurement]`, ungraded):

- `measurements_for_sample(sample_id)` — *"history of training example X"*. Exposed through `archive_queries.measurements_for_sample()`; **no caller today**, and kept anyway because architecture.md § Measurement archive (the actual database) declares both keys first-class read surfaces of the archive.
- `measurements_for_config(predicate)` — *"runs whose config matches this subset"*. Optional `run_ids` hint keeps the scan O(K + matches).

The archive is tenant-global and **never backend-scoped** — no read or write takes a `backend_id`.

**Schema:** the frozen dataclass `domain/sample.py::Measurement` — read its fields there.

**Extension seams:**

| Change | Files |
|---|---|
| New field on every measurement | `Measurement` (`domain/sample.py`), `build_dataset_run_data()` (`application/datasets/loaders.py`), `_to_measurement()` (`infrastructure/store/measurement_archive.py`) |
| New retrieval query | Method on `MeasurementArchive` parallel to `for_sample/for_config`. Pair with an index class if filtering must stay efficient. |
| New derived index | Class with `_seen_runs` cursor + `ingest_run()` returning its per-run row, applied through ONE `replay_row()` both live and on replay; register on `AxisIndex.refresh()`. Persist via `read_model` (`infrastructure/store/read_model.py`) — never a second mechanism |

**The one rule:** `node_configs` is canonical identity — must be deterministic from pipeline params. Don't break determinism.

**Two hashes, and they are not interchangeable.** `SearchPoint.content_hash(dataset)` is the RUN key — rendered prompt + dataset + merged params — so one prompt scored on N subsets is N runs. `PipelineSchema.sp_hash(params)` is over the schema-resolved node configs alone, so it is the same across subsets; that is why `intelligence/hard_sample_archive.py` keys the ruler's arms on it rather than on the run. It is stored on every index entry, and a caller wanting "the searchpoint" rather than "the run" reads that. **`OptSearchPoint.lineage.id` is neither** — a `uuid4` minted per individual, stable across nothing, and what `LineageNode.id` carries. Joining a tree node to an archive row on it matches nothing; the node serves `sp_hash` beside it for that, stamped on `ScoredCandidate` at the moment it is scored. **Never re-derive it downstream:** the only config a scored candidate carries forward (`resolved_pipeline_params`) is `config_params`, the node configs with each rendered `prompt` stripped, and the hash covers that prompt — so a recompute yields a well-formed id addressing no run, and nothing raises.

---

## Reading the three-layer loop

Order for a contributor who wants to follow L1/L2/L3 end-to-end:

1. [`dispatch-hub.md`](dispatch-hub.md) — signal routing, `injection_table()`, `L1Layout`, slot composition, the mermaid flow.
2. [`dispatch-hub.md`](dispatch-hub.md) § Outputs — what L2 writes, and the layout edits it makes.
3. [`../../promptpotter/application/optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md) — L3 plan + per-layer agent contracts.
4. [`self-healing-internals.md`](self-healing-internals.md) — wound channels, heal-trigger ladder.

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/the-loop.md`](../concepts/the-loop.md).
