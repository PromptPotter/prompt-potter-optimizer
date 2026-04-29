# Related Work

PromptPotter is an instance of **LLM-driven algorithm configuration**: a class of systems in which a language model proposes configurations of an artifact — source code, a prompt, a pipeline graph, a hyperparameter vector — an automatic evaluator scores each one on a problem instance distribution, and a search loop iterates on the population. The paradigm now spans from program synthesis at one extreme (AlphaEvolve discovering matrix-multiplication algorithms) to prompt-string optimization at the other (PromptWizard's mutate/score/refine on a single LLM call).

---

## Six systems under the umbrella

The systems below are flat siblings: each fits the same paradigm shape (LLM-in-the-loop search over a configuration space, evaluator-driven, population- or trajectory-based), and each instantiates that shape on a different combination of artifact, mutation operator, and selection rule.

| System | Target artifact | Search mechanism | Year / venue | Headline result |
|---|---|---|---|---|
| **AlphaEvolve** ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131)) | Source-code algorithms | LLM mutation (Gemini 2.0 Flash + Pro ensemble) over an evolutionary pool, code-execution evaluators ground every proposal | 2025 (DeepMind) | First improvement over Strassen for 4×4 complex matrix multiplication in 56 years; +0.7% compute recovered across Google data centers |
| **OpenEvolve** ([repo](https://github.com/algorithmicsuperintelligence/openevolve)) | Source code, prompts, hyperparameters | Open re-implementation of AlphaEvolve; LiteLLM-routed; explicit prompt-optimization domain support | 2025 (community) | Reported +23% on HotpotQA prompt evolution in repo examples |
| **PromptWizard** ([arXiv:2405.18369](https://arxiv.org/abs/2405.18369)) | Prompts (single LLM call) | Critique-guided mutate/score/refine | 2024 (Microsoft) | Cost-efficient single-prompt optimization; PromptPotter's direct loop ancestor |
| **MIPROv2** ([arXiv:2406.11695](https://arxiv.org/abs/2406.11695)) | Prompts + few-shot demos in DSPy programs | Bayesian optimization over instruction and demo bootstraps | 2024 (EMNLP, Stanford) | Up to +13% accuracy on Llama-3-8B |
| **GEPA** ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457)) | Prompts in DSPy programs | Reflective prompt evolution with trajectory feedback, tree of candidates | 2026 (ICLR Oral, Stanford) | +12% over MIPROv2 on AIME-2025; new SOTA on HotPotQA / HoVer / PUPA at the published settings |
| **PromptPotter** (this work) | Prompts (8-field decomposition) + per-node `pipeline_params` | Critique-guided L1→L2→L3 loop + Wilcoxon racing + cross-run SearchMemory | 2025 (independent) | Pipeline-aware optimization with statistical early-stopping; benchmarks: TermNorm, BBEH, AIME 2025 |

The umbrella **algorithm configuration** is borrowed from AutoML; in that community it has had a precise technical meaning for over two decades (see [`algorithm-configuration-lineage.md`](algorithm-configuration-lineage.md)). The six systems above use the LLM to do what F-Race / irace / SMAC do with numerical sampling models: propose configurations, observe per-instance evaluator outcomes, eliminate or refine. None of the six cite that lineage; the closest is CAPO, which implements paired t-test racing without naming F-Race as the precedent.

## Feature highlights

The capabilities that differentiate PromptPotter most sharply, with one column each for AlphaEvolve and the two prompt-tooling neighbors most often confused with PromptPotter (promptolution / `po`, promptfoo / `pf`).

| Capability | PP | AE | po | pf | How in PromptPotter |
|------------|:--:|:--:|:--:|:--:|---------------------|
| **Self-healing optimization** | 🟢 | 🟢 | 🔴 | 🔴 | L1-proposed values outside the declared allowed set are caught at parse time, scored 0 with no backend call, and fed to L2 as a self-healing signal. AlphaEvolve has the structural analogue (failed-eval signals re-enter the LLM mutation loop); PromptPotter is the only one in the prompt-tooling row that does this. See [../concepts/self-healing.md](../concepts/self-healing.md). |
| **Auto-injected scoring** | 🟢 | 🟢 | 🔴 | 🔴 | Per-dataset scoring formula from `campaign.json`, compiled once, injected into all eval paths. AlphaEvolve has the same property — code-execution evaluators are first-class. |
| **IDE-native operation** | 🟢 | 🔴 | 🔴 | 🔴 | `/potter-run` Claude Code skill — full campaign lifecycle from the terminal. AlphaEvolve is not publicised |
| **Prompt + pipeline optimization** | 🟢 | 🟢 | 🔴 | 🔴 | 8-field prompt decomposition + per-node `pipeline_params` — optimizes prompts AND pipeline config jointly. AlphaEvolve does the equivalent at the code level: it jointly mutates the algorithm and any tunable parameters embedded in it. |
| **Statistical early-stopping** | 🟢 | 🟢 | 🟡 | 🔴 | Sequential elimination via paired Wilcoxon signed-rank test + Holm-Bonferroni (α=0.05) after 20 queries. AlphaEvolve uses tournament + Pareto selection — different test, same role: drop dominated candidates without consuming the full budget. |
| **Cross-run learning** | 🟢 | 🟢 | 🔴 | 🔴 | SearchMemory — parameter impact, axis exhaustion, value trends, query tractability, failure-group × axis correlation. AlphaEvolve persists evolutionary populations across iterations; structurally equivalent at the umbrella level. |
| **Code optimization** | 🔴 | 🟢 | 🔴 | 🔴 | The single axis where AlphaEvolve is unambiguously stronger: its configuration space reaches into source code, while PromptPotter's stops at the prompts and parameters wrapping pre-built pipeline nodes. |

PromptPotter and AlphaEvolve are functionally equivalent across the existing capability rows — same statistical primitives, same evaluator-driven loop, same population dynamics, same structural answer to feedback compression. **Code optimization** is the single axis where AlphaEvolve is unambiguously stronger.

---

## Feature matrix


| Dimension | PromptPotter | promptolution (CAPO) | promptfoo | AlphaEvolve / OpenEvolve |
|-----------|-------------|---------------|-----------|--------------------------|
| **Language** | Python 3.13+ | Python 3.10–3.12 | TypeScript | Python (OpenEvolve); AlphaEvolve internal to Google |
| **Adoption** | Research/production tool | 126 stars (academic, AutoML group) | 19.9k stars, 300K+ users, acquired by OpenAI | OpenEvolve community-maintained, multiple forks; AlphaEvolve not released |
| **Core approach** | Critique-guided L1→L2→L3 loop | Evolutionary (GA, DE) + LLM-as-optimizer (OPRO) + hybrid (CAPO) | Manual A/B testing (human writes all variants) | LLM-mutated evolutionary pool over source code, grounded by automatic evaluators |
| **Optimization target** | prompts, node-parameters, hyperparameters | Prompts (single instruction string) + few-shot demos | Prompts (manual variants) | Source-code algorithms; OpenEvolve also: prompts, hyperparameters |
| **Multi-step pipeline** | Per-node params, PipelineSchema from backend | No — single LLM call only | Single LLM call (custom script for multi-step) | Not applicable — target is the program, not a pipeline of LLM calls |
| **Budget control** | `sp_budget_ttest` (adaptive), early-stopping | Token budget callback | `maxConcurrency`, `repeat`, `timeoutMs` | Compute-based; population-size + iteration count |
| **Scoring** | Composite formula, custom per-dataset | `accuracy_score`, reward function, LLM-as-judge | 40+ assertion types (deterministic + model-graded) | Code-execution evaluator(s) — domain-specific, deterministic |
| **Candidate selection** | Sequential elimination, Wilcoxon signed-rank early-stop | CAPO: paired t-test racing (α=0.2). Others: full eval or subsampling | Pass/fail assertions, weighted aggregation | Tournament / Pareto over evaluator scores |
| **Cross-run learning** | SearchMemory (parameter impact, axis exhaustion, failure groups) | None (in-memory only, lost on exit) | None (each eval independent) | Population persistence across iterations; no cross-run memory in published descriptions |
| **Few-shot optimization** | Pipeline-level (backend handles examples) | CAPO: joint instruction + few-shot optimization | Not applicable (manual) | Not applicable — programs aren't few-shot prompted |
| **Prompt representation** | 8-field decomposition | Opaque string (monolithic instruction) | Opaque string templates (Nunjucks) | Source code (AlphaEvolve); strings/code/configs (OpenEvolve) |
| **Red teaming** | — | — | 50+ vulnerability types, dedicated pipeline | — |
| **RAG / agent metrics** | — | — | context-faithfulness/-recall/-relevance, trajectory assertions | — |
| **Persistence** | Two-tier (session + campaign store), content-addressed archival | None (in-memory; FileOutputCallback writes parquet/csv post-hoc) | Disk cache for LLM responses only | Population checkpoints between iterations |
| **Provider ecosystem** | Backend-agnostic (single BackendClient endpoint) | OpenAI-compatible API, HuggingFace local, vLLM | 50+ built-in (OpenAI, Anthropic, Groq, Bedrock, etc.) | Gemini ensemble (AlphaEvolve); LiteLLM-routed (OpenEvolve) |
| **CI/CD** | — | — | GitHub Actions, GitLab, Jenkins, Azure Pipelines, etc. | — |

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

### Matchup 6 — AlphaEvolve on classical algorithm targets (AlphaEvolve paper)

**Source:** [arXiv:2506.13131](https://arxiv.org/abs/2506.13131) (May 2025) | **Inference:** Gemini 2.0 Flash + Gemini 2.0 Pro ensemble | **Targets:** matrix multiplication, kissing-number bounds, data-center scheduling

| Target | Prior best | AlphaEvolve | Note |
|--------|-----------|-------------|------|
| 4×4 complex matrix multiplication | 49 scalar mults (Strassen, 1969) | **48 scalar mults** | First improvement in 56 years |
| Google data-center scheduling heuristic | (proprietary baseline) | **+0.7% compute recovered** | Continuously deployed |
| Various open math problems | various | improved or matched on a spectrum | Detailed in paper |


---

## Key papers and venues

| Paper | Venue | Year | Category |
|-------|-------|------|----------|
| [APE](https://arxiv.org/abs/2211.01910) | ICLR | 2023 | LLMs as prompt engineers (foundational) |
| [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) | Nature | 2024 | LLM-driven program search (AlphaEvolve's predecessor) |
| [TextGrad](https://arxiv.org/abs/2406.07496) | Nature | 2024 | Compound system optimization |
| [Trace/OPTO](https://arxiv.org/abs/2406.16218) | NeurIPS | 2024 | Workflow optimization |
| [MIPROv2](https://arxiv.org/abs/2406.11695) | EMNLP | 2024 | Multi-stage prompt optimization |
| [PromptWizard](https://arxiv.org/abs/2405.18369) | — | 2024 | Critique-guided prompt optimization |
| [AFlow](https://arxiv.org/abs/2410.10762) | ICLR (Oral) | 2025 | Workflow architecture search |
| [ADAS](https://arxiv.org/abs/2408.08435) | ICLR | 2025 | Agent architecture search |
| [AlphaEvolve](https://arxiv.org/abs/2506.13131) | — | 2025 | LLM-driven program evolution |
| [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) | — | 2025 | Open re-implementation of AlphaEvolve; multi-target (code + prompts + hyperparameters) |
| [metaTextGrad](https://arxiv.org/abs/2505.18524) | NeurIPS | 2025 | Meta-optimization |
| [GEPA](https://arxiv.org/abs/2507.19457) | ICLR (Oral) | 2026 | Reflective prompt evolution |
| [CAPO/promptolution](https://arxiv.org/abs/2512.02840) | — | 2025 | Evolutionary prompt optimization |
| [AdalFlow](https://arxiv.org/abs/2501.16673) | — | 2025 | LLM AutoDiff (compound systems) |
| [Optimas](https://arxiv.org/abs/2507.03041) | ICLR | 2026 | Multi-component compound systems |
| [Survey](https://arxiv.org/abs/2506.08234) | EMNLP | 2025 | Compound AI optimization taxonomy |

---

## Adjacent work

Three nearby threads do not fit the umbrella as cleanly but inform the design.

- **Workflow architecture search.** AFlow ([arXiv:2410.10762](https://arxiv.org/abs/2410.10762)), ADAS ([arXiv:2408.08435](https://arxiv.org/abs/2408.08435)), Trace ([arXiv:2406.16218](https://arxiv.org/abs/2406.16218)), and AdalFlow ([arXiv:2501.16673](https://arxiv.org/abs/2501.16673)) optimize the *graph* of LLM calls rather than the parameters of a fixed graph. The configuration space is structurally different (edges, node identities) but the search-loop primitives are the same.
- **LLM-as-optimizer foundations.** APE ([arXiv:2211.01910](https://arxiv.org/abs/2211.01910)) and OPRO are the foundational instances of using an LLM to propose prompts; TextGrad ([arXiv:2406.07496](https://arxiv.org/abs/2406.07496)) lifts gradient-style backpropagation onto natural-language critique. They predate the umbrella as a coherent framing but seed every system above.
- **Compound-system surveys and meta-optimization.** The [Compound AI Systems Optimization survey](https://arxiv.org/abs/2506.08234) (EMNLP 2025) catalogues the compound-system branch; metaTextGrad ([arXiv:2505.18524](https://arxiv.org/abs/2505.18524)) and Optimas ([arXiv:2507.03041](https://arxiv.org/abs/2507.03041)) push toward second-order optimization of optimizers themselves.
- **Karpathy's AutoResearch.** [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) (March 2026) applies the same LLM-driven-evolution idea to ML training code rather than prompts: a single agent edits `train.py`, trains 5 min on a single-GPU nanochat setup, keeps or reverts. PromptPotter generalizes that loop with population search, statistical elimination, and L2/L3 meta-strategy — structurally, AutoResearch is the `n_variants=1` / no-L2-L3 / no-elimination degenerate case applied to a code-execution backend. Detailed mapping in the project README under "Relation to Karpathy's AutoResearch".

