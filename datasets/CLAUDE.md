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

Optional:

- `sweep/` — sweep-mode sibling cycle outputs.

## Sole route for backend tunable changes

**Backend overlay (`nodes.{name}.config` in `pipeline.yaml`) is the only way to switch model, provider, temperature, or anything in a node's `optimizer.param_keys`.** Never edit the backend repo (including the co-owned TermNorm backend) to achieve a tunable switch. Pipeline-agnostic is a §0 commitment.

`load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`promptpotter/application/pipeline_resolve.py`) merges the overlay onto each wire payload. **The dataset owns its task model** in `nodes.{node}.config.model` — every LLM node must declare one, or `configure_and_apply_pipeline` raises a loud setup error (no silent fall-through to the backend's own default).

## Registered datasets

The roster is the directory listing; each dataset's connector is read off its own `pipeline.yaml::nodes` — don't mirror either here. The special cases worth knowing:

- **`lca-termnorm`** (`termnorm`) — the multi-node retrieval pipeline. Every other benchmark declares a single `llm_only` **node**: all of them are `backend_type: "termnorm"` and route over HTTP to the server exactly as `lca-termnorm` does. `llm_only` is a node name only, never a connector.
- **`aime_2025`** — its overlay routes to OpenRouter+Mistral, off the Groq default.
- **`email-tagging`** — the built-in try-and-learn demo, surfaced while `User.demo_mode_enabled`.
- **`justlogic-d234`** — the L4 inner benchmark, an iid mix of depths 2-4 ([§ L4 below](#l4--promptpotter-self)); **`promptpotter-self`** (`promptpotter` connector) — the one L4 dataset.
- **The optimizer's own prompt homes are not in this directory.** They are package install content, shipped in the wheel: `promptpotter/assets/optimizer/pipeline.yaml` + `sets/*.yaml`. Still **operator-owned files** — nothing writes them. `optimizer_prompt_ranking.py` ranks optimizer-prompt states; graduating a winner into `assets/optimizer/pipeline.yaml` is a deliberate hand-edit, and an installed operator shadows that one file via `config/paths.py::optimizer_pipeline_path`.

## Re-cutting a dataset needs a NEW name

**A `sample_id` identifies a sample only *within* a `dataset_name` — the row's text is not in the key.** So changing which rows a dataset holds, or what a row says, while keeping the name serves the OLD measurement for the new sample, silently and with no error anywhere. Cut the new version under a new `datasets/{name}/` and leave the old directory in place for as long as anything is still keyed to it. Key + the requirement that scopes it: `infrastructure/store/archive_views.py::reusable_results`.

## L4 — `promptpotter-self`

`datasets/promptpotter-self/` is the **recursive case**: the outer cycle's mutation surface is the inner cycle's optimizer prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`), exposed via `pipeline.yaml::nodes.{node}.optimizer.param_keys`.

L4 is **not** a 4th `LayerStrategy` — it is the same PromptPotter applied to itself via the `promptpotter` connector, a recursion, not a new layer driver (full statement: [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md)).

**The inner instrument is `justlogic-d234`, and a cut switch is never advice.** Each depth cut is a separate `dataset_name`, so a measurement taken on one cut shares no cache key with another's — comparing "bands" across cuts reads a keying difference as a capability difference. A new cut is a new directory and nothing else — `justlogic_depths` reads the depths off the name — so widening difficulty means adding `justlogic-dNNN/`, never re-cutting this one.

The remaining work lives in ONE place — [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md) § Open (don't restate it here; it re-goes-stale every slice).

## Reference points — consult on every dataset question

Source of truth for wire / reject rationale, projection-bias findings, per-dataset model defaults:

- **Adding a dataset + canonical splits** → [`../docs/operations/dataset-selection-rationale.md`](../docs/operations/dataset-selection-rationale.md) § Adding a dataset — the wiring process. Research the canonical split; never invent one.
- **Why X is / isn't wired, trialed-and-rejected list** → [`../docs/operations/dataset-selection-rationale.md`](../docs/operations/dataset-selection-rationale.md). Check first when asked "why didn't we use Y?" or "have we trialed Z?".
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
(`infrastructure/store/archive_views.py`), which is what replays origin rows across
cycles, forks and resumes. `sp_budget_origin` breadth is cheap *because* of that archive,
never because of this file.

## Conventions

- **Origin = conservative floor** — owned by [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md) § Origin = conservative floor. Every tunable in this directory's overlay starts at its floor.
- **`task_description.md` is L1's framing input** — written for the LLM that will generate candidates, not for human readers (though it should be readable).
- **`task_context.yaml` is that description DECOMPOSED, and it is optional here.** An ingested dataset gets one at commit from its check-in; a benchmark may ship one. When it is absent, the first run decomposes `task_description.md` once — and writes the result to `.promptpotter/{tenant}/task-context/{name}.yaml`, **not** back into this directory, which is read-only under a wheel. Same definition-vs-derived split as `cache.json` above, and for the same reason. Resolved tenant-then-install by `readable_task_context`; hand-editing either tier's copy is supported (the run reads whichever wins).
- **`dataset.md` is operator-facing** — describes source, split, sample shape; cite the canonical evaluation protocol.
