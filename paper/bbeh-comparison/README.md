# BBEH Competitor Comparison

The uncontrolled reference arm: **PromptPotter vs CAPO vs DSPy optimizers** on BBEH (Big-Bench
Extra Hard), each peer run its own way. The controlled arm is `new --arm` —
[`../../docs/specs/roadmap.md`](../../docs/specs/roadmap.md) § The bench (M13).

## The protocol

| Variable | Value |
|---|---|
| Dataset | BBEH mini — 460 examples, 20/task, 23 tasks |
| Split | Per task `TRAIN_PER_TASK` / `TEST_PER_TASK` under `SPLIT_SEED` — `shared_config.py` is the executable owner |
| Metric | Exact match (case-insensitive), macro-averaged across the 23 tasks |
| Export | `shared_config.py::export_results`, one schema for every method |
| Opponents | CAPO (promptolution); GEPA / MIPROv2 / BootstrapFewShot behind `RUN_*` flags in the DSPy notebook |

Every method is scored on the same held-out rows. PromptPotter pools the per-task train halves into
one global prompt (`optimized_prompts = {"__global__": winner}`); the peers search per task. Its
hyperparameters are unmeasured starting points, tagged in `config.note`.

**Three blockers — no number from here is published until each is closed:**

- **The model.** The peers call `gpt-oss-120b` via Groq (`shared_config.py::MODEL_ID`); PromptPotter
  calls whatever `bbeh/dataset.yaml::pipeline.available_models` admits. The runner stamps the model
  it reached into the results file, so a mismatch shows.
- **The budget.** CAPO spends tokens, DSPy trials, PromptPotter rounds × variants. Match it in spent
  tokens, and report it as a dimension rather than a constant — the claim is fitness per dollar.
- **The grader.** The notebooks' `exact_match` lives only in their `%%writefile shared_config.py`
  cells and compares whole strings; PromptPotter grades with `matchers.py::_label_match` (last bold
  span, then BBEH's `fuzzy_match`), which favours us on chain-of-thought. The cells are meant to
  match `shared_config.py` byte for byte and nothing checks it.

## How to run

| Notebook | Methods | Runtime |
|---|---|---|
| `bbeh_capo.ipynb` | CAPO ([promptolution](https://github.com/automl/promptolution)) | Colab: `GROQ_API_KEY` in Secrets → Run All → download `results_*.json` |
| `bbeh_dspy.ipynb` | GEPA, MIPROv2, BootstrapFewShot ([DSPy](https://github.com/stanfordnlp/dspy)) | Colab, as above; toggle the `RUN_*` flags |
| [`../../notebooks/bbeh_potter.ipynb`](../../notebooks/bbeh_potter.ipynb) | PromptPotter | Local: `pip install -e ".[dev,jupyter]" datasets`, `GROQ_API_KEY` in `.env`, a TermNorm backend with `llm_only` on `:8000`; `results_potter.json` lands beside it |

## References

- BBEH: [arXiv:2502.19187](https://arxiv.org/abs/2502.19187) · CAPO: [arXiv:2512.02840](https://arxiv.org/abs/2512.02840)
- GEPA: [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) · MIPROv2: [arXiv:2406.11695](https://arxiv.org/abs/2406.11695) · [BootstrapFewShot](https://dspy.ai/api/optimizers/BootstrapFewShotWithRandomSearch/)
