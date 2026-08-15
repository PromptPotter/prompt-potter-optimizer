# BBEH — Dataset-Specific Notes

Source of truth: `notebooks/bbeh_potter.ipynb`.

## Entry point

Authored and launched from the notebook — it owns what CLI doesn't: HF `BBEH/bbeh` load via `shared_config.py::load_and_split()`, `{input,target}` → `{query,ground_truth}` normalisation, per-task test eval of the global winner across 23 tasks, and `results_potter.json` export.

**Resume via CLI works**: `run_campaign` (`application/embedded_run.py`) auto-mints a session+cycle pair and claims the workspace's `.workspace/active_session.json` with the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `new <name>`. So `python -m promptpotter resume` resumes an interrupted notebook run via the active pointer. State reads happen by opening `campaigns/<cycle_id>/{dashboard.json,log.md,index.json}` directly — no CLI read commands.

Default with no active BBEH session → open the notebook. With an active BBEH campaign dir on disk → confirm which surface the user wants.

## Methodology — single global prompt

One prompt optimised over all 23 tasks pooled (460-example HF `mini` split = training pool). Winner evaluated per-task on ~4,060 non-mini examples; harmonic-mean across tasks is the official BBEH metric.

Per-task optimisation is rejected: 20 mini examples/task is noise, specialising inflates the score vs a single-deploy prompt, and HF mini/non-mini disjointness is lost if splits are redone.

Inline assertion in the notebook: `train_keys & test_keys == ∅`. If it fires, stop and report the leak.

| Slice | Count | Source |
|-------|-------|--------|
| Train | 460 | HF `BBEH/bbeh` mini, pooled across 23 tasks |
| Test  | ~4,060 | HF `BBEH/bbeh` non-mini, grouped per-task |

Seed `SPLIT_SEED = 42` from `shared_config.py` (HF-native split — seed only affects shuffling).

## Hyperparameters (pre-sweep floor, not ceiling)

```python
MAX_ROUNDS = 8
N_VARIANTS = 4
SP_BUDGET_TTEST = 15
```

Also: `l2_patience=5`, `l3_patience=3`, `creativity=0.7`, `improvement_threshold=0.01`, `seed=42`. The notebook injects `task_context` inline — for BBEH, no separate task-decompose step happens (and CLI `new bbeh` won't auto-decompose because the BBEH dataset directory has no `task_description.md`).

If asked "what hyperparameters are we using?", cite the notebook and flag that `datasets/bbeh/campaign.json` is out of sync.

## Pipeline

Single-node `llm_only` (`datasets/bbeh/pipeline.yaml`): `openai/gpt-oss-120b` via Groq (canonical) — operator may flip `model` to `openai/gpt-oss-20b` when 120b daily volume is exhausted (see [Groq daily-volume swap](../../../../docs/operations/dataset-reasoning-matrix.md#groq-daily-volume-model-swap)). No dataset-side `max_tokens` cap; `reasoning_effort=low` to stay clear of Groq's per-model output ceiling. Full per-dataset table: [`dataset-reasoning-matrix.md`](../../../../docs/operations/dataset-reasoning-matrix.md).

## Prerequisites

`.env` with `GROQ_API_KEY`, local backend on `:8000` exposing `llm_only`. First time: `python scripts/smoke_campaign.py --dataset bbeh` (~90s).

## Export schema

`docs/research/bbeh-comparison/results_potter.json` matches `results_capo.json` / `results_dspy.json`:
- `per_task[task_name] = {accuracy, n_test}`
- `config` = `{optimizer, max_rounds, n_variants, sp_budget_ttest, model_id, n_train, train_accuracy, origin_train_accuracy, rounds, methodology, note}`
- `optimized_prompts = {"__global__": winner}`

`note` is conventionally `"unmeasured starting hyperparameters — pre-sweep"` so comparison tooling flags the number as a floor.

## The notebook shadows the committed campaign config

`build_campaign_config()` overrides `datasets/bbeh/campaign.json` on `max_rounds`, `n_variants`, `sp_budget_ttest`, `l2_patience` and `l3_patience`, so the two disagree by design — ignore the divergence for notebook runs. On the CLI path the committed file governs: flag the difference and ask whether to sync before launching.

## "Empty predictions" bug (deferred)

Project memory flags TermNorm `llm_only` returning empty → 0% campaigns. Notebook iteration suggests it's intermittent or narrowed. If many `0.0` accuracies appear, check `measurements/runs/*.jsonl` for empty `predicted` strings before re-running.

## Related tools

BBEH numbers feed `docs/research/related-work.md` (head-to-head matchup tables). CAPO/DSPy equivalents at `bbeh-comparison/bbeh_capo.ipynb` and `bbeh_dspy.ipynb` (Colab).
