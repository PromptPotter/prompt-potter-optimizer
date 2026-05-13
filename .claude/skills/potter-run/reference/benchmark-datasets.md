# Benchmark Datasets — Readiness & Prioritization

All datasets route through the pipeline backend. Benchmark datasets (GSM8K, HotPotQA) use the `llm_only` step — same `/matches` endpoint, just a single-step pipeline.

## Adding a New Dataset

The architecture is **registry + config** — no new code files. Two registries to update, one config directory to create.

### Registries (one entry each)

| Registry | File | What to add |
|----------|------|-------------|
| `DATASET_LOADERS` | `services/dataset_builder.py` | `"name": load_fn` — loader returns `[{"query": str, "ground_truth": str}]` |
| `SCORING_FUNCTIONS` | `shared/scoring.py` | `"name": scorer_fn` — receives `predicted` + `ground_truth`, returns float [0,1] |

Both are plain dicts. The loader fetches from any source (HuggingFace, file, API). The scorer is called from the formula in `campaign.json["scoring"]`.

### Scorer pattern

A scorer extracts the answer from the model's raw output and compares it to ground truth. The `predicted` variable in the formula is the full model response (may be multi-paragraph reasoning text), so the scorer must handle extraction.

**Function signature**: `(predicted: str, ground_truth: str) -> float`
- Return `1.0` for correct, `0.0` for wrong (binary scorers)
- Continuous scorers (like `rr`) are also supported; `hit` is derived as `score >= 1.0`

**Example** — AIME (integer extraction from reasoning text):
```python
def _aime_match(predicted: str, ground_truth: str) -> float:
    gt = int(ground_truth.strip())
    matches = _NUMBER_RE.findall(predicted or "")
    if not matches:
        return 0.0
    pred = int(float(matches[-1].replace(",", "")))
    return 1.0 if pred == gt else 0.0
```

**Registration**: add to `SCORING_FUNCTIONS` in `shared/scoring.py`.
**Formula**: set `"scoring": "aime_match(predicted, ground_truth)"` in `campaign.json`.
**`hit` derivation**: automatic — `hit = score >= 1.0` (set in `sample_measurement.py` after scorer runs).

### Config directory: `datasets/<name>/`

| File | Purpose |
|------|---------|
| `campaign.json` | `dataset_name`, `scoring` formula, `sp_budget_ttest`, optimizer settings |
| `pipeline.json` | Pipeline definition — `llm_only` node with LLM defaults, prompt template variables, optimizer metadata |
| `dataset.md` | Type, status, prerequisites, fresh-mode flags |
| `task_description.md` | Domain context for L2/L3 optimization layers |

### Shared infrastructure (already built, no per-dataset work)

- **Backend `llm_only` step** — the backend's `/matches` endpoint accepts `steps=["llm_only"]` with `node_config` for the system prompt. This is the default evaluation path for all datasets.
- **`prompt_variants.json`** — shared prompt variant library (persona, thinking_style, etc.). Index 1 entries are task-agnostic defaults suitable for any dataset; index 2+ are dataset-specific/PromptWizard variants.
- **`compile_scorer()`** — compiles any formula from `campaign.json` into a callable, auto-injects all `SCORING_FUNCTIONS`.
- **`load_dataset(name)`** — dispatches to the right loader from `DATASET_LOADERS`.

## Readiness Checklist

1. Pipeline backend running (`curl -s http://127.0.0.1:8000/status`)
2. Registry entries added (loader + scorer)
3. Config directory created (`datasets/<name>/`)
4. Dataset loaded into DatasetStore

## Prioritization

Pick by: scorer simplicity (fewer edge cases first) > competitor overlap (DSPy/MIPROv2/PromptWizard comparability) > feedback loop speed (smaller test sets iterate faster).

## Cost Model

| Factor | Details |
|--------|---------|
| Per-round cost | First candidate evaluates full eval set; others use sequential elimination (t-test early-stop after 20 queries). Typical: `eval_size + ~20-30 per eliminated candidate` |
| Caching | Prior dataset_runs reused across candidates sharing node_configs prefix. No per-node cache. |
| Round-over-round speedup | Only when consecutive candidates share the upstream prefix (tier-1 prior-result reuse). |
