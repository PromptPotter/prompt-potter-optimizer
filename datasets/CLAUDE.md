# datasets/ — per-dataset configuration

Each subdirectory is a **first-class dataset definition**: the campaign config, pipeline overlay, optimizer prompts, and human-facing description for one optimization target. Configs are the **source of truth** — no parallel default ladders elsewhere in code.

> Architecture entry point: [`../docs/architecture.md`](../docs/architecture.md) §0 + §0.5.
> Per-dataset prompt store + overlay merge contract: [`../promptpotter/application/CLAUDE.md#backend-overlay`](../promptpotter/application/CLAUDE.md).

## Canonical layout

```
datasets/{name}/
├── campaign.yaml          # CampaignConfig: optimizer LLM, scoring, max_rounds, etc.
├── pipeline.yaml          # Backend tunable overlay (nodes.{name}.config)
├── task_description.md    # L1's framing input — what the task IS
├── task_context.yaml      # …decomposed into the framing fields (optional; see below)
├── dataset.md             # Human-facing description: source, split, sample shape
├── prompts/{node}.yaml    # Per-node PromptTemplate overrides (optional)
└── cache.json             # The dataset ITEM BANK (write-managed; don't hand-edit)
```

## A ceiling is DECLARED, or the clock that needs it reports nothing

`campaign_config.accuracy_ceiling` is the accuracy a best-reachable prompt would score on this dataset at this campaign's model, and only the dataset owner can state it — it is a joint claim about the two, so it is not derivable from the bank, the schema or a literature number. Declaring one turns on `index.json::final.rounds_to_ceiling`; leaving it unset (the default on every dataset here) leaves that clock unset too, which is the honest reading and costs nothing else. **Declare it only where you can say what measured it** — a saturation screen on the same model, a published ceiling for the same split. A guessed value makes every campaign on the dataset publish a round count nobody can defend, and no run says so.

## Sole route for backend tunable changes

**Backend overlay (`nodes.{name}.config` in `pipeline.yaml`) is the only way to switch model, provider, temperature, or anything in a node's `optimizer.param_keys`.** Never edit the backend repo (including the co-owned TermNorm backend) to achieve a tunable switch. Pipeline-agnostic is a §0 commitment.

`load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`promptpotter/application/pipeline_resolve.py`) merges the overlay onto each wire payload. **The dataset owns its task model** in `nodes.{node}.config.model` — every LLM node must declare one, or `configure_and_apply_pipeline` raises a loud setup error (no silent fall-through to the backend's own default).

**`route_order` is the third key on that overlay, and only two of the three are locked.** `model` names WHAT answers, `provider` the GATEWAY it is asked through, and `route_order: [<host>, …]` which of that gateway's upstream HOSTS to try, in order (`nodes.{name}.config.route_order`; `current_config` carries it to `llm_call` untouched). `provider` and `route_order` sit in `PARAM_FORBIDDEN_KEYS`, so `node_param_keys()` strips them and L1 can never emit one: they are **operator cost levers set against a measured capture, never search axes**. **`model` is a real axis** — a dataset opens it by listing it in `optimizer.param_keys`, and bounds it with `param_allowed_values.model` (absent ⇒ the whole `available_models` menu); a dataset whose model is a measurement premise simply does not list it. Names are the gateway's own `provider_name` — read them off `served_by` in the ledger, never from a catalogue. Why an order pays at all, and the measured numbers: [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § LLM client.

## Registered datasets

The roster is the directory listing; each dataset's connector is read off its own `pipeline.yaml::nodes` — don't mirror either here. The special cases worth knowing:

- **`lca-termnorm`** (`termnorm`) — the multi-node retrieval pipeline. Every other benchmark declares a single `llm_only` **node**: all of them are `backend_type: "termnorm"` and route over HTTP to the server exactly as `lca-termnorm` does. `llm_only` is a node name only, never a connector.
- **`aime_2025`** — its overlay routes to OpenRouter+Mistral, off the Groq default.
- **`email-tagging`** — the built-in try-and-learn demo. `User.demo_mode_enabled` is a stored preference with **no reader**: nothing surfaces this dataset from it yet ([`../docs/specs/roadmap.md`](../docs/specs/roadmap.md) lane A1).
- **`justlogic-d234`** — the L4 inner benchmark, an iid mix of depths 2-4 ([§ L4 below](#l4--promptpotter-self)); **`promptpotter-self`** (`promptpotter` connector) — the L4 dataset, and the only one to read a result off, because **`promptpotter-self-e2e`** beside it is a degenerate one-cell twin the browser walk runs for cents. A fixture, never a second instrument (`promptpotter-self-e2e/dataset.md`).
- **The optimizer's own prompt homes are not in this directory.** They are package install content, shipped in the wheel: `promptpotter/assets/optimizer/pipeline.yaml` + `sets/*.yaml`. Still **operator-owned files** — nothing writes them. `application/evidence/read.py` ranks the measured edits; graduating a winner into `assets/optimizer/pipeline.yaml` is a deliberate hand-edit, and an installed operator shadows that one file via `config/paths.py::optimizer_pipeline_path`.

## Re-cutting a dataset needs a NEW name

**A measurement replays by CONTENT; per-sample history is kept by POSITION.** Replay matches a cell on its `sample_key` — the sample's query, label, question and connector-resolved `source_pin` — under the same instrument configuration, in every dataset at once: a sample carried into a wider panel or under a new name replays every cell already measured on it (`infrastructure/store/measurement_archive.py::load_reusable_results`). What a `sample_id` still keys is the history — δ, hit rates, hard samples — scoped `(dataset_name, sample_id)`. So a change that puts different content at an existing position (editing, replacing or reordering rows) needs a new `datasets/{name}/`, while one that leaves every existing sample in its slot, like appending tasks to a panel, does not. Re-running a measured configuration on a re-cut name refuses with `DatasetIdentityError` rather than pooling two questions in one slot (`application/scoring/search_point_scorer.py::_replayable_on`).

## L4 — `promptpotter-self`

`datasets/promptpotter-self/` is the **recursive case**: the outer cycle mutates the inner cycle's optimizer prompt template fields, exposed via `pipeline.yaml::nodes.{node}.optimizer.param_keys` — every node the file DECLARES (`PipelineSchema.config_nodes`), never only the ones a round runs, or an escalation node reached on a stall could never be told to improve. Its `pipelines` block mirrors the optimizer manifest's, so both describe ONE graph and an edit evolved on either layer lifts onto the other.

L4 is **not** a 4th `LayerStrategy` — it is the same PromptPotter applied to itself via the `promptpotter` connector, a recursion, not a new layer driver (full statement: [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md)).

**The inner instrument is `justlogic-d234`, and a cut switch is never advice.** Each depth cut is a separate `dataset_name` with its own δ scale, so comparing "bands" across cuts reads a difference of rulers as a capability difference. A new cut is a new directory and nothing else — `justlogic_depths` reads the depths off the name — so widening difficulty means adding `justlogic-dNNN/`, never re-cutting this one.

The remaining work lives in ONE place — [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md) § Open (don't restate it here; it re-goes-stale every slice).

## Reference points — consult on every dataset question

- **Adding a dataset, canonical splits, and why X is or isn't wired** → [`../docs/operations/dataset-selection-rationale.md`](../docs/operations/dataset-selection-rationale.md) § Adding a dataset. Research the canonical split; never invent one; check here first for "why didn't we use Y?" and "have we trialed Z?".
- **Per-dataset model + `reasoning_effort` + `max_tokens`, BBEH output-ceiling traps, Groq daily-volume swap protocol** → [`../docs/operations/dataset-reasoning-matrix.md`](../docs/operations/dataset-reasoning-matrix.md). This — not self-optimizing campaign NOTES.md — is the canonical source for model recommendations.

## `cache.json` is the item bank, not a score cache

**Don't hand-edit it, and don't reason about origin cost from it.** It holds
`{name, created_at, source_file, row_count, items}`, read into `session.samples` at wiring.
A file *here* is the SHIPPED bank (only `email-tagging` has one); a fetched one is the
operator's, written to `.promptpotter/{tenant}/benchmark-rows/{name}.json` by
`resolve_dataset_items` → `TenantDatasetStore.save_benchmark_rows`, since this tier is
read-only under a wheel. Both resolve through `readable_dataset_rows`. It is **not** an
origin score cache: measurements
live in the tenant-global content-addressed `measurements/` archive
(`infrastructure/store/archive_queries.py`), which is what replays origin rows across
cycles, forks and resumes. `sp_budget_origin` breadth is cheap *because* of that archive,
never because of this file.

## Conventions

- **Origin = conservative floor** — owned by [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md) § Origin = conservative floor. Every tunable in this directory's overlay starts at its floor.
- **`task_description.md` is L1's framing input** — written for the LLM that will generate candidates, not for human readers (though it should be readable).
- **`task_context.yaml` is that description DECOMPOSED, and it is optional here.** An ingested dataset gets one at commit from its check-in; a benchmark may ship one. When it is absent, the first run decomposes `task_description.md` once — and writes the result to `.promptpotter/{tenant}/task-context/{name}.yaml`, **not** back into this directory, which is read-only under a wheel. Same definition-vs-derived split as `cache.json` above, and for the same reason. Resolved tenant-then-install by `readable_task_context`; hand-editing either tier's copy is supported (the run reads whichever wins).
- **`dataset.md` is operator-facing** — describes source, split, sample shape; cite the canonical evaluation protocol.
