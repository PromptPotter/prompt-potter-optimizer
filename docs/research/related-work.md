# Related Work

Two lineages to position PromptPotter against: (1) other prompt-optimization frameworks in the compound-AI systems literature, and (2) the automatic algorithm-configuration lineage out of AutoML, which most prompt-optimization papers don't cite but should.

---

## Prompt-optimization frameworks — positioning

PromptPotter's core loop is a direct descendant of **PromptWizard** (Microsoft, 2024) — critique-guided generation with mutate/score/refine cycles. Where PromptWizard stops at a single-prompt optimizer, PromptPotter absorbs the strongest idea from each subsequent line of work and lifts them onto a pipeline-aware substrate: **MIPROv2**'s joint instruction-and-demo search becomes our 8-field decomposition + per-node `pipeline_params`; **GEPA**'s reflective trajectory feedback becomes our L2 directive bridge; **Promptomatix**'s cost-aware objectives become our `sp_budget_ttest` + sequential elimination; **adv-CoT**'s adversarial refinement maps onto our L3 replan escalation. The goal is not to out-benchmark DSPy on its own turf — it's to overshadow PromptWizard by being what PromptWizard would have been if it had shipped after 2025's ideas landed.

| Paper | Affiliation | Year | Datasets | Key result |
|-------|-------------|------|----------|------------|
| PromptWizard | Microsoft Research | 2024 | GSM8K, others | **PromptPotter's direct ancestor** — critique-guided mutate/score/refine loop. PromptPotter extends it with pipeline-awareness, L2/L3 escalation, and cross-run SearchMemory. |
| MIPROv2 | Stanford NLP (DSPy) | 2024 | GSM8K, HotPotQA | Up to 13% accuracy gains on Llama-3-8B; Bayesian optimization over instructions + demos |
| GEPA | Stanford NLP (DSPy) | 2025 | HotPotQA, AIME, IFBench, HoVer | +12% over MIPROv2 on AIME-2025; reflective prompt evolution |
| CAPO | AutoML Freiburg (DSPy) | 2025 | GSM8K, others | Evolutionary prompt optimization; paired t-test racing |
| Promptomatix | Salesforce AI Research | 2025 | GSM8K, SQuAD_2, CommonGen, AG News, XSum | Cost-aware optimization; competitive performance with reduced compute |
| adv-CoT | — | 2025 | 12 datasets (commonsense + arithmetic) | +4.44% on GPT-3.5-turbo, +1.08% on GPT-4o-mini; adversarial refinement |
| PromptPotter | Independent | 2025 | TermNorm, AIME 2025, BBEH | Critique-guided L1→L2→L3 loop; pipeline-aware optimization |

**When to use what.** *DSPy / MIPROv2* — you need a full programming framework for compound LLM systems and are willing to learn it. *GEPA* — you want state-of-the-art optimization quality in very few rollouts and are already inside DSPy. *Promptomatix* — you care most about optimizer cost. *PromptWizard* — you want a simple, cost-efficient single-prompt optimizer. *PromptPotter* — you have a multi-node pipeline (prompts + node params), you need cross-run learning, and you want the critique-guided quality of PromptWizard without being locked to a single LLM call.

### Feature highlights

| Capability | PP | po | pf | How in PromptPotter |
|------------|:--:|:--:|:--:|---------------------|
| **Self-healing optimization** | 🟢 | 🔴 | 🔴 | L1-proposed values outside the declared allowed set are caught at parse time, scored 0 with no backend call, and fed to L2 as a self-healing signal. As far as we know, no other prompt-optimization framework does this. See [../concepts/self-healing.md](../concepts/self-healing.md). |
| **Auto-injected scoring** | 🟢 | 🔴 | 🔴 | Per-dataset scoring formula from `campaign.json`, compiled once, injected into all eval paths |
| **IDE-native operation** | 🟢 | 🔴 | 🔴 | `/potter-run` Claude Code skill — full campaign lifecycle from the terminal |
| **Prompt + pipeline optimization** | 🟢 | 🔴 | 🔴 | 8-field prompt decomposition + per-node `pipeline_params` — optimizes prompts AND pipeline config jointly |
| **Statistical early-stopping** | 🟢 | 🟡 | 🔴 | Sequential elimination via paired Wilcoxon signed-rank test + Holm-Bonferroni (α=0.05) after 20 queries |
| **Cross-run learning** | 🟢 | 🔴 | 🔴 | SearchMemory — parameter impact, axis exhaustion, value trends, query tractability, failure-group × axis correlation |

### Feature matrix

| Dimension | PromptPotter | promptolution | promptfoo |
|-----------|-------------|---------------|-----------|
| **Language** | Python 3.13+ | Python 3.10–3.12 | TypeScript |
| **Adoption** | Research/production tool | 126 stars (academic, AutoML group) | 19.9k stars, 300K+ users, acquired by OpenAI |
| **Core approach** | Critique-guided L1→L2→L3 loop | Evolutionary (GA, DE) + LLM-as-optimizer (OPRO) + hybrid (CAPO) | Manual A/B testing (human writes all variants) |
| **Multi-step pipeline** | Per-node params, PipelineSchema from backend | No — single LLM call only | Single LLM call (custom script for multi-step) |
| **Budget control** | `sp_budget_ttest` (adaptive), early-stopping | Token budget callback | `maxConcurrency`, `repeat`, `timeoutMs` |
| **Scoring** | Composite formula, custom per-dataset | `accuracy_score`, reward function, LLM-as-judge | 40+ assertion types (deterministic + model-graded) |
| **Candidate selection** | Sequential elimination, Wilcoxon signed-rank early-stop | CAPO: paired t-test racing (α=0.2). Others: full eval or subsampling | Pass/fail assertions, weighted aggregation |
| **Cross-run learning** | SearchMemory (parameter impact, axis exhaustion, failure groups) | None (in-memory only, lost on exit) | None (each eval independent) |
| **Few-shot optimization** | Pipeline-level (backend handles examples) | CAPO: joint instruction + few-shot optimization | Not applicable (manual) |
| **Prompt representation** | 8-field decomposition | Opaque string (monolithic instruction) | Opaque string templates (Nunjucks) |
| **Red teaming** | — | — | 50+ vulnerability types, dedicated pipeline |
| **RAG / agent metrics** | — | — | context-faithfulness/-recall/-relevance, trajectory assertions |
| **Persistence** | Two-tier (session + campaign store), content-addressed archival | None (in-memory; FileOutputCallback writes parquet/csv post-hoc) | Disk cache for LLM responses only |
| **Provider ecosystem** | Backend-agnostic (single BackendClient endpoint) | OpenAI-compatible API, HuggingFace local, vLLM | 50+ built-in (OpenAI, Anthropic, Groq, Bedrock, etc.) |
| **CI/CD** | — | — | GitHub Actions, GitLab, Jenkins, Azure Pipelines, etc. |

---

## Head-to-head matchups from the literature

> **Reading these tables:** Each matchup is from a single paper using identical eval conditions (same model, same splits, same scoring). Numbers across matchups are **NOT comparable** — different backbone models, token budgets, and eval protocols. For the full taxonomy, see the [Compound AI Systems Optimization survey](https://arxiv.org/abs/2506.08234) (EMNLP 2025).

### Matchup 1 — GEPA vs MIPROv2 (GEPA paper, ICLR 2026 Oral)

**Source:** [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) | **Inference:** Qwen3-8B | **Optimizer:** undisclosed (frontier)

| Method | HotPotQA | HoVer | PUPA |
|--------|----------|-------|------|
| Baseline | 42.33 | — | — |
| GRPO | 43.33 | — | 86.66 |
| MIPROv2 | 55.33 | 47.33 | 81.55 |
| **GEPA** | **62.33** | **52.33** | **91.85** |

GEPA also reports GPT-4.1 Mini results on same tasks (same relative ranking). On AIME-2025 (GPT-4.1 Mini): GEPA 56.6% vs MIPROv2 46.6%.

### Matchup 2 — CAPO vs field (promptolution paper)

**Source:** [arXiv:2512.02840](https://arxiv.org/abs/2512.02840) (Dec 2025) | **Inference:** Gemma-3-27B | **Optimizer:** Llama-3.3-70B | **Budget:** 1M tokens, 500 dev / 300 test

| Method | GSM8K (test) | SST-5 (test) |
|--------|-------------|-------------|
| Unoptimized | 78.1% | 44.6% |
| OPRO | 69.7% | 56.0% |
| EvoPromptGA | 91.0% | 53.3% |
| **CAPO** | **93.7%** | **56.3%** |
| AdalFlow | 88.7% | 55.7% |
| DSPy (GEPA) | 84.7% | 42.0% |

### Matchup 3 — AdalFlow vs TextGrad vs DSPy (AdalFlow paper)

**Source:** [arXiv:2501.16673](https://arxiv.org/abs/2501.16673) (Jan 2025) | **Inference:** GPT-3.5-turbo-0125 | **Optimizer:** GPT-4o

| Method | ObjectCount | TREC-10 | HotPotQA (Vanilla RAG) | HotPotQA (Multi-hop) | HotPotQA (Agentic) |
|--------|------------|---------|------------------------|---------------------|-------------------|
| DSPy | 82.5% | 81.7% | 42.375% | 47.75% | 31% |
| TextGrad | 84.5% | 84.88% | — | — | — |
| **AdalFlow** | **93.75%** | **87.5%** | **43.25%** | **49.625%** | **32.25%** |

### Matchup 4 — Trace vs DSPy (Trace paper, NeurIPS 2024)

**Source:** [arXiv:2406.16218](https://arxiv.org/abs/2406.16218) (Jun 2024) | **Inference:** GPT-3.5-turbo-1106 | **Optimizer:** GPT-4

| Method | BBH All (23 tasks) | BBH Algorithmic (11 tasks) |
|--------|-------------------|---------------------------|
| DSPy+CoT | 70.4% | — |
| DSPy-PO+CoT | 71.6% | 70.0% |
| **Trace+CoT** | **78.6%** | **80.6%** |

3x faster wall-clock time than TextGrad with comparable or better accuracy.

### Matchup 5 — AFlow vs ADAS (AFlow paper, ICLR 2025 Oral)

**Source:** [arXiv:2410.10762](https://arxiv.org/abs/2410.10762) (Oct 2024) | **Optimizer:** Claude-3.5-Sonnet | **Inference:** GPT-4o-mini, DeepSeek-V2.5, Claude-3.5-Sonnet, GPT-4o (all tested)

| Method | GSM8K | HotPotQA | MATH | Avg (6 benchmarks) |
|--------|-------|----------|------|---------------------|
| ADAS | 81.3% | 78.5% | 68.7% | — |
| **AFlow** | **83.5%** | **77.9%** | **82.9%** | **80.3%** |

AFlow enables GPT-4o-mini + optimized workflow to outperform GPT-4o + manual workflow at 4.55% of inference cost.

---

## Key papers and venues

| Paper | Venue | Year | Category |
|-------|-------|------|----------|
| [APE](https://arxiv.org/abs/2211.01910) | ICLR | 2023 | LLMs as prompt engineers (foundational) |
| [TextGrad](https://arxiv.org/abs/2406.07496) | Nature | 2024 | Compound system optimization |
| [Trace/OPTO](https://arxiv.org/abs/2406.16218) | NeurIPS | 2024 | Workflow optimization |
| [MIPROv2](https://arxiv.org/abs/2406.11695) | EMNLP | 2024 | Multi-stage prompt optimization |
| [AFlow](https://arxiv.org/abs/2410.10762) | ICLR (Oral) | 2025 | Workflow architecture search |
| [ADAS](https://arxiv.org/abs/2408.08435) | ICLR | 2025 | Agent architecture search |
| [PromptWizard](https://arxiv.org/abs/2405.18369) | — | 2024 | Critique-guided prompt optimization |
| [metaTextGrad](https://arxiv.org/abs/2505.18524) | NeurIPS | 2025 | Meta-optimization |
| [GEPA](https://arxiv.org/abs/2507.19457) | ICLR (Oral) | 2026 | Reflective prompt evolution |
| [CAPO/promptolution](https://arxiv.org/abs/2512.02840) | — | 2025 | Evolutionary prompt optimization |
| [AdalFlow](https://arxiv.org/abs/2501.16673) | — | 2025 | LLM AutoDiff (compound systems) |
| [Optimas](https://arxiv.org/abs/2507.03041) | ICLR | 2026 | Multi-component compound systems |
| [Survey](https://arxiv.org/abs/2506.08234) | EMNLP | 2025 | Compound AI optimization taxonomy |

---

## Algorithm configuration: the missing lineage

Prompt-optimization papers position themselves against Bayesian optimization, evolutionary search, reinforcement learning, or LLM-as-optimizer (OPRO). The **automatic algorithm configuration** lineage out of AutoML — F-Race → irace → ParamILS → SMAC — is structurally closer to what tools like PromptPotter and CAPO actually do, but the connection is almost never made explicit.

### The lineage

- **F-Race** (Birattari, Stützle, Paquete & Varrentrapp, 2002) — Friedman-test racing for metaheuristic configuration. Candidates race on problem instances; statistically inferior candidates are eliminated as soon as the test rejects them. The grandfather of modern algorithm configurators.
- **irace** (López-Ibáñez et al., 2016, *Operations Research Perspectives*) — iterated F-Race. Canonical AutoML algorithm-configurator: sample configurations from a model, race them on instances, eliminate losers, update the sampling model, repeat. Still the reference implementation the field measures against.
- **ParamILS** (Hutter, Hoos & Stützle, 2009) — iterated local search over parameter configurations. Different search operator (ILS rather than racing), same problem statement.
- **SMAC** (Hutter, Hoos & Leyton-Brown, 2011) — model-based (random-forest surrogate) algorithm configurator. The Bayesian-optimization branch of the same family.
- **Hyperband / BOHB** (Li et al., 2017; Falkner et al., 2018) — the hyperparameter-optimization branch: successive halving with early stopping on training curves. Same racing intuition, applied to ML training rather than algorithm runs.
- **Optuna** (Akiba et al., 2019) — the widely-adopted HPO framework; exposes the same primitives (search space, pruning, racing-style early stopping) to practitioners.

### Where PromptPotter sits

PromptPotter's sequential elimination (paired Wilcoxon signed-rank + Holm-Bonferroni, α=0.05, minimum 6 queries before any candidate can be dropped) **is** a racing procedure. The mapping to the algorithm-configuration framing is direct:

| Algorithm configuration | PromptPotter |
|-------------------------|--------------|
| Configuration space | `pipeline_params` + 8-field prompt decomposition |
| Problem instance | One dataset query |
| Runtime / cost metric | Scoring formula output |
| Racing test | Wilcoxon signed-rank, Holm-Bonferroni correction |
| Sampling model | L1 generator (LLM) + L1-critique-guided L2/L3 |
| Termination | `sp_budget_ttest` budget, convergence, or HITL pause |

`pipeline_params` optimization is the closest direct analogue to classical algorithm configuration — node parameters are exactly the kind of numerical/categorical knobs irace was built for. The 8-field prompt decomposition is the prompt-native extension: each field is a semantic parameter that the L1→L2→L3 critique loop mutates. That critique loop is the one piece irace lacks — irace's sampling models are numerical (truncated normals, discrete distributions); it has no notion of "reflect on why this configuration failed and propose a better one." Conversely, PromptPotter lacks irace's formal guarantees on configuration-space coverage and its convergence proofs.

### The gap in the prompt-optimization literature

MIPROv2, GEPA, PromptWizard, Promptomatix, adv-CoT, AFlow, ADAS — none cite the algorithm-configuration lineage. The closest the field gets is **CAPO** (promptolution, 2025), which explicitly implements paired t-test racing with α=0.2 for candidate selection. Even CAPO's paper does not cite F-Race or irace; it frames the racing procedure as a novel contribution rather than a 23-year-old technique from operations research.

This is worth noting. The absence is not just a citation oversight — it means the prompt-optimization community is re-deriving AutoML primitives (racing, successive halving, surrogate models, configuration-space sampling) under different names, without the theoretical scaffolding the AutoML community has already built. Pointing at the lineage is a small contribution in itself: it opens the door to importing decades of proof technique (sample complexity bounds, anytime convergence, portfolio construction) into prompt optimization.

### References

- Birattari, M., Stützle, T., Paquete, L., & Varrentrapp, K. (2002). *A racing algorithm for configuring metaheuristics.* GECCO.
- López-Ibáñez, M., Dubois-Lacoste, J., Pérez Cáceres, L., Stützle, T., & Birattari, M. (2016). *The irace package: iterated racing for automatic algorithm configuration.* Operations Research Perspectives, 3, 43–58.
- Hutter, F., Hoos, H. H., & Stützle, T. (2009). *ParamILS: an automatic algorithm configuration framework.* JAIR, 36, 267–306.
- Hutter, F., Hoos, H. H., & Leyton-Brown, K. (2011). *Sequential model-based optimization for general algorithm configuration.* LION.
- Li, L., Jamieson, K., DeSalvo, G., Rostamizadeh, A., & Talwalkar, A. (2017). *Hyperband: a novel bandit-based approach to hyperparameter optimization.* JMLR, 18, 1–52.
- Falkner, S., Klein, A., & Hutter, F. (2018). *BOHB: robust and efficient hyperparameter optimization at scale.* ICML.
- Akiba, T., Sano, S., Yanase, T., Ohta, T., & Koyama, M. (2019). *Optuna: a next-generation hyperparameter optimization framework.* KDD.
