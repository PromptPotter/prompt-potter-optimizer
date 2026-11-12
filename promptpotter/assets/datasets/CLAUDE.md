# assets/datasets/ — the shipped template definitions

Each `dataset.yaml` directory is a definition named by its path, read-only in the wheel; a tenant definition resolves first. **The wheel ships only what a user may start**: research and test tooling (L4's `promptpotter-self`) is the checkout's [`research/datasets/`](../../../research/datasets/CLAUDE.md), never listed. `defaults.yaml` (`{pipeline:, campaign:}`) lies under every definition; a `{family}/family.yaml` holds shared sections and is no dataset. Beside a `dataset.yaml`: `task.md` (L1's framing: front matter committed, body the description), `origin.yaml` (optional starting prompts per node), `README.md` (source, split, every model screen).

## A dataset file is a partial

**Every reader sees the chain laid together** (`infrastructure/store/dataset_access.py::definition_chain`): `defaults.yaml`, each `family.yaml` / `dataset.yaml` root-down, then the leaf. A value the chain already holds is deleted from the lower file; a key goes up only where every dataset reading it agrees; a value its reader derives is not written. A cell hashes the merged document, so moving a value between layers moves no key.

## `source:` is where the rows come from

Rows are a recipe (`BANK_RECIPES` / `PANEL_RECIPES`) plus the cut beside it; a new cut is a new definition, never a recipe edit. A re-cut is a new `dataset_hash` and needs no new name: the next `new` moves the label, and per-sample history carries over by `sample_key`.

## Declarations, not defaults

- **Declare `campaign_config.fitness_ceiling` only where you can say what measured it**; unset leaves `rounds_to_ceiling` unset, the honest reading.
- `dataset_split: {bench: N, demo: M}` holds out the only rows a node's `shots` may name.
- **Unframed is a declaration, never an absence**: `task_framing: off` is the ablation arm. Absent front matter, the first framed mint decomposes `task.md`'s body into the tenant's task-context tree (keyed by `dataset_id_prefix`), never back here.

## Sole route for backend tunable changes

**The backend overlay (`dataset.yaml` `pipeline.nodes.{name}.config`) is the only way to switch model, provider, temperature or any `optimizer.param_keys` tunable — never edit the backend repo**, TermNorm included. An LLM node declaring no `model` raises. `route_order` is [`infrastructure/llm/CLAUDE.md`](../../infrastructure/llm/CLAUDE.md)'s.
