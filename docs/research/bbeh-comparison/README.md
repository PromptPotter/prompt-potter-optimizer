# BBEH Competitor Comparison

Head-to-head benchmark of **PromptPotter vs CAPO vs DSPy optimizers** on BBEH (Big-Bench Extra Hard) with identical model and dataset splits.

## Setup

| Variable | Value |
|----------|-------|
| Dataset | BBEH mini (460 examples, 20/task, 23 tasks) |
| Split | 10/task train (optimization), 10/task test (held-out), seed=42 |
| Inference model | `gpt-oss-120b` via Groq |
| Optimizer model | Same (single-model setup) |
| Scoring | Exact match (case-insensitive), macro-average across 23 tasks |

## Notebooks

| Notebook | Methods | Framework | Runtime |
|----------|---------|-----------|---------|
| `bbeh_capo.ipynb` | CAPO | [promptolution](https://github.com/automl/promptolution) | Colab |
| `bbeh_dspy.ipynb` | GEPA, MIPROv2, BootstrapFewShot | [DSPy](https://github.com/stanfordnlp/dspy) | Colab |
| `bbeh_potter.ipynb` | L1/L2/L3 critique-guided loop | PromptPotter (this repo) | **Local** |

`bbeh_dspy.ipynb` has a flags cell at the top to toggle which optimizers run:

```python
RUN_GEPA = True
RUN_MIPRO = True
RUN_BOOTSTRAP = True
```

## How to run

**CAPO / DSPy (Colab):**

1. Open notebook in Google Colab
2. Add your Groq API key to Colab Secrets (key name: `GROQ_API_KEY`)
3. Toggle optimizer flags if needed (DSPy notebook only)
4. Run All
5. Download the `results_*.json` output file(s)

**PromptPotter (local):** runs against this repo, not Colab — step 3 boots a backend on localhost, which a Colab runtime cannot reach. Installing from PyPI would not change that.

1. `pip install -e ".[dev,jupyter]"` from the repo root, plus `pip install datasets`
2. `.env` at the repo root with `GROQ_API_KEY`
3. Boot a TermNorm backend exposing the `llm_only` pipeline at `http://127.0.0.1:8000`
4. Open `bbeh_potter.ipynb` in Jupyter / VS Code and Run All
5. `results_potter.json` is written next to the notebook

The PromptPotter notebook routes every BBEH task through TermNorm `/matches` with `steps: ["llm_only"]` — the same evaluation path used by `lca-termnorm` and every other dataset. Per-task loop (23 campaigns, one `cycle_id` per task) mirrors the CAPO/DSPy harness. Hyperparameters in the config cell are **unmeasured starting points** (pre-sweep); `results_potter.json` tags this via its `config.note` field so downstream comparison tables don't misread the numbers as tuned.

## Output format

Each optimizer produces a JSON file with identical schema:

```json
{
  "method": "gepa|miprov2|bootstrap_fewshot|capo",
  "model": "gpt-oss-120b",
  "dataset": "bbeh-mini",
  "split_seed": 42,
  "per_task": { "task_name": { "accuracy": 0.7, "n_test": 10 } },
  "overall_accuracy": 0.45,
  "optimized_prompts": { "task_name": "..." }
}
```

## References

- BBEH: [arXiv:2502.19187](https://arxiv.org/abs/2502.19187) (ACL 2025)
- CAPO/promptolution: [arXiv:2512.02840](https://arxiv.org/abs/2512.02840)
- GEPA: [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) (ICLR 2026 Oral)
- MIPROv2: [arXiv:2406.11695](https://arxiv.org/abs/2406.11695) (EMNLP 2024)
- BootstrapFewShot: [DSPy docs](https://dspy.ai/api/optimizers/BootstrapFewShotWithRandomSearch/)
