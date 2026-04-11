# Benchmark Methodology

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
| AIME 2025 | Exact Match | — | Extract `\boxed{N}` (primary) or last integer (fallback), compare to ground truth (`aime_match`) |

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


## Results

### Main Results

<!-- Filled after benchmark campaigns complete -->

| Method | HotPotQA F1 | HotPotQA EM | GSM8K EM | AIME 2025 EM | Source |
|--------|-------------|-------------|----------|--------------|--------|
| Zero-shot | — | — | — | — | Ours |
| MIPROv2 | — | — | — | — | Cited |
| GEPA | — | — | — | — | Cited |
| **PromptPotter (full)** | **—** | **—** | **—** | **—** | **Ours** |

### Convergence, Ablation, Parameter Impact, Significance

| Method | HotPotQA F1 | HotPotQA EM | GSM8K EM | AIME 2025 EM | Source |
|--------|-------------|-------------|----------|--------------|--------|
| Zero-shot | — | — | — | — | Ours |
| MIPROv2 | — | — | — | — | Cited |
| GEPA | — | — | — | — | Cited |
| **PromptPotter (full)** | **—** | **—** | **—** | **—** | **Ours** |

## Reference Papers

| Paper | Year | Datasets | Key Result |
|-------|------|----------|------------|
| PromptWizard | 2024 | GSM8K, others | Critique-guided generation; cost-efficient; PromptPotter's primary inspiration |
| MIPROv2 | 2024 | GSM8K, HotPotQA | Up to 13% accuracy gains on Llama-3-8B; Bayesian optimization over instructions + demos |
| GEPA | 2025 | HotPotQA, AIME, IFBench, HoVer | +12% over MIPROv2 on AIME-2025; reflective prompt evolution |
| Promptomatix | 2025 | GSM8K, SQuAD_2, CommonGen, AG News, XSum | Cost-aware optimization; competitive performance with reduced compute |
| adv-CoT | 2025 | 12 datasets (commonsense + arithmetic) | +4.44% on GPT-3.5-turbo, +1.08% on GPT-4o-mini; adversarial refinement |
