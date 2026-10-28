# assets/datasets/ — the shipped template definitions

**This directory is a template set, not a benchmark library**: each `dataset.yaml` directory is a definition
named by its path, read-only in the wheel; every other definition is a tenant one, resolved first.

## Canonical layout

```
assets/datasets/
├── defaults.yaml          # {pipeline:, campaign:} — what every definition below is laid over
├── {family}/family.yaml   # A family's shared sections; the family itself is no dataset
└── {name}/                # nested any depth; a plain directory between is only a path segment
    ├── dataset.yaml       # source: / pipeline: / campaign: / panel: — only what differs from above
    ├── task.md            # L1's framing input: front matter = committed framing, body = description
    ├── origin.yaml        # the starting prompts, per node (optional)
    └── README.md          # operator-facing: source, split, sample shape, model screens
```

## A dataset file is a partial

**Every reader sees the definition chain laid together** (`infrastructure/store/dataset_access.py::definition_chain`):
`defaults.yaml`, each `family.yaml` / `dataset.yaml` root-down, then the leaf. **A value equal to what the chain
already holds is deleted from the lower file**, and a key goes up only where every dataset reading it agrees; a
value its reader derives (a schema default, the name) is not written. **A cell hashes the merged document**, so
moving a value between layers moves no key. Only install content has a chain: a tenant upload is whole.

## `source:` is where the rows come from

A definition's rows are a recipe (`BANK_RECIPES` / `PANEL_RECIPES`) plus the cut beside it; a new cut is a new
definition, never a recipe edit; a split is the source's canonical one, never invented. An unpinned fetched
source raises at parse.

## A ceiling is DECLARED, or the clock that needs it reports nothing

**Declare `campaign_config.accuracy_ceiling` only where you can say what measured it** — a saturation screen on
the same model, a published ceiling for the same split. Unset leaves `rounds_to_ceiling` unset, the honest reading.

## A demo pool is declared in the split

`dataset_split: {bench: N, demo: M}` holds out the only rows a node's `shots` may name; `partition_bank` refuses a
demo row with no ground truth.

## Sole route for backend tunable changes

**The backend overlay (`dataset.yaml` `pipeline.nodes.{name}.config`) is the only way to switch model, provider,
temperature or any `optimizer.param_keys` tunable — never edit the backend repo**, TermNorm included. **The dataset
owns its task model**: an LLM node declaring no `model` raises at `configure_and_apply_pipeline`. `route_order` is
[`infrastructure/llm/CLAUDE.md`](../../infrastructure/llm/CLAUDE.md)'s.

## Registered datasets

The roster is the directory tree. Special cases:

- **`email-tagging`** — inline rows, runs with no network; the browser walk runs on it.
- **`promptpotter-self/`** — read a result off `panel` alone; `e2e` is a one-cell fixture for the browser walk.
- **Optimizer prompt homes are `promptpotter/assets/optimizers/`**, operator-owned; graduating a winner there is a hand-edit.

## A dataset is its content; its name is a label

**A bank's identity is the hash of its rows as stored** (`shared/hashing.py::dataset_hash`). A re-cut (rows, `source:`
section or the recipe's code, `loaders.py::Source.identity`) is a new hash and needs no new name: the next `new` moves the label, and per-sample history carries over by `sample_key`.

## L4 — `promptpotter-self`

**Its `pipeline` declares no nodes**: the connector serves the graph from the manifest the panel's cells run, every
llm node of it, so any preset can be an inner and an edit lifts across layers with no copy.

**The inner instrument is `justlogic/d234`, and a cut switch is never advice** — each depth cut has its own δ
scale; a new cut is a new `justlogic/dNNN/`, never a re-cut of this one.

## Conventions

- **`task.md` is L1's framing input**, written for the LLM. Its front matter is optional: absent, the first framed
  mint decomposes the body into the tenant's `task-context/{dataset_id_prefix(name)}.yaml`, never back here.
- **Unframed is a declaration, never an absence** — `task_framing: off` is the ablation arm, kept in the manifest.
- **`README.md` is operator-facing** — source, split, sample shape, every model screen, the canonical protocol.
