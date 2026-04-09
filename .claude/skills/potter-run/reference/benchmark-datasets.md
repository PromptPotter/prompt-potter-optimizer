# Benchmark Datasets — Readiness & Prioritization

Two dataset types: `backend` (multi-node pipeline, optimizes prompt + pipeline params) and `llm-only` (single LLM call, optimizes prompt only).

## Adding a New Dataset

The architecture is **registry + config** — no new code files. Two registries to update, one config directory to create.

### Registries (one entry each)

| Registry | File | What to add |
|----------|------|-------------|
| `DATASET_LOADERS` | `services/dataset_builder.py` | `"name": load_fn` — loader returns `[{"query": str, "ground_truth": str}]` |
| `SCORING_FUNCTIONS` | `shared/scoring.py` | `"name": scorer_fn` — receives `predicted` + `ground_truth`, returns float [0,1] |

Both are plain dicts. The loader fetches from any source (HuggingFace, file, API). The scorer is called from the formula in `campaign.json["scoring"]`.

### Config directory: `datasets/<name>/`

| File | Purpose |
|------|---------|
| `campaign.json` | `dataset_name`, `dataset_type`, `scoring` formula, `eval_sample_size`, optimizer settings |
| `pipeline.json` | Pipeline definition — nodes, LLM defaults, prompt template variables |
| `dataset.md` | Type, status, prerequisites, init flags |
| `task_description.md` | Domain context for L2/L3 optimization layers |
| `scan_variants.json` | (optional) Parameter variants for sensitivity scanning |

### Shared infrastructure (already built, no per-dataset work)

- **`LLMOnlyAdapter`** — generic drop-in for `BackendClient`. Reads prompts from `pipeline_params` the same way as any backend pipeline. No per-dataset config in the adapter.
- **`prompt_variants.json`** — shared prompt variant library (persona, thinking_style, etc.). Dataset-agnostic.
- **`compile_scorer()`** — compiles any formula from `campaign.json` into a callable, auto-injects all `SCORING_FUNCTIONS`.
- **`load_dataset(name)`** — dispatches to the right loader from `DATASET_LOADERS`.

## Readiness Checklist

**`backend`**: server running + `campaign.json` configured + dataset in DatasetStore.

**`llm-only`**: entry in `DATASET_LOADERS` + entry in `SCORING_FUNCTIONS` + config directory. If either registry entry is missing, `dataset.md` says "Not yet implemented".

## Prioritization

Pick by: scorer simplicity (fewer edge cases first) > competitor overlap (DSPy/MIPROv2/PromptWizard comparability) > feedback loop speed (smaller test sets iterate faster).

## Cost Model

| Factor | `backend` | `llm-only` |
|--------|-----------|------------|
| Per-round cost | `n_variants x eval_size` backend calls | `n_variants x eval_size` LLM calls |
| Caching | IntermediateCache skips upstream nodes | None |
| Round-over-round speedup | Yes | No |
