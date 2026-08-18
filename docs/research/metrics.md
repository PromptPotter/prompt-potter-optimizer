# Metrics: Beyond Absolute Accuracy

PromptPotter reports four metrics instead of just one. Absolute accuracy (the standard figure in prompt-optimization papers) hides more than it reveals; these four separate *how good*, *how much of what's available*, *how cheaply*, and *how quickly*.

---

## The problem with a single number

When a paper reports a number on GSM8K, that number is almost always **absolute accuracy** — the percentage of test problems the optimized prompt gets correct on a specific base model. That's the standard reporting convention in this space. GSM8K, BBH, and MMLU form the canonical benchmark trio for prompt optimization:

| Dataset | Citation | Role |
|---------|----------|------|
| BIG-Bench Hard | Suzgun et al., 2022 | Reasoning, diverse tasks |
| GSM8K | Cobbe et al., 2021 | Math reasoning |
| MMLU | Hendrycks et al., 2020 | Knowledge breadth |

Absolute accuracy is meaningful only when you know the base model and the origin prompt. Without those, lifting 60 → 75 and 90 → 93 look comparable — but the first captured 75% of the available headroom and the second captured only 33%.

---

## The four metrics

| # | Metric | Symbol | Formula | What it measures | Why it matters |
|---|--------|--------|---------|------------------|----------------|
| 1 | **Absolute Accuracy** | Acc | `correct / total` on test set | Raw performance of the best prompt found | Standard comparison point — but meaningless without knowing the base model and origin prompt |
| 2 | **Headroom Captured** | HC | `(Acc_opt − Acc_base) / (Acc_ceil − Acc_base)` | Fraction of available improvement realized | Normalizes across models. Improving 60→75 with ceiling 80 (HC = 0.75) is more impressive than 90→93 with ceiling 99 (HC = 0.33) |
| 3 | **Sample Efficiency** | SE | `HC / N_queries` | Headroom captured per optimization query spent | How economically the optimizer finds gains. High SE = fewer LLM calls to reach the same lift |
| 4 | **Convergence Profile** | R₉₀ | Queries needed to reach 90% of final HC | Speed to near-saturation | Separates "finds good prompts" from "finds them fast". Critical for practical cost budgets |

---

---

## The winner's own number is biased upward

All four metrics above are read off the **selected** candidate, and selection and estimation must not
come from the same rows. Where they do, the reported figure overstates what the prompt will do on
deployment: the argmax of noisy means is optimistic, and PoBB compounds it, because elimination stops
an arm at a data-dependent time — so the survivor's mean is already biased before a max is taken over
it. Being Bayesian is not an exemption; the selected arm's posterior mean still conditions on the
selection that chose it. Neither is subset-invariant θ, which corrects for *which samples* were
scored, not for *which candidate was chosen* — orthogonal biases, and reaching for θ here is the
plausible wrong move.

Where each number stands today:

- **Published head-to-head figures are clean** — the split that makes them so is owned by
  [`bbeh-comparison/README.md`](bbeh-comparison/README.md) § The protocol, which already satisfies
  the requirement stated here.
- **In-campaign figures are not.** The winner's `composite_fitness`, the round banner, the dashboard
  headline and `export.json`'s fitness are computed on the rows that selected the winner. Fixing it
  means a reserved partition the loop never scores on, read by `verify` and by the export —
  tracked in [`../specs/roadmap.md`](../specs/roadmap.md) § Selection-clean reporting.

Named and corrected for in *Correcting the Winner's Curse in Adaptive Benchmarking*
([arXiv:2605.05973](https://arxiv.org/abs/2605.05973)), whose protocol assumes a fixed shortlist and
smooth stabilized selection — the assumption PoBB's adaptive stopping strains, so their estimator
needs checking against it before it is adopted.

---

A published table reports all four per (method, model), from `results_*.json`. See [`benchmarks.md`](benchmarks.md) for the evaluation protocol and dataset specs.
