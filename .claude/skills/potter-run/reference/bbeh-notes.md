# BBEH — Dataset-Specific Notes

Source of truth: `notebooks/bbeh_potter.ipynb`. `datasets/bbeh/dataset.md` and `campaign.json` are partially stale — see "Known doc drift".

## Entry point

Authored and launched from the notebook — it owns what CLI doesn't: HF `BBEH/bbeh` load via `shared_config.py::load_and_split()`, `{input,target}` → `{query,ground_truth}` normalisation, per-task test eval of the global winner across 23 tasks, and `results_potter.json` export.

**Resume via CLI works**: `run_optimization_notebook` auto-mints a session+cycle pair and claims `.promptpotter/active_session.json` with the same `CAMPAIGN_ARTIFACTS` + `SESSION_ARTIFACTS` as CLI `init`. So `python -m promptpotter optimize` resumes an interrupted notebook run via the active pointer. Post-hoc renderers (`show-status` / `show-results`) are shared.

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

Also: `l2_patience=5`, `l3_patience=3`, `creativity=0.7`, `improvement_threshold=0.01`, `seed=42`. Notebook injects `task_context` inline — **do not run `set-task`** for BBEH.

If asked "what hyperparameters are we using?", cite the notebook and flag that `datasets/bbeh/campaign.json` is out of sync.

## Pipeline

Single-node `llm_only` (`datasets/bbeh/pipeline.json`): `openai/gpt-oss-120b` via Groq, `max_tokens=32000`, `reasoning_effort=medium`. Both are load-bearing — reducing either risks empty-output when reasoning tokens eat the output budget.

## Prerequisites

`.env` with `GROQ_API_KEY`, local backend on `:8000` exposing `llm_only`. First time: `python scripts/smoke_campaign.py --dataset bbeh` (~90s).

## Export schema

`docs/research/bbeh-comparison/results_potter.json` matches `results_capo.json` / `results_dspy.json`:
- `per_task[task_name] = {accuracy, n_test}`
- `config` = `{optimizer, max_rounds, n_variants, sp_budget_ttest, model_id, n_train, train_accuracy, baseline_train_accuracy, rounds, methodology, note}`
- `optimized_prompts = {"__global__": winner}`

`note` is conventionally `"unmeasured starting hyperparameters — pre-sweep"` so comparison tooling flags the number as a floor.

## Known doc drift (2026-04-18)

- `datasets/bbeh/dataset.md`: "Per-task loop: 23 separate campaigns" — wrong, one global. `max_tokens: 16000` — `pipeline.json` actually sets 32000.
- `datasets/bbeh/campaign.json`: diverges on `max_rounds`, `n_variants`, `sp_budget_ttest`, `l2_patience`, `l3_patience` — notebook's `build_campaign_config()` shadows it, safe to ignore for notebook runs. CLI path → flag and ask whether to sync first.

## "Empty predictions" bug (deferred)

Project memory flags TermNorm `llm_only` returning empty → 0% campaigns. Notebook iteration suggests it's intermittent or narrowed. If many `0.0` accuracies appear, check `library/dataset_runs/*.json` for empty `predicted` strings before re-running.

## Related tools

BBEH numbers feed `docs/research/related-work.md` (head-to-head matchup tables). CAPO/DSPy equivalents at `bbeh-comparison/bbeh_capo.ipynb` and `bbeh_dspy.ipynb` (Colab).
