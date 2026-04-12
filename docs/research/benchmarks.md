# Benchmark Methodology

## Priority (2026-04-12)

1. **BBEH (primary)** — the M10 publication benchmark. Ample headroom at `gpt-oss-120b`; head-to-head infrastructure ready at [`bbeh-comparison/`](bbeh-comparison/).
2. **HotPotQA (secondary, pending saturation probe)** — multi-hop QA data point. Probe first; run fully only if non-saturated.
3. **GSM8K, AIME 2025 (deprioritized)** — effectively saturated at `gpt-oss-120b`. Cite published numbers for context; run only if a future probe reveals headroom (e.g., under a smaller or constrained model setup).

The legacy "HotPotQA + GSM8K + AIME" framing below pre-dates the saturation finding. Dataset sections are kept as reference and for future model setups where headroom may exist.

## Datasets

### BBEH (Big-Bench Extra Hard) -- Primary

Successor to BBH from Google DeepMind. Replaces each of the 23 BBH tasks with a harder variant probing similar reasoning capabilities. Best general-purpose models score ~24%, reasoning-specialized ~54%. Ample headroom for prompt optimization to matter.

| Property | Value |
|----------|-------|
| Task | 23 diverse reasoning tasks |
| Split | Single (evaluation-only); we create train/test with seed=42 |
| Size | 4,520 full / 460 mini (20 per task) |
| Source | [BBEH/bbeh](https://huggingface.co/datasets/BBEH/bbeh) |
| Metrics | Exact Match (case-insensitive), macro-average across tasks |
| Format | `{"task": str, "input": str, "target": str, "mini": int}` |
| Paper | [arXiv:2502.19187](https://arxiv.org/abs/2502.19187) (ACL 2025) |

**Head-to-head infrastructure:** [`bbeh-comparison/`](bbeh-comparison/) contains Colab notebooks (`bbeh_capo.ipynb`, `bbeh_dspy.ipynb`) running CAPO, GEPA, MIPROv2, and BootstrapFewShot against the identical `gpt-oss-120b` model and identical 10/task train + 10/task test split (seed=42). PromptPotter runs via its own CLI producing the same JSON output schema. This is the M10 primary benchmark.

### HotPotQA -- Secondary (saturation probe pending)

Multi-hop question answering over Wikipedia paragraphs. Requires reasoning across multiple documents to produce a short answer.

| Property | Value |
|----------|-------|
| Task | Multi-hop QA |
| Split | Validation (distractor setting) |
| Size | 7,405 questions |
| Source | [Yang et al., 2018](https://hotpotqa.github.io/) |
| Metrics | Token F1, Exact Match (EM) |
| Format | `{"question": str, "answer": str, "context": list[list]}` |

Used in: MIPROv2, GEPA, adv-CoT. **Saturation status at `gpt-oss-120b`: unknown — probe scheduled in M10 Wave 1.**

### GSM8K -- Deprioritized (saturated)

> **Saturation note (2026-04-12):** Effectively saturated at `gpt-oss-120b`. Deprioritized as a primary publication target. Literature numbers cited in results tables; no new runs unless probe reveals headroom.

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

### AIME 2025 -- Deprioritized (saturated)

> **Saturation note (2026-04-12):** Effectively saturated at `gpt-oss-120b`. Deprioritized as a primary publication target. Literature numbers cited in results tables; no new runs unless probe reveals headroom.

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
| BBEH | Exact Match (case-insensitive), macro-avg across 23 tasks | Per-task accuracy | `bbeh_match` |
| HotPotQA | Token F1 | Exact Match | Token-level precision/recall (planned: `F1Evaluator`) |
| GSM8K (deprioritized) | Exact Match | — | `gsm8k_match` |
| AIME 2025 (deprioritized) | Exact Match | — | `aime_match` |

## Baselines

| Method | Description | Source |
|--------|-------------|--------|
| Zero-shot | Raw question, no system prompt | Manual |
| Few-shot (manual) | Hand-crafted 3-shot examples | Manual |
| DSPy Bootstrap | DSPy's bootstrap few-shot optimizer | [DSPy library](https://github.com/stanfordnlp/dspy) |
| MIPROv2 | DSPy's MIPRO v2 instruction + demo optimizer (cited) | [Opsahl-Ong et al., 2024](https://arxiv.org/abs/2406.11695) |
| GEPA | Reflective prompt evolution with trajectory feedback (cited) | [GEPA, 2025](https://github.com/stanfordnlp/dspy) |
| Promptomatix | Meta-prompt + DSPy compiler, cost-aware (cited) | Salesforce, 2025 (arXiv ID TODO) |
| adv-CoT | Adversarial generator-discriminator for reasoning (cited) | [adv-CoT, 2025](https://www.mdpi.com/) |
| PromptWizard | Critique-guided prompt optimization (cited) | [Microsoft, 2024](https://arxiv.org/abs/2405.18369) |
| PromptPotter (L1 only) | L1 generate + evaluate, no L2/L3 | This work |
| PromptPotter (L1+L2) | L1 + L2 context refinement | This work |
| PromptPotter (full) | L1 + L2 + L3 replanning | This work |


## Results

### Main Results -- BBEH Mini (Primary)

**Model:** gpt-oss-120b via Groq | **Split:** 10/task train, 10/task test, seed=42 | **Scoring:** Exact match, macro-average across 23 tasks

<!-- Filled from results_*.json after M10 runs complete -->

| Method | Overall | Source |
|--------|---------|--------|
| **PromptPotter (full)** | **—** | Ours (CLI) |
| PromptPotter (L1+L2) | — | Ours (CLI) |
| PromptPotter (L1 only) | — | Ours (CLI) |
| GEPA | — | `bbeh_dspy.ipynb` |
| MIPROv2 | — | `bbeh_dspy.ipynb` |
| BootstrapFewShot | — | `bbeh_dspy.ipynb` |
| CAPO | — | `bbeh_capo.ipynb` |
| Zero-shot | — | Ours |

Per-task breakdown (23 tasks × methods) will be added once experiments complete. Reproducible notebooks: [`bbeh-comparison/`](bbeh-comparison/).

### HotPotQA (Secondary, pending saturation probe)

<!-- Filled only if saturation probe shows headroom at gpt-oss-120b -->

| Method | HotPotQA F1 | HotPotQA EM | Source |
|--------|-------------|-------------|--------|
| Zero-shot | — | — | Ours |
| MIPROv2 | — | — | Cited |
| GEPA | — | — | Cited |
| **PromptPotter (full)** | **—** | **—** | **Ours** |

### Saturated Datasets (cited literature only)

GSM8K and AIME 2025 are effectively saturated at `gpt-oss-120b`. Literature numbers included for context only; no new runs planned under current model setup.

| Dataset | Method | Reported | Source |
|---------|--------|----------|--------|
| GSM8K EM | MIPROv2 | — | [Opsahl-Ong et al., 2024](https://arxiv.org/abs/2406.11695) |
| GSM8K EM | PromptWizard | — | [Agarwal et al., 2024](https://arxiv.org/abs/2405.18369) |
| AIME 2025 EM | GEPA | — | [GEPA, 2025](https://arxiv.org/abs/2507.19457) |

## Infrastructure Notes

**Suffix-hash cache (M9).** Per-query intermediate caching was rebuilt for this publication. The prior `IntermediateCache` used a chained-prefix scheme that walked O(n) per lookup and could only reuse upstream nodes across config changes — changing any node cascaded invalidation downstream. We replaced it with a **suffix-hash cache** keyed at every pipeline cut point, giving O(1) lookup for the common case, symmetric reuse across both upstream and downstream config changes, and mid-call short-circuiting when the backend streams intermediates. This is a deliberate pre-publication simplification, not an incidental rewrite — benchmark wall-clock numbers reported in this document are produced against the suffix-hash cache. Design: [`docs/architecture/suffix-cache.md`](../architecture/suffix-cache.md).

## Reference Papers

**Positioning.** PromptPotter's core loop is a direct descendant of **PromptWizard** (Microsoft, 2024) — critique-guided generation with mutate/score/refine cycles. Where PromptWizard stops at a single-prompt optimizer, PromptPotter absorbs the strongest idea from each subsequent line of work and lifts them onto a pipeline-aware substrate: **MIPROv2**'s joint instruction-and-demo search becomes our 8-field decomposition + per-node `pipeline_params`; **GEPA**'s reflective trajectory feedback becomes our L2 directive bridge; **Promptomatix**'s cost-aware objectives become our `sp_budget_ttest` + sequential elimination; **adv-CoT**'s adversarial refinement maps onto our L3 replan escalation. The goal is not to out-benchmark DSPy on its own turf — it's to overshadow PromptWizard by being what PromptWizard would have been if it had shipped after 2025's ideas landed.

| Paper | Affiliation | Year | Datasets | Key Result |
|-------|-------------|------|----------|------------|
| PromptWizard | Microsoft Research | 2024 | GSM8K, others | **PromptPotter's direct ancestor** — critique-guided mutate/score/refine loop. PromptPotter extends it with pipeline-awareness, L2/L3 escalation, and cross-run SearchMemory. |
| MIPROv2 | Stanford NLP (DSPy) | 2024 | GSM8K, HotPotQA | Up to 13% accuracy gains on Llama-3-8B; Bayesian optimization over instructions + demos |
| GEPA | Stanford NLP (DSPy) | 2025 | HotPotQA, AIME, IFBench, HoVer | +12% over MIPROv2 on AIME-2025; reflective prompt evolution |
| CAPO | AutoML Freiburg (DSPy) | 2025 | GSM8K, others | Evolutionary prompt optimization; paired t-test racing |
| Promptomatix | Salesforce AI Research | 2025 | GSM8K, SQuAD_2, CommonGen, AG News, XSum | Cost-aware optimization; competitive performance with reduced compute |
| adv-CoT | — | 2025 | 12 datasets (commonsense + arithmetic) | +4.44% on GPT-3.5-turbo, +1.08% on GPT-4o-mini; adversarial refinement |
| PromptPotter | Independent | 2025 | TermNorm, AIME 2025, BBEH | Critique-guided L1→L2→L3 loop; pipeline-aware optimization |

**When to use what.** *DSPy / MIPROv2* — you need a full programming framework for compound LLM systems and are willing to learn it. *GEPA* — you want state-of-the-art optimization quality in very few rollouts and are already inside DSPy. *Promptomatix* — you care most about optimizer cost. *PromptWizard* — you want a simple, cost-efficient single-prompt optimizer. *PromptPotter* — you have a multi-node pipeline (prompts + node params), you need cross-run learning, and you want the critique-guided quality of PromptWizard without being locked to a single LLM call.
