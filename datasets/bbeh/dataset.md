# BBEH — Dataset Context

## Status

**Routed through TermNorm `/matches` with the `llm_only` pipeline** (M9 LLM-Only
Unification, 2026-04-14). Data is loaded in-memory from HuggingFace `BBEH/bbeh`
via `docs/research/bbeh-comparison/shared_config.py::load_and_split`. The
comparison notebook `docs/research/bbeh-comparison/bbeh_potter.ipynb` feeds the
per-task train split directly into `prepare_scoring_context`.

## Type

Single `llm_only` generation node. No retrieval, no enrichment. Evaluates
against a running TermNorm backend that exposes the `llm_only` pipeline.

## Init Flags (CLI)

```
--backend-url http://127.0.0.1:8000
--dataset-name bbeh
--config datasets/bbeh/campaign.json
```

Requires a TermNorm instance with the `llm_only` node enabled.

## Data

- Source: HuggingFace `BBEH/bbeh` (mini split, 23 tasks × 20 examples = 460 total)
- Split: 10/task train (optimization), 10/task test (held-out), `seed=42`
- Loader: `docs/research/bbeh-comparison/shared_config.py::load_and_split()`

## Scoring

`exact_match(predicted, ground_truth)` — case-insensitive, whitespace-stripped
string equality. Registered in
`promptpotter/shared/scoring.py::SCORING_FUNCTIONS`.

## Pipeline Notes

- Single `llm_only` node — prompt flows through `pipeline_params.llm_only.prompt`
- No dataset-side `max_tokens` cap — provider ceiling applies. `reasoning_effort: "low"`
  is intentional: it keeps Groq's per-model output ceiling (~2048 on `gpt-oss-20b`)
  from being consumed by the hidden reasoning trace before any visible token emerges.
  See `task_description.md` for the trap and
  [`docs/operations/dataset-reasoning-matrix.md`](../../docs/operations/dataset-reasoning-matrix.md)
  for the per-dataset matrix.
- Optimization target: prompt template, `reasoning_effort`, `temperature`,
  `max_tokens`. The optimizer can re-discover higher reasoning settings if they pay.
- Per-task loop: 23 separate campaigns, one `cycle_id` per task
