# Related Work

PromptPotter is an instance of **LLM-driven algorithm configuration**: a class of systems in which a language model proposes configurations of an artifact — source code, a prompt, a pipeline graph, a hyperparameter vector — an automatic evaluator scores each one on a problem instance distribution, and a search loop iterates on the population. The paradigm now spans from program synthesis at one extreme (AlphaEvolve discovering matrix-multiplication algorithms) to prompt-string optimization at the other (PromptWizard's mutate/score/refine on a single LLM call).

---

## Eight systems under the umbrella

The systems below are flat siblings: each fits the same paradigm shape (LLM-in-the-loop search over a configuration space, evaluator-driven, population- or trajectory-based), and each instantiates that shape on a different combination of artifact, mutation operator, and selection rule.

| System | Target artifact | Search mechanism | Year / venue | Headline result |
|---|---|---|---|---|
| **AlphaEvolve** ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131)) | Source-code algorithms | LLM mutation (Gemini 2.0 Flash + Pro ensemble) over an evolutionary pool, code-execution evaluators ground every proposal | 2025 (DeepMind) | First improvement over Strassen for 4×4 complex matrix multiplication in 56 years; +0.7% compute recovered across Google data centers |
| **OpenEvolve** ([repo](https://github.com/algorithmicsuperintelligence/openevolve)) | Source code, prompts, hyperparameters | Open re-implementation of AlphaEvolve; LiteLLM-routed; explicit prompt-optimization domain support | 2025 (community) | Circle packing n=26 matches AlphaEvolve SOTA (sum-of-radii 2.635); MLX kernel 2.8x; HotpotQA prompt evolution +10.69% multi-hop |
| **AlgoTuner** ([arXiv:2507.15887](https://arxiv.org/abs/2507.15887), [algotune.io](https://algotune.io/)) | Algorithm source code (Python, vs SciPy / scikit-learn / CVXPY reference solvers) | LLM agentic loop with profile-guided feedback; single trajectory, no population, no crossover. Ships the **AlgoTune** benchmark (154 numerical-programming tasks + correctness/timing harness + public leaderboard) | 2025 (NeurIPS, Tübingen + Princeton + Anthropic) | 1.72x avg speedup (paper); 2.05x harmonic mean (GPT-5.2 leaderboard); 681x peak on `cyclic_independent_set` |
| **AutoResearch** ([repo](https://github.com/karpathy/autoresearch)) | ML training code (`train.py`) | Single-agent edit-keep-revert against a fixed 5-min nanochat training loss; meta-instructions in `program.md` | 2026 (Karpathy) | Minimal single-agent proof-of-concept; the `n_variants=1` / no-elimination / no-L2-L3 degenerate case of the umbrella, applied to a code-execution backend |
| **PromptWizard** ([arXiv:2405.18369](https://arxiv.org/abs/2405.18369)) | Prompts (single LLM call) | Critique-guided mutate/score/refine | 2024 (Microsoft) | Cost-efficient single-prompt optimization; PromptPotter's direct loop ancestor |
| **MIPROv2** ([arXiv:2406.11695](https://arxiv.org/abs/2406.11695)) | Prompts + few-shot demos in DSPy programs | Bayesian optimization over instruction and demo bootstraps | 2024 (EMNLP, Stanford) | Up to +13% accuracy on Llama-3-8B |
| **GEPA** ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457)) | Prompts in DSPy programs | Reflective prompt evolution with trajectory feedback, tree of candidates | 2026 (ICLR Oral, Stanford) | +12% over MIPROv2 on AIME-2025; new SOTA on HotPotQA / HoVer / PUPA at the published settings |
| **PromptPotter** (this work) | Prompts (8-field decomposition) + per-node `pipeline_params` | Critique-guided L1→L2→L3 loop + Bayesian PoBB best-arm-ID + cross-run SearchMemory | 2025 (independent) | Pipeline-aware optimization with population-aware statistical early-stopping; benchmarks: TermNorm, BBEH, AIME 2025 |

### Benchmark hygiene across the umbrella

The published numbers in the table above are not on equal footing. **OpenEvolve** — the most directly comparable open system — reports per-task speedups with `seed=42` only, no variance across seeds, no held-out test split, and baselines mixed between published SOTA (circle packing matches AlphaEvolve at 2.635) and naive in-repo references (MLX kernel 2.8x, function-min 100x, HotpotQA +10.69%). Default iteration budgets are 50–200 with cost framed in dollars per iter ($0.01–0.60). **AlphaEvolve** sets the methodological ceiling: peer-reviewed deployment numbers and Pareto fronts for the matrix-multiplication result. **AlgoTune** (the benchmark, not the agent) ships the cleanest harness — 154 tasks with reference solvers, correctness verification, wall-clock profiling, and a public leaderboard — and is reused as an evaluation suite by OpenEvolve.

**Takeaway for PromptPotter's benchmark track:** multi-seed runs, train/test split discipline, and explicit lift-over-reference reporting are a credibility gap the FunSearch / AlphaEvolve / OpenEvolve line has not closed for prompt-tooling-relevant tasks. Closing it is cheap — it is mostly bookkeeping on the existing PoBB ledger — and load-bearing for the BBEH and AIME tracks in [`benchmarks.md`](benchmarks.md).

The umbrella **algorithm configuration** is borrowed from AutoML; in that community it has had a precise technical meaning for over two decades (see [`algorithm-configuration-lineage.md`](algorithm-configuration-lineage.md)). The six systems above use the LLM to do what F-Race / irace / SMAC do with numerical sampling models: propose configurations, observe per-instance evaluator outcomes, eliminate or refine. None of the six cite that lineage; the closest is CAPO, which implements paired t-test racing without naming F-Race as the precedent.

## Feature highlights

The capabilities that differentiate PromptPotter most sharply, with one column each for AlphaEvolve and the two prompt-tooling neighbors most often confused with PromptPotter (promptolution / `po`, promptfoo / `pf`).

| Capability | PP | AE | po | pf | How in PromptPotter |
|------------|:--:|:--:|:--:|:--:|---------------------|
| **Self-healing optimization** | 🟢 | 🟢 | 🔴 | 🔴 | L1-proposed values outside the declared allowed set are caught at parse time, scored 0 with no backend call, and fed to L2 as a self-healing signal. AlphaEvolve has the structural analogue (failed-eval signals re-enter the LLM mutation loop); PromptPotter is the only one in the prompt-tooling row that does this. See [../concepts/self-healing.md](../concepts/self-healing.md). |
| **Auto-injected scoring** | 🟢 | 🟢 | 🔴 | 🔴 | Per-dataset scoring formula from `campaign.json`, compiled once, injected into all eval paths. AlphaEvolve has the same property — code-execution evaluators are first-class. |
| **IDE-native operation** | 🟢 | 🔴 | 🔴 | 🔴 | `/potter-run` Claude Code skill — full campaign lifecycle from the terminal. AlphaEvolve is not publicised |
| **Prompt + pipeline optimization** | 🟢 | 🟢 | 🔴 | 🔴 | 8-field prompt decomposition + per-node `pipeline_params` — optimizes prompts AND pipeline config jointly. AlphaEvolve does the equivalent at the code level: it jointly mutates the algorithm and any tunable parameters embedded in it. |
| **Statistical early-stopping** | 🟢 | 🟢 | 🟡 | 🔴 | Bayesian Posterior-of-Being-Best (Russo 2016): per-query joint Normal-CLT posterior over candidate accuracy means, MC over independent Normals, stop a candidate when its `P(round-best) < ε` (default 0.05). Population-aware (uses joint posterior across all candidates) and variance-adaptive. AlphaEvolve uses tournament + Pareto selection — different test, same role: drop dominated candidates without consuming the full budget. |
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
| **Candidate selection** | Sequential elimination via Bayesian Posterior-of-Being-Best (Russo 2016): population-aware joint posterior, ε-threshold stop | CAPO: paired t-test racing (α=0.2). Others: full eval or subsampling | Pass/fail assertions, weighted aggregation | Tournament / Pareto over evaluator scores |
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
| Origin | 42.33 | — | — |
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
| Google data-center scheduling heuristic | (proprietary origin) | **+0.7% compute recovered** | Continuously deployed |
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

---

## Best-arm identification & sequential testing

PromptPotter's mid-round abortion mechanism is an instance of the **best-arm identification** (BAI) problem in stochastic multi-armed bandits: given a fixed population of arms (round candidates) and a per-pull noisy reward signal (per-query score), identify the arm with highest mean reward at minimum sample cost. The BAI literature splits along three axes — fixed-budget vs fixed-confidence, frequentist vs Bayesian, pairwise vs population — and PromptPotter's choice (Bayesian, population, fixed-confidence-flavored via ε) sits in one cell of that grid.

| Algorithm | Family | What it does | Why it's not what we use |
|---|---|---|---|
| **Wilcoxon signed-rank + Holm-Bonferroni** ([Wilcoxon 1945](https://www.jstor.org/stable/3001968)) | Frequentist, paired, pairwise | Tests, for each pair (current, prior), whether the prior dominates; Holm controls family-wise error | What PromptPotter used until this revision. Pairwise (no joint distribution), variance-agnostic (rank-based); strictly weaker than PoBB on the high-signal regime. Retired. |
| **LUCB** ([Kalyanakrishnan et al. 2012](https://icml.cc/2012/papers/359.pdf)) | Frequentist, pairwise, fixed-confidence | Maintain Hoeffding/Bernstein lower-upper confidence bounds per arm; eliminate when an arm's UCB falls below another's LCB | Pairwise — only ever compares to the current leader. Empirical Bernstein adds variance-awareness but PoBB's joint posterior subsumes that and reads more naturally. |
| **Bayes-UCB** ([Kaufmann et al. 2012](https://hal.science/hal-00738209)) | Bayesian, pairwise | UCB-style elimination using posterior quantiles instead of Hoeffding bounds | Same pairwise limitation. |
| **Top-Two Thompson Sampling** ([Russo 2016](https://arxiv.org/abs/1602.08448)) | Bayesian, population | Sample posterior; allocate budget to top two by posterior probability of being best | The population-aware family PoBB belongs to. PoBB drops the budget-allocation half (we don't choose which candidate to query — the loop iterates them deterministically) and keeps the stop rule. |
| **Successive Rejects** ([Audibert et al. 2010](https://hal.archives-ouvertes.fr/hal-00654404)) | Frequentist, population, fixed-budget | Phased elimination: at the end of each phase, drop the bottom-by-mean | Doesn't adapt within phase — clearly broken candidates run to phase boundary. PoBB stops them as soon as `P(best) < ε`. |
| **Sequential Halving** ([Karnin et al. 2013](https://proceedings.mlr.press/v28/karnin13.html)) | Frequentist, population, fixed-budget | log K phases; drop bottom half each | Same within-phase limitation. |
| **Hoeffding Races** ([Maron & Moore 1993](https://papers.nips.cc/paper/799-hoeffding-races-accelerating-model-selection-search-for-classification-and-function-approximation)) | Frequentist, pairwise, fixed-confidence | Eliminate via Hoeffding-bound non-overlap | Pairwise; variance-agnostic; predates Bernstein-tight bounds. |
| **OCBA** ([Chen 2000](https://www.jstor.org/stable/2697075)) | Bayesian, population, fixed-budget | Allocate next pulls to maximize posterior probability of correct selection | Optimal-allocation focus; same population-aware family as PoBB, addresses budget allocation rather than stop rules. |
| **PoBB (this work)** | Bayesian, population, fixed-confidence | Per-query joint Normal-CLT posterior over candidate accuracy means; MC argmax; stop when `P(c is best) < ε` | What PromptPotter uses. ~60 LOC; single tunable `ε`; operator-readable per-query (display the probabilities). See [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md). |

The choice of population-aware over pairwise is dictated by what we want to know: not "is the current candidate worse than each individual prior?" but "is the current candidate the round winner?" That second question depends on the *joint* shape of all candidates' posteriors, which only the population-aware family captures.

The choice of Bayesian over frequentist is dictated by interpretability: P(c is best) is one number per candidate that an operator can read at a glance ("c042 73% probability of winning round"); a Holm-corrected p-value or a Hoeffding bound is not.

The choice of fixed-confidence (ε threshold) over fixed-budget (Successive Rejects / Sequential Halving) is dictated by adaptivity: we want clearly-broken candidates to stop within 3–5 queries in the early-round high-signal regime, and we want indistinguishable candidates to run to budget cap in the late-round low-signal regime. Phased fixed-budget algorithms can't do the first.

For the implementation, two-regime analysis, tunable knobs, open questions, and the rationale for replacing Wilcoxon, see [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

### Comparison to MCTS

PromptPotter and MCTS share the genus *tree search guided by statistical confidence*: both expand a search tree via a confidence rule (UCB1 for MCTS, [PoBB](#best-arm-identification--sequential-testing) for us). We diverge on three of MCTS's four phases. **(1) Simulation.** MCTS scores a new leaf via random rollout to terminal; we score via deterministic forward pass on a fixed eval set. AlphaZero is the published precedent for that swap — it replaces rollouts with a learned value network — so the determinism is not what makes us not-MCTS. **(2) Backpropagation.** MCTS propagates visit count + cumulative reward to ancestor nodes so future selection sees signal from descendants; we don't — only the current round's siblings compete, ancestor stats do not update from descendant outcomes. **(3) Selection.** MCTS uses UCB1 to descend from root every iteration, picking which node to expand; we always expand the latest round-winner (one-armed search — no rewind, no choice).

**Aspiration — would AlphaZero-shaped MCTS be categorically better?** Partially yes. The unlocked capability is **recovery from dead-end branches**: a sequence of L1/L2/L3 fires that exhausts the current trajectory today simply ends the cycle; under MCTS-shaped selection, L3 could rewind to a deferred ancestor and seed a new subtree from there. UCB1's regret bounds also give asymptotic guarantees that one-armed best-arm-ID over a single round does not. Partially no: MCTS's full apparatus was built for game tree search with cheap random rollouts; our "rollout" cost is one full round of LLM calls per candidate, so any UCB rule we adopt has to be deeply sample-efficient (closer in spirit to AlphaZero's PUCT than to AlphaGo's vanilla UCT). Backpropagation semantics also need care — averaging descendant scores up a lineage where each descendant ran under a different `task_context` is not trivially meaningful. **Bridge to make it happen — three steps**: **(1) selection** ✓ *shipped (observation-only)*: L3 may now emit `fork_proposal: {round_offset, reason}` alongside its `plan` rewrite. The proposal is written to `round_NNNN.json::nodes[l3_plan].exit.fork_proposal`; the operator reads it and forks manually via `resume --from N`. No automatic fork yet — empirical evidence on whether L3 uses the lever in the wild gates the harder follow-ups. **(2) backpropagation** *(M13+)*: persist round outcomes as node-stats up the lineage tree. **(3) UCB rule** *(M13+)*: rule for L3's ancestor pick + automatic fork. Today we are *best-arm identification over a one-armed tree, with a manual fork-proposal channel*; the M13+ aspiration is **AlphaZero-shaped MCTS over the lineage tree**.

