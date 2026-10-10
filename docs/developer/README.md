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
python scripts/gate.py --changed       # while editing: only the checks and tests your diff reaches
python scripts/gate.py                 # before handing over: every check CI runs (--py | --web | --only NAME)
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
| `PotterState.memory.steer["l1_generate"]` (`layout` + call settings) | L2 | L1 generate (`fill`); L2 (`l1_layout` injection) | persistent (banked per round as `optimizer_state`) |
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

The runner asks the escalation rules engine after every round. `EscalationFSM.observe_round` builds a frozen `EscalationInputs` snapshot and delegates to `decide_escalation`, which sort-by-priority first-match-wins over `DEFAULT_ESCALATION_RULES`. All three live in `application/optimizers/potter/escalation/rules.py` — the input vocabulary, the rules and the router are one file, so the policy reads without a hop.

**Which rules exist, and which of them preempt patience, is owned by [`dispatch-hub.md`](dispatch-hub.md) § Trigger** — read the membership there and in `escalation/rules.py`, never from a copy on this page.

Counter state is one typed value, `PotterState.escalation.ladder` (`records.py::Ladder`) — it moves at a closed round and at a fire that LANDED; an L2 ask (`ask_l2_escalation`) reads a verdict and mutates nothing. It is banked whole, beside the memory, on the round it stands on (`PotterRoundState`): at the round's close, and restated by a fire that lands after it. A resume or a fork takes up the last standing round's — nothing is folded back. Every transition is checkpointed.

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
(`SampleIndex`, `AxisIndex`) are folded from it by `refresh()`. `SampleIndex`'s per-configuration
derivation is persisted (`measurements/derived/sample_fold__{dataset}.jsonl`) and replayed at start, so a
process re-reads and re-scores only the populations that grew since; the fold is revalidated
against the active formula and each population's signature, and rebuilt whole if the formula moved.

```
ON DISK (the database)                DERIVED (folded from disk)
──────────────────────────            ─────────────────────────────
measurements/                       MeasurementArchive
  index.jsonl        ← configuration × dataset       │
  configs/{config_key}.json                 ┌─────────┴─────────┐
  cells/{config_key}.jsonl ← one line       ▼                   ▼
                             per answer  SampleIdx            AxisIdx
                                       (per sample)    (folds both axes)
```

**A cell is one configuration measuring one sample, and it keeps every answer it was given.** An answer is one appended line, never rewritten, addressed `{file_key}.{id}` and filed under the node-chain prefix that PRODUCED it (`terminal_node`), so every configuration sharing that prefix reads it there. A configuration × dataset's answers are its **population**; each reader declares its take over it, and the default — per sample the most recent answer, a live one over a failed or deprecated one — is `measurement_archive.py::standing`. The archive knows no run, campaign or round: a walk records the answers it took (`ScoredWalk.cells`) and the ledger names them.

**Every row is FACTS, never a grade.** A filed answer is `domain/sample.py::FiledAnswer` — a `MeasuredCell` plus who filed it — and the index carries no score, so every read path — replay, the δ ruler, the indexes, the cell reads, a bench pairing — grades rows under the `Scorer` it names (`Scorer.sheet`). Why a grade is not a fact: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md). Beside its facts an answer carries who filed it: `config_key`, `dataset_name`, `role`, `source`, its own `provenance` grade and `created_at`. **`role` is WHY the scoring pass ran** (`shared/measurement_context.py::MeasurementRole`): `panel` is a candidate's own evidence in the round's shared order and `origin` the campaign's C0, and every other one re-enters outside that order — `backfill` and `parent` a prior or the parent caught up for a paired comparison, `repair` a resume's re-measured hole, `overlap` and `verify` report-only passes that reach no election, floor, lift or acquisition, `bench` the held-out pass no optimizer reading folds. Which roles a reading may see is its `RoleScope`, part of its identity, so readings in two scopes never pair (`SCOPE_ROLES`).

**Write path:** a taken cell (`Walk.take`) → `archive_entry()` (`application/datasets/loaders.py`) names the configuration as a `domain/sample.py::ArchiveEntry` → `MeasurementArchive.file_answers(entry, graded, ...)` appends one line per answer the cell did not hold and returns the addresses. **A replay files nothing** — the walk takes the answer already there. `reindex` rebuilds `index.jsonl` from `cells/` and `configs/`.

**Read paths:**

- `load_population(stores, entry)` / `list_populations(stores, dataset_name=)` — the EVIDENCE read: a configuration's standing answers, memory-scoped for a controlled line.
- `walked_answers(stores, {sample_id: answer})` — the rows one walk took, whatever its cells have been answered since.
- `measurements_for_config(predicate)` — *"answers under configurations matching this subset"*, as `FiledAnswer`s, ungraded.

The archive is tenant-global and **never backend-scoped** — no read or write takes a `backend_id`.

**Schema:** `domain/sample.py::FiledAnswer` (the answer) and `ArchiveEntry` (the index entry) — read their fields there.

**Extension seams:**

| Change | Files |
|---|---|
| New field on every measurement | A measured FACT joins `MeasuredCell` (`domain/scoring.py`); a STAMP beside the facts joins `FiledAnswer` (`domain/sample.py`) |
| New retrieval query | Method on `MeasurementArchive` parallel to `measurements_for_config`. Pair with an index class if filtering must stay efficient. |
| New derived index | Class folding each POPULATION into one row keyed by `config_key` and stamped with its signature, applied through ONE `replay_row()` both live and on replay; register on `AxisIndex.refresh()`. Persist via `archive_queries.write_sample_fold`'s shape — never a second mechanism |

**The one rule:** `node_configs` is canonical identity — must be deterministic from pipeline params. Don't break determinism.

**Two hashes, and they are not interchangeable.** `config_key(node_configs)` is where a configuration's cells are FILED — one per prefix of its node chain. `PipelineSchema.sp_hash(params)` names the searchpoint over the same schema-resolved node configs; it is stored on every index entry as `prompt_fields_id`, and it is what `intelligence/hard_sample_archive.py` keys the ruler's arms on. `JobSearchPoint.content_hash(dataset)` files nothing: it names a campaign's root. **`OptSearchPoint.id` is neither** — the individual's own content (its prompt fields, shots and resolved config), the same wherever and whenever it is reached, and what `ArmNode.id` carries. It leaves out the campaign's framing and the demo pool, so joining a tree node to an archive row on it matches nothing; the node serves `sp_hash` beside it for that, stamped on `ScoredCandidate` at the moment it is scored. **Never re-derive it downstream:** the only config a scored candidate carries forward (`resolved_pipeline_params`) is `config_params`, the node configs with each rendered `prompt` stripped, and the hash covers that prompt — so a recompute yields a well-formed id addressing no cell, and nothing raises.

---

## Reading the three-layer loop

Order for a contributor who wants to follow L1/L2/L3 end-to-end:

1. [`dispatch-hub.md`](dispatch-hub.md) — signal routing, `injection_table()`, `L1Layout`, slot composition, the mermaid flow.
2. [`dispatch-hub.md`](dispatch-hub.md) § Outputs — what L2 writes, and the layout edits it makes.
3. [`../../promptpotter/application/optimizers/potter/CLAUDE.md`](../../promptpotter/application/optimizers/potter/CLAUDE.md) — L3 plan + per-layer agent contracts.
4. [`self-healing-internals.md`](self-healing-internals.md) — wound channels, heal-trigger ladder.

For the conceptual layer (CONTEXT, PLAN, spend control): [`../concepts/the-loop.md`](../concepts/the-loop.md).
