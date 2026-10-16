# assets/datasets/ — the shipped template definitions

Each directory holding a `dataset.yaml` is a **first-class dataset definition**, named by its path below
this one (`justlogic/d234`, `promptpotter-self/panel`). It ships in the wheel and is read-only there.
**This directory is a template set, not a benchmark library**: every other definition, an operator's
backends and the paper's benchmarks alike, is a tenant definition under `.promptpotter/`, resolved
first. Configs are the **source of truth** — no parallel default ladders elsewhere in code.

> Architecture entry point: [`docs/architecture.md`](../../../docs/architecture.md) §0 + §0.5.
> Per-dataset prompt store + overlay merge contract: [`application/CLAUDE.md`](../../application/CLAUDE.md) § Backend overlay.

## Canonical layout

```
assets/datasets/
├── defaults.yaml          # {pipeline:, campaign:} — what every definition below is laid over
├── {family}/family.yaml   # A family's shared sections; the family itself is no dataset
└── {name}/                # nested any depth; a plain directory between is only a path segment
    ├── dataset.yaml       # source: / pipeline: / campaign: / panel: — only what differs from above
    ├── task.md            # L1's framing input: front matter = committed framing, body = description
    ├── origin.yaml        # the starting prompts, per node (optional)
    ├── README.md          # operator-facing: source, split, sample shape, model screens
    └── rows.json          # a hand-authored item bank, where one ships (optional; see below)
```

A dataset may also be a family: a directory with `dataset.yaml` and children is both. A leaf with no
values of its own carries a one-line `dataset.yaml` as its marker.

## A dataset file is a partial

**Every reader sees the definition chain laid together** (`infrastructure/store/dataset_access.py::definition_chain`):
`defaults.yaml`, then each `family.yaml` / `dataset.yaml` from the root down, then the leaf. Each section
merges by the kind it is: `campaign` depth-first (`campaign_config.py::merge_config_layers`); `pipeline`
by top-level replacement with nodes through `pipeline_parsing.py::merge_node_blocks` — a default node is a
TEMPLATE, so only a node the dataset itself names receives it; `source` and `panel` key by key, a panel's local paths resolving against the deepest layer declaring a panel key.
`task.md` and `README.md` are the nearest file; `origin.yaml` merges root-first. A value equal to what
the chain already holds is deleted from the lower file, never restated; one that needs less than an upper
layer holds cannot unsay a key, so a key goes up only where every dataset reading it agrees. So is a value
its reader derives: a knob at its schema default, a `pipelines.default` naming every node in order, and
the dataset's name, which is its path.

**What a cell hashes is the merged document**, so moving a value between layers moves no key — and the
campaign half validated, through `campaign_config.py::CampaignConfig.identity`, which drops every schema
default and the `optimizer`/`nodes` its optimizer's `Treatment` resolves against the manifest. Only
install content has a chain: a tenant upload is whole.

## `source:` is where the rows come from

**A definition's rows are a recipe plus the data half beside it** (`application/datasets/loaders.py`):
`recipe` names the code in `BANK_RECIPES` (rows) or `PANEL_RECIPES` (a connector-owned panel built into the
tenant tier), and every other key is the cut — `hub:` / `github:` with its pinned `rev`, splits, configs,
caps. A section with no `hub:`/`github:` is INLINE: its keys are the rows' inputs. A
new cut is a new definition, never a recipe edit. **Every fetched source is pinned to a commit** — an
unpinned one raises at parse; a moving ref re-cuts the bank under the name it already has.

## A ceiling is DECLARED, or the clock that needs it reports nothing

`campaign_config.accuracy_ceiling` is the accuracy a best-reachable prompt would score on this dataset at this campaign's model, and only the dataset owner can state it — it is a joint claim about the two, so it is not derivable from the bank, the schema or a literature number. Declaring one turns on `index.json::final.rounds_to_ceiling`; leaving it unset leaves that clock unset too, which is the honest reading and costs nothing else. **Declare it only where you can say what measured it** — a saturation screen on the same model, a published ceiling for the same split. A guessed value makes every campaign on the dataset publish a round count nobody can defend, and no run says so.

## A demo pool is declared in the split, and every row in it needs an answer

`campaign_config.dataset_split: {bench: N, demo: M}` holds `M` rows out as the **demo pool** — the only rows an individual's `shot_ids` may name, rendered into its prompt as query and ground truth. Run init partitions them away from the search pool and the bench set, so no demo row is ever scored. `partition_bank` refuses a demo row with no ground truth, so a verifier-graded bank declares none; `demo` omitted is a pool of zero, which silences every shot surface.

## Sole route for backend tunable changes

**Backend overlay (`dataset.yaml::pipeline.nodes.{name}.config`) is the only way to switch model, provider, temperature, or anything in a node's `optimizer.param_keys`.** Never edit the backend repo (including the co-owned TermNorm backend) to achieve a tunable switch. Pipeline-agnostic is a §0 commitment.

`load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`application/pipeline_resolve.py`) merges the overlay onto each wire payload. **The dataset owns its task model** in `nodes.{node}.config.model` — every LLM node must declare one, or `configure_and_apply_pipeline` raises a loud setup error (no silent fall-through to the backend's own default).

**`route_order` is the third key on that overlay, and only two of the three are locked.** `model` names WHAT answers, `provider` the GATEWAY it is asked through, and `route_order: [<host>, …]` which of that gateway's upstream HOSTS to try, in order (`nodes.{name}.config.route_order`; `current_config` carries it to `llm_call` untouched). Names are the gateway's own `provider_name` — read them off `served_by` in the ledger, never from a catalogue. **Which of the three the optimizer may search** — owned by [`application/optimizers/potter/CLAUDE.md`](../../application/optimizers/potter/CLAUDE.md) § The optimizer never searches the GATEWAY or the ROUTE; a dataset opens `model` by listing it in `optimizer.param_keys` and bounds it with `param_allowed_values.model` (absent ⇒ the whole `available_models` menu). Why an order pays at all: [`infrastructure/llm/CLAUDE.md`](../../infrastructure/llm/CLAUDE.md) § LLM client.

## Registered datasets

The roster is the directory tree; each dataset's connector is read off its merged `pipeline.nodes` — don't mirror either here. A definition stays here only when the package needs it with no tenant tier behind it. The special cases worth knowing:

- **`email-tagging`** — inline rows, so it is the one definition that runs with no network; the browser walk (`webapp/e2e/`) runs on it.
- **`justlogic/d234`** — the L4 inner benchmark, an iid mix of depths 2-4 ([§ L4 below](#l4--promptpotter-self)). **`promptpotter-self/`** ships two on the `promptpotter` connector: `panel` — the fixed calibration panel and the only one to read a result off; `e2e` — a degenerate one-cell twin the browser walk runs for cents, a fixture and never a second instrument. `pool`, which draws environments per round, is a tenant definition, since its seats name tenant benchmarks.
- **The optimizer's own prompt homes are not in this directory.** One manifest per optimizer lives under `promptpotter/assets/optimizers/`, which a campaign picks with `optimization.optimizer` and adjusts with `optimization.nodes`. Still **operator-owned files** — nothing writes them. `application/evidence/read.py` ranks the measured edits; graduating a winner into `assets/optimizers/potter/pipeline.yaml` is a deliberate hand-edit, and an installed operator shadows one file via `config/paths.py::optimizer_manifest_path`.

## Re-cutting a dataset needs a NEW name

**A measurement replays by CONTENT; per-sample history is kept by POSITION.** Replay matches a cell on its `sample_key` — the sample's query, label, question and connector-resolved `source_pin` — under the same instrument configuration, in every dataset at once: a sample carried into a wider panel or under a new name replays every cell already measured on it (`infrastructure/store/measurement_archive.py::ReplayFeed.advance`). What a `sample_id` still keys is the history — δ, hit rates, hard samples — scoped `(dataset_name, sample_id)`. So a change that puts different content at an existing position (editing, replacing or reordering rows, or any `source:` cut parameter) needs a new definition, while one that leaves every existing sample in its slot, like appending tasks to a panel, does not. Re-running a measured configuration on a re-cut name refuses with `DatasetIdentityError` rather than pooling two questions in one slot (`application/scoring/search_point_scorer.py::_replayable_on`).

## L4 — `promptpotter-self`

`promptpotter-self/` is the **recursive case**: the outer cycle mutates the inner cycle's optimizer prompt template fields. **Its `pipeline` declares no nodes and no pipelines**: the connector serves the graph (`Connector.pipeline_declaration`), derived from the manifest the panel's cells run — every llm node of it, never only the ones a round runs, or an escalation node reached on a stall could never be told to improve. So any preset can be an inner, and an edit evolved on either layer lifts onto the other with no copy to keep in step.

**L4 is a recursion, not a 4th layer** — owned by [`application/optimizers/potter/CLAUDE.md`](../../application/optimizers/potter/CLAUDE.md) § Add no 4th layer; here it is one more dataset, run through the `promptpotter` connector.

**The inner instrument is `justlogic/d234`, and a cut switch is never advice.** Each depth cut is a separate `dataset_name` with its own δ scale, so comparing "bands" across cuts reads a difference of rulers as a capability difference. A new cut is a new `justlogic/dNNN/` whose `source.depths` names its depths, never a re-cut of this one.

The remaining work lives in ONE place — [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) § Open (don't restate it here).

## Reference points — consult on every dataset question

- **Adding a dataset, canonical splits, and why X is or isn't wired** → [`docs/operations/dataset-selection-rationale.md`](../../../docs/operations/dataset-selection-rationale.md) § Adding a dataset. Research the canonical split; never invent one; check here first for "why didn't we use Y?" and "have we trialed Z?".
- **Per-dataset model + `reasoning_effort` + `max_tokens`, BBEH output-ceiling traps** → [`docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md). This — not self-optimizing campaign NOTES.md — is the canonical source for model recommendations.

## `rows.json` is the item bank, not a score cache

**Don't reason about origin cost from it.** It holds `{name, created_at, source_file, row_count, items}`,
read into `session.samples` at wiring. **A file *here* is a hand-authored SHIPPED bank; a fetched or built
one is the operator's** — owned by [`infrastructure/store/CLAUDE.md`](../../infrastructure/store/CLAUDE.md)
§ Dataset content has two tiers; nothing writes a bank into this directory. Measurements live in the
tenant-global content-addressed `measurements/` archive (`infrastructure/store/archive_queries.py`), which
is what replays origin rows across cycles, forks and resumes.

## Conventions

- **Origin = conservative floor** — owned by [`application/optimizers/potter/CLAUDE.md`](../../application/optimizers/potter/CLAUDE.md) § Origin = conservative floor. Every tunable in this directory's overlay starts at its floor.
- **`task.md` is L1's framing input** — written for the LLM that will generate candidates, not for human readers (though it should be readable). **Its front matter is that description DECOMPOSED, and it is optional.** When absent, the first mint of a framed campaign decomposes the body — one `checkin` call, billed on that run's ledger, at every entry point that mints (`application/jobs/mint.py::mint_framed_cycle`) — and lands it in `.promptpotter/{tenant}/task-context/{name}.yaml`, **not** back here, which is read-only under a wheel. Resolved by `readable_task_context`; hand-editing either tier's copy is supported.
- **Unframed is a declaration, never an absence.** `campaign_config.task_framing: off` runs with no framing even where one is committed — the ablation arm — and the campaign manifest keeps the delta, so a result never reads as a framed one.
- **`README.md` is operator-facing** — describes source, split, sample shape and every model screen run on it; cite the canonical evaluation protocol.
