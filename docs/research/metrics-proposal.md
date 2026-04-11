# Metrics Proposal: Beyond Absolute Accuracy

## The Problem with a Single Number

When a paper reports a number on GSM8K, that number is almost certainly **absolute accuracy**
— the percentage of test problems the optimized prompt gets correct on a specific base model.
That's the standard reporting convention in this space. GSM8K, BBH, and MMLU form the
canonical benchmark trio for prompt optimization:

| Dataset | Citation | Role |
|---------|----------|------|
| BIG-Bench Hard | Suzgun et al., 2022 | Reasoning, diverse tasks |
| GSM8K | Cobbe et al., 2021 | Math reasoning |
| MMLU | Hendrycks et al., 2020 | Knowledge breadth |

---

## Proposed Metrics

| # | Metric | Symbol | Formula | What It Measures | Why It Matters |
|---|--------|--------|---------|------------------|----------------|
| 1 | **Absolute Accuracy** | Acc | `correct / total` on test set | Raw performance of the best prompt found | Standard comparison point — but meaningless without knowing the base model and baseline prompt |
| 2 | **Headroom Captured** | HC | `(Acc_opt - Acc_base) / (Acc_ceil - Acc_base)` | Fraction of available improvement realized | Normalizes across models. Improving 60→75 with ceiling 80 (HC = 0.75) is more impressive than 90→93 with ceiling 99 (HC = 0.33) |
| 3 | **Sample Efficiency** | SE | `HC / N_queries` | Headroom captured per optimization query spent | How economically the optimizer finds gains. High SE = fewer LLM calls to reach the same lift |
| 4 | **Convergence Profile** | R₉₀ | Queries needed to reach 90% of final HC | Speed to near-saturation | Separates "finds good prompts" from "finds them fast". Critical for practical cost budgets |



## Proposed Table Format

| Method       | Model        | Acc   | HC    | SE₉₀  | R₉₀ |
|-------------|-------------|-------|-------|--------|-----|
| Zero-shot    | Llama-3-8B  |   | —     | —      | —   |
| MIPROv2      | Llama-3-8B  | -  | - | -  | -   |
| adv-CoT      | Llama-3-8B  | -  | - | ?      | ?   |
| PromptPotter | Llama-3-8B  | —     | —     | —      | —   |




- Together, the four metrics separate three concerns: *how good* (Acc), *how much of what's available* (HC), *how cheaply* (SE), and *how quickly* (R₉₀).
