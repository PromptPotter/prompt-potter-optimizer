# Benchmark Datasets — Readiness & Prioritization

Two evaluation modes: `backend` (default — queries routed to TermNorm `/matches` with `steps=["llm_only"]`) and `llm-only` (gated local eval — `LLMOnlyAdapter` calls LLMs directly, requires `LOCAL_EVAL_SECRET` authorization). See `TermNorm-excel/docs/spec/proper-step-loop.md` for full security model.

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
| `pipeline.json` | Pipeline definition — `llm_only` node with LLM defaults, prompt template variables, optimizer metadata |
| `dataset.md` | Type, status, prerequisites, init flags |
| `task_description.md` | Domain context for L2/L3 optimization layers |
| `scan_variants.json` | (optional) Parameter variants for sensitivity scanning |

### Shared infrastructure (already built, no per-dataset work)

- **`LLMOnlyAdapter`** — generic drop-in for `BackendClient`. Reads prompts from `pipeline_params` the same way as any backend pipeline. Gated behind `LOCAL_EVAL_SECRET` + `local_eval_token` — see `TermNorm-excel/docs/spec/proper-step-loop.md`.
- **Backend `llm_only` step** — TermNorm's `/matches` endpoint accepts `steps=["llm_only"]` with `node_config` for the system prompt. Default evaluation path (no local LLM keys needed).
- **`prompt_variants.json`** — shared prompt variant library (persona, thinking_style, etc.). Index 1 entries are task-agnostic defaults suitable for any dataset; index 2+ are TermNorm-specific/PromptWizard variants.
- **`compile_scorer()`** — compiles any formula from `campaign.json` into a callable, auto-injects all `SCORING_FUNCTIONS`.
- **`load_dataset(name)`** — dispatches to the right loader from `DATASET_LOADERS`.

## Readiness Checklist

**`backend` mode** (default): TermNorm running + `llm_only` node configured in `pipeline.json` + `campaign.json` configured + dataset in DatasetStore.

**`llm-only` mode** (gated): All of the above registry/config requirements PLUS:
- `LOCAL_EVAL_SECRET` set in `.env` (user sets this once)
- `local_eval_token` in `campaign.json` matching the secret (user adds per dataset)
- `dataset_type: "llm-only"` in `campaign.json`

**Critical:** If `LOCAL_EVAL_SECRET` is empty or `local_eval_token` is missing, `init_services` **silently falls through to `BackendClient`** — queries go to the backend URL instead of the LLM. This produces garbage results (e.g., math problems normalized as terminology). The `/potter-run` skill checks for this in Phase 0 step 5 and blocks with a clear explanation before wasting any eval calls.

## Prioritization

Pick by: scorer simplicity (fewer edge cases first) > competitor overlap (DSPy/MIPROv2/PromptWizard comparability) > feedback loop speed (smaller test sets iterate faster).

## Cost Model

| Factor | `backend` | `llm-only` (gated) |
|--------|-----------|---------------------|
| Per-round cost | `n_variants x eval_size` backend calls | `n_variants x eval_size` LLM calls (server keys) |
| Caching | IntermediateCache skips upstream nodes | None |
| Round-over-round speedup | Yes | No |
| Auth required | No (backend holds keys) | Yes (`LOCAL_EVAL_SECRET` + `local_eval_token`) — user configures, agent validates |
