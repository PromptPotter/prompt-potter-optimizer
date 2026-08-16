# BBEH Competitor Comparison

Head-to-head benchmark of **PromptPotter vs CAPO vs DSPy optimizers** on BBEH (Big-Bench Extra Hard).
**This directory owns the head-to-head protocol** — split, seed, metric and export schema. Nothing
else may restate them.

## The protocol

| Variable | Value |
|---|---|
| Dataset | BBEH mini — 460 examples, 20/task, 23 tasks |
| Split | Per task: `TRAIN_PER_TASK` train, `TEST_PER_TASK` test, shuffled under `SPLIT_SEED` — the constants in `shared_config.py`, which is the executable owner |
| Metric | Exact match (case-insensitive), macro-averaged across the 23 tasks |
| Export | `shared_config.py::export_results`, one schema for every method |

Every method is scored on the **same held-out rows**. PromptPotter pools the per-task train halves
into one list because it optimizes a single global prompt; the peers keep them per task. That changes
how the prompt is *searched*, never what it is *scored on*.

The wider non-mini evaluation (~4,060 rows) is deliberately deferred — it costs a full re-run of
every peer optimizer and buys nothing before a release worth spending it on.

> ⚠️ **The model is not yet held constant, and no number may be published until it is.** The peers
> call `gpt-oss-120b` via Groq (`shared_config.py::MODEL_ID`); PromptPotter calls whatever
> `datasets/bbeh/pipeline.yaml` pins, and that file's `available_models` currently admits only
> `mistral-small-3.2-24b`. Reconcile the two before comparing. The runner now stamps the model it
> actually reached into the results file rather than the constant, so a mismatch is visible in the
> output instead of silent.

## Notebooks

| Notebook | Methods | Framework | Runtime |
|---|---|---|---|
| `bbeh_capo.ipynb` | CAPO | [promptolution](https://github.com/automl/promptolution) | Colab |
| `bbeh_dspy.ipynb` | GEPA, MIPROv2, BootstrapFewShot | [DSPy](https://github.com/stanfordnlp/dspy) | Colab |
| [`../../../notebooks/bbeh_potter.ipynb`](../../../notebooks/bbeh_potter.ipynb) | L1/L2/L3 critique-guided loop | PromptPotter (this repo) | **Local** |

Both Colab notebooks open with a `%%writefile shared_config.py` cell, because Colab has no checkout
to import from. **That cell must stay byte-equivalent to `shared_config.py` in this directory** — when
they drift, the peers and PromptPotter silently measure different things, which is exactly how the
split diverged before. `bbeh_dspy.ipynb` has a flags cell to toggle `RUN_GEPA` / `RUN_MIPRO` /
`RUN_BOOTSTRAP`.

## How to run

**CAPO / DSPy (Colab):** open in Colab → add `GROQ_API_KEY` to Colab Secrets → toggle optimizer flags
(DSPy only) → Run All → download the `results_*.json` outputs.

**PromptPotter (local)** — it runs against this repo, not Colab, because it needs a backend on
localhost that a Colab runtime cannot reach:

1. `pip install -e ".[dev,jupyter]"` from the repo root, plus `pip install datasets`
2. `.env` at the repo root with `GROQ_API_KEY`
3. Boot a TermNorm backend exposing the `llm_only` pipeline at `http://127.0.0.1:8000`
4. Run `notebooks/bbeh_potter.ipynb`; `results_potter.json` lands next to it

**One global campaign, not one per task** — a single winner is optimized across the pooled train
split, then evaluated per task, and the export carries `optimized_prompts = {"__global__": winner}`.
Hyperparameters are **unmeasured starting points** (pre-sweep), tagged in `config.note` so a
downstream table cannot misread them as tuned.

## References

- BBEH: [arXiv:2502.19187](https://arxiv.org/abs/2502.19187) (ACL 2025)
- CAPO/promptolution: [arXiv:2512.02840](https://arxiv.org/abs/2512.02840)
- GEPA: [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) · MIPROv2: [arXiv:2406.11695](https://arxiv.org/abs/2406.11695)
- BootstrapFewShot: [DSPy docs](https://dspy.ai/api/optimizers/BootstrapFewShotWithRandomSearch/)
