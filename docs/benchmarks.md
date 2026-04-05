# Benchmark Methodology

PromptPotter takes a prompt and a dataset, and finds a better prompt. For academic benchmarking, the simplest setup applies: a single LLM call with question-answer pairs. The optimization loop (L1 generate, L2 refine, L3 replan) is evaluated against standard datasets to measure prompt improvement and compare with published baselines (MIPROv2, GEPA, adv-CoT, Promptomatix).

**Status:** Methodology defined, export infrastructure built. Dataset loaders and benchmark-specific evaluators (F1, numeric exact match) are planned. Result tables contain placeholders (`—`) that are filled after benchmark campaigns complete.

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

### Phase 2 (planned)

| Dataset | Task | Why |
|---------|------|-----|
| AIME 2025 | Competition math | New frontier benchmark (GEPA: +12% over MIPROv2) |
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
| GSM8K | Exact Match | — | Extract `#### N`, compare numeric value (planned: `NumericExactMatchEvaluator`) |

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
| MIPROv2 | DSPy's MIPRO v2 instruction + demo optimizer | [Opsahl-Ong et al., 2024](https://arxiv.org/abs/2406.11695) |
| PromptPotter (L1 only) | L1 generate + evaluate, no L2/L3 | This work |
| PromptPotter (L1+L2) | L1 + L2 context refinement | This work |
| PromptPotter (full) | L1 + L2 + L3 replanning | This work |

---

## Head-to-Head Comparison Protocol

### Statistical Rigor

- **Multiple seeds:** Each method runs 3 times with different random seeds. Report mean and standard deviation.
- **Confidence intervals:** 95% Wilson score CI on accuracy (via `wilson_ci()` from `services/search/_stats.py`).
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

| Method | HotPotQA F1 | HotPotQA EM | GSM8K EM |
|--------|-------------|-------------|----------|
| Zero-shot | — | — | — |
| Few-shot (manual) | — | — | — |
| DSPy Bootstrap | — | — | — |
| MIPROv2 | — | — | — |
| **PromptPotter (full)** | **—** | **—** | **—** |

### Convergence

<!-- TODO: Fill after benchmark campaigns complete -->

| Round | HotPotQA F1 | GSM8K EM |
|-------|-------------|----------|
| 0 (baseline) | — | — |
| 1 | — | — |
| 2 | — | — |
| 3 | — | — |
| 5 | — | — |
| 10 | — | — |

### Ablation

<!-- TODO: Fill after benchmark campaigns complete -->

| Configuration | HotPotQA F1 | GSM8K EM | Rounds to best |
|--------------|-------------|----------|----------------|
| L1 only | — | — | — |
| L1 + L2 | — | — | — |
| L1 + L2 + L3 (full) | — | — | — |
| Full, no scan | — | — | — |

### Parameter Impact

<!-- TODO: Fill from SearchMemory export -->

| Axis | Effect Size | Consistency | Classification |
|------|-------------|-------------|---------------|
| — | — | — | — |

### Statistical Significance

<!-- TODO: Fill after all methods evaluated -->

| Comparison | p-value | Significant? |
|-----------|---------|-------------|
| PromptPotter vs MIPROv2 (HotPotQA) | — | — |
| PromptPotter vs MIPROv2 (GSM8K) | — | — |
| PromptPotter vs Zero-shot (HotPotQA) | — | — |
| PromptPotter vs Zero-shot (GSM8K) | — | — |

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
from notebooks.campaign_lib import generate_supplemental, generate_export_json

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
| MIPROv2 | 2024 | GSM8K, HotPotQA | Up to 13% accuracy gains on Llama-3-8B |
| GEPA | 2025 | HotPotQA, AIME, IFBench, HoVer | +12% over MIPROv2 on AIME-2025 |
| Promptomatix | 2025 | GSM8K, SQuAD_2, CommonGen, AG News, XSum | Broad coverage, conservative baselines |
| adv-CoT | 2025 | 12 datasets (commonsense + arithmetic) | +4.44% on GPT-3.5-turbo, +1.08% on GPT-4o-mini |
