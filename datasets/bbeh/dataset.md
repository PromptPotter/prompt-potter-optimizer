# BBEH — Dataset Context

## Status

**Scaffolded for the `docs/research/bbeh-comparison/bbeh_potter.ipynb` comparison run.** Data is loaded
in-memory from HuggingFace `BBEH/bbeh` via
`docs/research/bbeh-comparison/shared_config.py::load_and_split` — there is no on-disk ground-truth file
in this folder. The notebook feeds the per-task train split directly into
`prepare_scoring_context(session, train_data, ...)`.

## Type

`llm-only` — single `llm_only` node, no retrieval.

## Init Flags (CLI — not used by the comparison notebook)

```
--dataset-name bbeh
--dataset-type llm-only
--config datasets/bbeh/campaign.json
--skip-baseline
```

## Data

- Source: HuggingFace `BBEH/bbeh` (mini split, 23 tasks × 20 examples = 460 total)
- Split: 10/task train (optimization), 10/task test (held-out), `seed=42`
- Loader: `docs/research/bbeh-comparison/shared_config.py::load_and_split()`

## Scoring

`exact_match(predicted, ground_truth)` — case-insensitive, whitespace-stripped string equality.
The scorer is **not** in `SCORING_FUNCTIONS` by default; `bbeh_potter.ipynb` registers it in-process before
running campaigns.

## Pipeline Notes

- Single `llm_only` node — prompt flows through `pipeline_params` via `PromptTemplate`
- Optimization target: prompt template (task framing, reasoning style, answer formatting)
- Per-task loop: 23 separate campaigns, one `cycle_id` per task
