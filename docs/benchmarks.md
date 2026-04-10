# Benchmark Methodology

PromptPotter takes a prompt and a dataset, and finds a better prompt. For academic benchmarking, the simplest setup applies: a single LLM call with question-answer pairs. The optimization loop (L1 generate, L2 refine, L3 replan) is evaluated against standard datasets to measure prompt improvement and compare with published baselines (MIPROv2, GEPA, adv-CoT, Promptomatix).

**Status:** Methodology defined, export infrastructure built. Dataset loaders and benchmark-specific evaluators (F1, numeric exact match) are planned as M9 Track 1. Result tables contain placeholders (`—`) that are filled after benchmark campaigns complete.

---

## Datasets

### HotPotQA

Multi-hop question answering over Wikipedia paragraphs. Requires reasoning across multiple documents to produce a short answer.

| Property | Value |
|----------|-------|
| Task | Multi-hop QA |
| Split | Validation (distractor setting) |
| Size | 7,405 questions |
| Source | [Yang et al., 2018](https://hotpotqa.github.io/) |
| Metrics | Token F1, Exact Match (EM) |
| Format | `{"question": str, "answer": str, "context": list[list]}` |

Used in: MIPROv2, GEPA, adv-CoT.

### GSM8K

Grade school math word problems requiring multi-step arithmetic reasoning. Clean structured output with exact-match scoring.

| Property | Value |
|----------|-------|
| Task | Math word problems |
| Split | Test |
| Size | 1,319 questions |
| Source | [Cobbe et al., 2021](https://github.com/openai/grade-school-math) |
| Metrics | Exact Match (numeric answer) |
| Format | `{"question": str, "answer": str}` with `#### N` answer format |

Used in: MIPROv2, Promptomatix, adv-CoT.

### AIME 2025

Competition-level math from the American Invitational Mathematics Examination. Frontier models still struggle here — GEPA reported +12% over MIPROv2. All answers are integers in [0, 999].

| Property | Value |
|----------|-------|
| Task | Competition math |
| Split | Full (single split) |
| Size | 30 problems |
| Source | [MathArena/aime_2025](https://huggingface.co/datasets/MathArena/aime_2025) |
| Metrics | Exact Match (integer) |
| Format | `{"problem": str, "answer": int}` |

Used in: GEPA.

### Phase 2 (planned)

| Dataset | Task | Why |
|---------|------|-----|
| IFBench | Instruction following | Multi-criteria scoring, 2025 credibility signal |

---

## Evaluation Protocol

### Controlled Variables

All methods evaluated under identical conditions:

| Variable | Value |
|----------|-------|
| Model | <!-- TODO: e.g. Llama-3-8B or GPT-4o-mini --> |
| Temperature | 0.0 (deterministic) |
| Max tokens | <!-- TODO: dataset-specific --> |
| Dataset split | Fixed per dataset (see above) |
| Eval sample | Full test/validation set for final numbers; `sample_size=200` during optimization |
| Seeds | n=3 independent optimization runs per method |

### Scoring

| Dataset | Primary Metric | Secondary | Scorer |
|---------|---------------|-----------|--------|
| HotPotQA | Token F1 | Exact Match | Token-level precision/recall (planned: `F1Evaluator`) |
| GSM8K | Exact Match | — | Extract `#### N`, compare numeric value (`gsm8k_match`) |
| AIME 2025 | Exact Match | — | Extract last integer, compare to ground truth (`aime_match`) |

### Budget Control

Fair comparison requires fixed compute budget per method:

| Budget Component | How Measured |
|------------------|-------------|
| Optimizer LLM calls | Total calls to the optimizer model (L1/L2/L3 generation) |
| Eval LLM calls | Total calls to the target model (dataset evaluation) |
| Wall time | Total optimization time (optimizer + eval) |

Each method gets the same maximum eval budget. Optimization efficiency = accuracy gain per eval call.

---

## Baselines

| Method | Description | Source |
|--------|-------------|--------|
| Zero-shot | Raw question, no system prompt | Manual |
| Few-shot (manual) | Hand-crafted 3-shot examples | Manual |
| DSPy Bootstrap | DSPy's bootstrap few-shot optimizer | [DSPy library](https://github.com/stanfordnlp/dspy) |
| MIPROv2 | DSPy's MIPRO v2 instruction + demo optimizer (cited) | [Opsahl-Ong et al., 2024](https://arxiv.org/abs/2406.11695) |
| GEPA | Reflective prompt evolution with trajectory feedback (cited) | [GEPA, 2025](https://github.com/stanfordnlp/dspy) |
| Promptomatix | Meta-prompt + DSPy compiler, cost-aware (cited) | [Salesforce, 2025](https://arxiv.org/abs/2507.00000) |
| adv-CoT | Adversarial generator-discriminator for reasoning (cited) | [adv-CoT, 2025](https://www.mdpi.com/) |
| PromptWizard | Critique-guided prompt optimization (cited) | [Microsoft, 2024](https://arxiv.org/abs/2405.18369) |
| PromptPotter (L1 only) | L1 generate + evaluate, no L2/L3 | This work |
| PromptPotter (L1+L2) | L1 + L2 context refinement | This work |
| PromptPotter (full) | L1 + L2 + L3 replanning | This work |

---

## Head-to-Head Comparison Protocol

### Statistical Rigor

- **Multiple seeds:** Each method runs 3 times with different random seeds. Report mean and standard deviation.
- **Confidence intervals:** 95% Wilson score CI on accuracy (via `wilson_ci()` from `services/search/failure_group_analysis.py`).
- **Significance testing:** McNemar's test (paired, per-query) for primary comparisons. Two-proportion z-test (via `proportion_test()`) for unpaired comparisons.
- **Significance threshold:** p < 0.05 with Bonferroni correction for multiple comparisons.

### Ablation Design

Isolate each optimization layer's contribution:

| Ablation | What runs | What's disabled |
|----------|-----------|----------------|
| L1 only | Generate candidates, evaluate, select winner | L2 context refinement, L3 replanning |
| L1 + L2 | + L2 refines task_context after stagnation | L3 replanning |
| Full | + L3 modifies search plan after L2 stagnation | Nothing |
| No scan | Skip sensitivity scan, start from default prompt | Scan-informed seeding |

---

## Results

### Main Results

<!-- TODO: Fill after benchmark campaigns complete -->

| Method | HotPotQA F1 | HotPotQA EM | GSM8K EM | AIME 2025 EM | Source |
|--------|-------------|-------------|----------|--------------|--------|
| Zero-shot | — | — | — | — | Ours |
| Few-shot (manual) | — | — | — | — | Ours |
| DSPy Bootstrap | — | — | — | — | Cited |
| MIPROv2 | — | — | — | — | Cited |
| GEPA | — | — | — | — | Cited |
| Promptomatix | — | — | — | — | Cited |
| adv-CoT | — | — | — | — | Cited |
| PromptWizard | — | — | — | — | Cited |
| PromptPotter (L1 only) | — | — | — | — | Ours |
| PromptPotter (L1+L2) | — | — | — | — | Ours |
| **PromptPotter (full)** | **—** | **—** | **—** | **—** | **Ours** |

### Convergence

<!-- TODO: Fill after benchmark campaigns complete -->

| Round | HotPotQA F1 | GSM8K EM | AIME 2025 EM |
|-------|-------------|----------|--------------|
| 0 (baseline) | — | — | — |
| 1 | — | — | — |
| 2 | — | — | — |
| 3 | — | — | — |
| 5 | — | — | — |
| 10 | — | — | — |

### Ablation

<!-- TODO: Fill after benchmark campaigns complete -->

| Configuration | HotPotQA F1 | GSM8K EM | AIME 2025 EM | Rounds to best |
|--------------|-------------|----------|--------------|----------------|
| L1 only | — | — | — | — |
| L1 + L2 | — | — | — | — |
| L1 + L2 + L3 (full) | — | — | — | — |
| Full, no scan | — | — | — | — |

### Parameter Impact

<!-- TODO: Fill from SearchMemory export -->

| Axis | Effect Size | Consistency | Classification |
|------|-------------|-------------|---------------|
| — | — | — | — |

### Statistical Significance

<!-- TODO: Fill after all methods evaluated -->

| Comparison | p-value | Significant? | Note |
|-----------|---------|-------------|------|
| PromptPotter vs MIPROv2 (HotPotQA) | — | — | Cited |
| PromptPotter vs MIPROv2 (GSM8K) | — | — | Cited |
| PromptPotter vs GEPA (HotPotQA) | — | — | Cited |
| PromptPotter vs GEPA (AIME 2025) | — | — | Cited |
| PromptPotter vs PromptWizard (GSM8K) | — | — | Cited |
| PromptPotter vs Zero-shot (HotPotQA) | — | — | Ours |
| PromptPotter vs Zero-shot (GSM8K) | — | — | Ours |
| PromptPotter vs Zero-shot (AIME 2025) | — | — | Ours |

---

## Reproducibility: Cycle Identity Modes

PromptPotter uses a **cycle identity hash** to track optimization campaigns. The same cycle_id means the same experiment — candidates, results, and checkpoints are shared. Two modes control what goes into the hash:

### Experiment mode (default)

The cycle identity hashes only the **problem definition**:
- `active_steps` — which pipeline nodes are active
- `baseline_rendered` — the starting prompt
- `dataset_pairs` — the evaluation questions

Everything else is excluded (`TUNING_KEYS` in `lifecycle.py`):
- **Loop control:** `max_rounds`, `l1_patience`, `l2_patience`, `l3_patience`, `degradation_threshold`
- **Optimization strategy:** `model` (optimizer LLM), `seed`, `n_variants`, `creativity`, `improvement_threshold`, `sp_budget_ttest`

This means you can freely:
- Switch between `--round` (one round) and full loop (default, no flag) without losing history
- Change the optimizer model, adjust patience, tweak creativity or n_variants
- Interrupt and resume — cached candidates and dataset_run results carry over

Only changing the dataset, baseline prompt, or active pipeline steps starts a new experiment.

### Strict mode (for publication)

Enable by adding `"strict_cycle_identity": true` to `campaign.json`:

```json
{
  "campaign_config": {
    "strict_cycle_identity": true,
    ...
  }
}
```

Every parameter, including loop-control knobs, becomes part of the cycle identity. Changing `max_rounds` from 15 to 10 creates a distinct experiment. Use this when:
- Running definitive experiments for benchmark tables
- Generating ablation studies or statistical significance claims
- Anyone re-running with the same config must get the exact same cycle_id

### Recommended workflow

1. **Explore** in experiment mode (default) — iterate freely, adjust patience, interrupt and resume
2. **Lock down** — once you have a good config, set `"strict_cycle_identity": true` and run from scratch
3. **Publish** — the strict cycle_id guarantees exact reproducibility

---

## Reproducing Results

### Generate supplemental materials from completed campaigns

```bash
# Markdown supplemental (tables, CI, significance, reproducibility manifest)
python -m promptpotter.cli.export_results supplemental \
    --backend-id <backend-id> --output supplemental.md

# Structured JSON (for paper repo / further analysis)
python -m promptpotter.cli.export_results json \
    --backend-id <backend-id> --output paper_results.json

# Export specific campaigns only
python -m promptpotter.cli.export_results supplemental \
    --backend-id <backend-id> \
    --campaigns campaign_hotpotqa_001,campaign_gsm8k_001 \
    --output supplemental.md
```

### From notebook

```python
from promptpotter.display.campaign import generate_supplemental, generate_export_json

# Markdown
md = generate_supplemental(store, backend_id)

# JSON
data = generate_export_json(store, backend_id)
```

### What the export includes

| Section | Content |
|---------|---------|
| Campaign Comparison | Side-by-side table: baseline, best, delta, CI, rounds, budget |
| Convergence | Per-round accuracy for all campaigns |
| Pairwise Significance | Two-proportion z-test p-values between campaigns |
| Parameter Impact | SearchMemory axis rankings (effect size, consistency) |
| Failure Analysis | Failure pattern clusters with example queries |
| Query Difficulty | Easy/discriminating/hard/dead distribution |
| Reproducibility | Config hashes, pipeline snapshot, Python version, platform |

---

## Reference Papers

| Paper | Year | Datasets | Key Result |
|-------|------|----------|------------|
| PromptWizard | 2024 | GSM8K, others | Critique-guided generation; cost-efficient; PromptPotter's primary inspiration |
| MIPROv2 | 2024 | GSM8K, HotPotQA | Up to 13% accuracy gains on Llama-3-8B; Bayesian optimization over instructions + demos |
| GEPA | 2025 | HotPotQA, AIME, IFBench, HoVer | +12% over MIPROv2 on AIME-2025; reflective prompt evolution |
| Promptomatix | 2025 | GSM8K, SQuAD_2, CommonGen, AG News, XSum | Cost-aware optimization; competitive performance with reduced compute |
| adv-CoT | 2025 | 12 datasets (commonsense + arithmetic) | +4.44% on GPT-3.5-turbo, +1.08% on GPT-4o-mini; adversarial refinement |
