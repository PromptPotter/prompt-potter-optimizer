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

| Notebook | Methods | Framework |
|----------|---------|-----------|
| `bbeh_capo.ipynb` | CAPO | [promptolution](https://github.com/automl/promptolution) |
| `bbeh_dspy.ipynb` | GEPA, MIPROv2, BootstrapFewShot | [DSPy](https://github.com/stanfordnlp/dspy) |

`bbeh_dspy.ipynb` has a flags cell at the top to toggle which optimizers run:

```python
RUN_GEPA = True
RUN_MIPRO = True
RUN_BOOTSTRAP = True
```

PromptPotter runs via its own CLI (not a Colab).

## How to run

1. Open notebook in Google Colab
2. Add your Groq API key to Colab Secrets (key name: `GROQ_API_KEY`)
3. Toggle optimizer flags if needed (DSPy notebook only)
4. Run All
5. Download the `results_*.json` output file(s)

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
