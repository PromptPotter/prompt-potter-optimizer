# Benchmark Datasets — Readiness & Prioritization

PromptPotter supports two dataset types with fundamentally different infrastructure requirements and optimization characteristics.

## Dataset Types

### `backend` — Pipeline Optimization

Requires a running backend server with multi-node pipeline. PromptPotter optimizes both prompt fields and pipeline parameters (node configs like temperature, max_sites, scorer thresholds).

**Infrastructure needed:**
- Running backend server (`curl -s {url}/status`)
- `GET /pipeline` endpoint returning full `PipelineSchema`
- Dataset loaded in `DatasetStore` (from Excel via `dataset_builder.py`)

**Optimization characteristics:**
- Per-node intermediate caching (`IntermediateCache`) — prompt variants skip redundant upstream computation (~60% speedup when only downstream nodes change)
- Short-circuit nodes can terminate early (cache hits, high-confidence fuzzy matches)
- Two optimization surfaces: prompt string fields + pipeline params
- `PROMPT_STRING_FIELDS` split routes L1 candidate output to the right surface

### `llm-only` — Pure Prompt Optimization

No backend server. PromptPotter sends prompts directly to an LLM and evaluates the response against ground truth. Used for scientific benchmarks (GSM8K, HotPotQA, etc.).

**Infrastructure needed:**
- `DatasetLoader` implementation (fetches/parses the benchmark dataset)
- `eval_query_local()` adapter (renders prompt + calls LLM + returns `QueryResult`)
- Task-specific scorer integrated with `compile_scorer()` in `shared/scoring.py`

**Optimization characteristics:**
- No per-node caching — every eval is a fresh LLM call
- Prompt-only optimization surface (no pipeline params to tune)
- Convergence profile differs: no short-circuit nodes to exploit, all accuracy gains come from prompt quality
- Typically lower latency per call but no cache speedup across rounds

## Readiness Checklist

Before running a campaign, verify the dataset's readiness:

### For `backend` datasets:
1. Backend server is running and responding to `/status`
2. `campaign.json` has correct `exclude_nodes`, `pipeline_overrides`, `scoring`
3. Dataset is loaded (check `DatasetStore` or `--dataset-name` flag)

### For `llm-only` datasets:
1. `DatasetLoader` exists for this dataset (check `dataset.md` Status section)
2. Scorer formula defined in `campaign.json["scoring"]`
3. `eval_query_local()` adapter is implemented
4. If any of these are missing, the dataset is **not yet runnable** — `dataset.md` will say "Not yet implemented" and list what's needed

## Dataset Prioritization

When multiple datasets are available and the user asks which to run first, evaluate by:

1. **Scorer simplicity** — Start with the dataset whose scorer has fewer edge cases. Numeric exact match (GSM8K: extract number, compare) is simpler than Token F1 (HotPotQA: tokenization, normalization, precision/recall). Fewer debugging variables means the infrastructure pipeline gets validated without fighting scorer bugs.

2. **Competitor overlap** — Choose the dataset most commonly reported by competitors (DSPy/MIPROv2, GEPA, adv-CoT, PromptWizard, Promptomatix). Higher overlap = more direct comparison in the paper. GSM8K is near-universal; HotPotQA is common but less so.

3. **Feedback loop speed** — Smaller test sets and binary scoring give faster iteration. Consider `eval_sample_size` in campaign.json and whether scoring is pass/fail or continuous.

4. **Shared infrastructure** — The first dataset forces you to build the common infrastructure (DatasetLoader protocol, `eval_query_local()`, scoring integration). Pick the one where the task-specific parts (loader + scorer) are simplest, so most of your effort goes into reusable plumbing.

## Scoring System

PromptPotter's scoring is per-dataset and formula-driven:

- **Formula**: Python expression in `campaign.json["scoring"]`, compiled by `compile_scorer(formula)` in `shared/scoring.py`
- **Threading**: Formula compiles to a callable, passed via `EvalContext.scorer` to all eval paths
- **Two signals per query**:
  - `score` — continuous float from the scoring formula. Feeds the optimizer (composite accuracy, round winner selection)
  - `hit` — boolean, rank-1 exact match. Feeds query classification in SearchMemory and cohort analysis
- **Namespace**: The scorer receives a dict with `hit`, `ground_truth_rank`, `n_candidates`, `error`, plus a `rr()` helper for reciprocal rank
- **Default**: `float(hit)` when no formula specified (pure exact match)

### Scorer examples by dataset type:
- **TermNorm** (backend): `float(hit)` — exact match against normalized term
- **GSM8K** (llm-only): Needs numeric extraction from `#### N` format, then exact numeric comparison
- **HotPotQA** (llm-only): Needs Token F1 — tokenize predicted and gold answer, compute token-level precision/recall/F1

## Cost Model

| Factor | `backend` | `llm-only` |
|--------|-----------|------------|
| Per-round cost | `n_variants x eval_dataset_size` backend calls | `n_variants x eval_dataset_size` LLM calls |
| Caching benefit | High — `IntermediateCache` skips upstream nodes | None yet |
| Latency per query | Higher (multi-node pipeline) | Lower (single LLM call) |
| Round-over-round speedup | Yes (cache warms up) | No |
| API cost | Backend API (may be local/free) | LLM API tokens (Groq/OpenAI) |
