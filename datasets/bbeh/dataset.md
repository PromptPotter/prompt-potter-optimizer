# BBEH — Dataset Context

Big-Bench Extra Hard mini: 23 tasks × 20 examples = 460 rows, HuggingFace `BBEH/bbeh`.
Loader `load_bbeh` (`promptpotter/application/datasets/loaders.py`) — no native per-sample id, so
ids are assigned sequentially after flattening and per-task metadata is dropped.

**Head-to-head split, model and metric** — owned by
[`../../docs/research/bbeh-comparison/README.md`](../../docs/research/bbeh-comparison/README.md);
this dataset carries only what a campaign run needs.

**One global campaign, not one per task.** A single winner is optimized across the pooled train
split and evaluated per-task at test time — the export carries
`optimized_prompts = {"__global__": winner}` beside a `per_task` accuracy map. Nothing here loops
over the 23 tasks.

The answer-format contract for `exact_match` is the live string
`matchers.py::EXTRACTION_NOTES`, which the origin resolver already feeds to the prompt — read it
there rather than from a doc, because a prompt written against a paraphrase scores zero.
