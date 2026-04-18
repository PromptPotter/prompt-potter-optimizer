# BBEH — Dataset-Specific Notes

Source of truth: `notebooks/bbeh_potter.ipynb`. This file captures the facts that
diverge from the generic `/potter-run` flow and from `datasets/bbeh/dataset.md`
(which contains stale guidance — see "Known doc drift" below).

## Primary entry point: notebook, not CLI

BBEH is driven from `notebooks/bbeh_potter.ipynb`. The skill's default CLI
flow (`init → set-task → optimize → show-results`) is **not** how this dataset
is run. The notebook:

1. Loads HuggingFace `BBEH/bbeh` via
   `docs/research/bbeh-comparison/shared_config.py::load_and_split()`.
2. Normalises `{input, target}` → `{query, ground_truth}`.
3. Calls the notebook API (`init_services`, `prepare_scoring_context`,
   `run_optimization_notebook`) from `promptpotter.presentation.ui.campaign`.
4. Runs per-task test evaluation of the global winner across all 23 tasks.
5. Exports `results_potter.json` next to `results_capo.json` and
   `results_dspy.json` for the comparison table.

When the user invokes `/potter-run bbeh`, default to **"open the notebook"**,
not `python -m promptpotter init`. Only run the CLI path if the user
explicitly asks for it.

## Methodology — single global prompt, not per-task

**One prompt optimized over all 23 tasks, pooled.** The campaign sees the
full 460-example mini split as one undifferentiated training pool and produces
a single winner prompt. That winner is then evaluated per-task on the ~4,060
non-mini examples, and per-task accuracies are reported (feeding the
harmonic-mean metric BBEH is officially graded on).

Why not per-task:

- 20 mini examples per task is noise-level. Per-task optimization hits 100%
  accuracy and early-stops on round 1 without learning anything.
- Specialising a different prompt per task inflates the score relative to
  what would actually be deployed at inference time (one prompt, 23 tasks).
- Leakage risk: HF mini/non-mini is disjoint by construction, so training on
  pooled mini and evaluating on non-mini has zero overlap. Per-task schemes
  are fine in principle but lose this disjointness guarantee if the split is
  redone.

**Sanity check** the notebook runs: `train_keys & test_keys == ∅` asserted
inline. If that assert ever fires, stop immediately and report the leak.

## Data split

| Slice | Count | Source |
|-------|-------|--------|
| Train (optimization) | 460 | HF `BBEH/bbeh` mini flag, pooled across 23 tasks |
| Test (held-out eval) | ~4,060 | HF `BBEH/bbeh` non-mini, grouped per-task |
| Tasks | 23 | HF-native task labels |

Seed: `SPLIT_SEED = 42` from `shared_config.py`. The split is HF-native, not
sampled — the seed only affects downstream shuffling.

## Hyperparameters (unmeasured starting points)

The notebook's three headline dials are explicitly flagged as **pre-sweep**
starting points, not tuned values. Any numbers reported from this setup are a
**floor**, not a ceiling, for PromptPotter on BBEH.

```python
MAX_ROUNDS = 8
N_VARIANTS = 4
SP_BUDGET_TTEST = 15
```

Other config the notebook sets (and that the dataset's `campaign.json` does
**not** match as of 2026-04-18):

- `l2_patience: 5`, `l3_patience: 3` (more forgiving than `campaign.json`'s
  `l2_patience: 2, l3_patience: 1`)
- `creativity: 0.7`, `improvement_threshold: 0.01`, `seed: 42`
- `task_context.task_description` is set inline in the notebook (not loaded
  from `datasets/bbeh/task_description.md`)

If the user asks "what hyperparameters are we using?", cite the notebook
values and flag that `datasets/bbeh/campaign.json` is out of sync.

## Task context (inline, not from file)

The notebook injects `task_context` directly into `campaign_config`:

> Solve a reasoning problem from BIG-Bench Extra Hard (BBEH), which spans 23
> diverse task types including boardgame QA, multi-step arithmetic, causal
> reasoning, disambiguation, and adversarial distractor text. Read the input
> carefully, reason step by step as needed, and return only the final answer.

This bypasses `set-task --task-file`. For BBEH, do not run `set-task` from
the CLI — the notebook is the authoritative source for task context.

## Pipeline

Single-node `llm_only` pipeline (per `datasets/bbeh/pipeline.json`):

- Model: `openai/gpt-oss-120b` via Groq (`MODEL_ID` from `shared_config.py`)
- `max_tokens: 32000`, `reasoning_effort: "medium"` — both required for long
  BBEH prompts; reducing either risks empty-output failures when hidden
  reasoning tokens consume the output budget.
- No retrieval, no enrichment, no ranking — plain LLM call with the optimized
  prompt as system message, query as user message.

## Prerequisites

1. `pip install -e ".[dev,jupyter]"` from repo root.
2. `pip install datasets` (HuggingFace).
3. `.env` with `GROQ_API_KEY` — notebook asserts it inline.
4. Local backend at `http://127.0.0.1:8000` exposing the `llm_only` node
   (check with `curl -s http://127.0.0.1:8000/status`).
5. Smoke-test first: `python scripts/smoke_campaign.py --dataset bbeh`
   (~90s).

## Export — results_potter.json schema

The notebook writes `docs/research/bbeh-comparison/results_potter.json` with
the same top-level shape as `results_capo.json` / `results_dspy.json`:

- `per_task[task_name] = {accuracy: float, n_test: int}` — one entry per
  BBEH task.
- `config` — includes `optimizer`, `max_rounds`, `n_variants`,
  `sp_budget_ttest`, `model_id`, `n_train`, `train_accuracy`,
  `baseline_train_accuracy`, `rounds`, `methodology`, `note`.
- `optimized_prompts = {"__global__": winner_prompt_str}` — single entry
  since there is one global winner.

`note` field is conventionally set to
`"unmeasured starting hyperparameters — pre-sweep"` so downstream comparison
tooling can flag the number as a floor.

## Known doc drift (as of 2026-04-18)

`datasets/bbeh/dataset.md` contains two stale lines:

1. Line 47: `"Per-task loop: 23 separate campaigns, one cycle_id per task"` —
   **contradicted by the notebook**. Reality: one global campaign.
2. Pipeline Notes block says `max_tokens: 16000` — `pipeline.json` actually
   sets `32000`. The notebook hits the backend's pipeline defaults, so the
   dataset.md value is descriptive but misleading.

`datasets/bbeh/campaign.json` also diverges from the notebook on
`max_rounds`, `n_variants`, `sp_budget_ttest`, `l2_patience`, `l3_patience`.
It's safe to ignore for BBEH runs — the notebook's `build_campaign_config()`
shadows it.

When running BBEH, trust the notebook. If the user wants the CLI path, flag
the drift and ask whether to sync `campaign.json` and `dataset.md` first.

## Memory note: "BBEH empty predictions bug"

There's a project memory flagging that TermNorm `llm_only` returns empty on
BBEH queries → 0% campaigns, deferred. The notebook's existence and its
inline assertion `assert os.environ.get("GROQ_API_KEY")` suggest this is the
working setup the user iterates with; the bug may be intermittent or have
been narrowed since the memory was written. If a run produces suspiciously
many `0.0` accuracies, check `dataset_runs/*.json` for empty `predicted`
strings before re-running — that's the signature of the deferred bug.

## Related tools comparison

BBEH numbers feed `docs/research/table-sup-1.md`. CAPO and DSPy comparison
notebooks live at `docs/research/bbeh-comparison/bbeh_capo.ipynb` and
`bbeh_dspy.ipynb` respectively (Colab-based — unlike `bbeh_potter.ipynb`
which runs locally against this repo).
