# Benchmark Datasets — Readiness & Prioritization

All datasets route through the TermNorm backend. Benchmark datasets (GSM8K, HotPotQA) use the `llm_only` step — same `/matches` endpoint, just a single-step pipeline.

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
| `campaign.json` | `dataset_name`, `scoring` formula, `eval_sample_size`, optimizer settings |
| `pipeline.json` | Pipeline definition — `llm_only` node with LLM defaults, prompt template variables, optimizer metadata |
| `dataset.md` | Type, status, prerequisites, init flags |
| `task_description.md` | Domain context for L2/L3 optimization layers |
| `scan_variants.json` | (optional) Parameter variants for sensitivity scanning |

### Shared infrastructure (already built, no per-dataset work)

- **Backend `llm_only` step** — TermNorm's `/matches` endpoint accepts `steps=["llm_only"]` with `node_config` for the system prompt. This is the default evaluation path for all datasets.
- **`prompt_variants.json`** — shared prompt variant library (persona, thinking_style, etc.). Index 1 entries are task-agnostic defaults suitable for any dataset; index 2+ are TermNorm-specific/PromptWizard variants.
- **`compile_scorer()`** — compiles any formula from `campaign.json` into a callable, auto-injects all `SCORING_FUNCTIONS`.
- **`load_dataset(name)`** — dispatches to the right loader from `DATASET_LOADERS`.

## Readiness Checklist

1. TermNorm backend running (`curl -s http://127.0.0.1:8000/status`)
2. Registry entries added (loader + scorer)
3. Config directory created (`datasets/<name>/`)
4. Dataset loaded into DatasetStore

## Prioritization

Pick by: scorer simplicity (fewer edge cases first) > competitor overlap (DSPy/MIPROv2/PromptWizard comparability) > feedback loop speed (smaller test sets iterate faster).

## Cost Model

| Factor | Details |
|--------|---------|
| Per-round cost | First candidate evaluates full eval set; others use sequential elimination (t-test early-stop after 20 queries). Typical: `eval_size + ~20-30 per eliminated candidate` |
| Caching | IntermediateCache available for multi-step pipelines; single-step `llm_only` has no upstream to cache |
| Round-over-round speedup | Only for multi-step pipelines (TermNorm). Benchmark datasets: none |
