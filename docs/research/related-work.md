# Related Work

PromptPotter is an instance of **LLM-driven algorithm configuration**: a class of systems in which a language model proposes configurations of an artifact — source code, a prompt, a pipeline graph, a hyperparameter vector — an automatic evaluator scores each one on a problem instance distribution, and a search loop iterates on the population. The paradigm now spans from program synthesis at one extreme (AlphaEvolve discovering matrix-multiplication algorithms) to prompt-string optimization at the other (PromptWizard's mutate/score/refine on a single LLM call).

---

## Systems under the umbrella

The systems below are flat siblings: each fits the same paradigm shape (LLM-in-the-loop search over a configuration space, evaluator-driven, population- or trajectory-based), and each instantiates that shape on a different combination of artifact, mutation operator, and selection rule.

| System | Target artifact | Search mechanism | Year / venue | Headline result |
|---|---|---|---|---|
| **SkillOpt** ([Microsoft, 2026](https://github.com/microsoft/SkillOpt)) | Agent *skills* — a compact natural-language skill doc treated as the frozen agent's trainable state | Rollout → Reflect → Edit → **validation-gate**: a separate optimizer model reads trajectories, proposes bounded add/delete/replace edits, and adopts a candidate only if it *strictly* beats the current skill on a held-out split (rejected-edit feedback + slow/meta updates guard against prompt drift) | 2026 (Microsoft, May) | Best-or-tied on **all 52 cells** — 6 agent benchmarks (SearchQA, SpreadsheetBench, OfficeQA, DocVQA, LiveMath, ALFWorld) × 7 targets (GPT-5.5 → Qwen3.5-4B) × 3 modes (direct / Codex / Claude Code) — over human / one-shot-LLM / Trace2Skill / TextGrad / GEPA / EvoSkill skills; frozen-model, harness-portable, `v0.1.0` on PyPI. **The closest open neighbour to PromptPotter's frozen-model thesis.** |
| **DarwinX** ([arXiv:2608.07545](https://arxiv.org/abs/2608.07545)) | Agent harnesses — prompts, tools, skills and control flow, model frozen | Selection over a *population* of harnesses under a **preserve-and-extend contract** (a variant is admitted only if it extends coverage without regressing) + an **archive of alternative lineages** kept for recombination; failure-, teacher- and self-derived evidence share one edit interface. Fitness comes from each benchmark's own verifier — no gold solutions | 2026 (arXiv, Jul) | ~17 points average per loop: Terminal-Bench 2.1 +7.7 → 83.2% on a matched base (84.7% on a stronger one), TerminalWorld held-out 68.3%, WebArena-Infinity pass@1 43.5% → 93.0%, and a Terminal-Bench harness transfers unchanged to SWE-bench Verified. **States the one-armed-search critique this repo uses as a differentiator, and answers it with an archive rather than a tree.** |
| **AutoDesign** ([arXiv:2608.13560](https://arxiv.org/abs/2608.13560)) | An agent harness for long-horizon design work, edited *as code* by a code agent | An outer optimizer reads rollout feedback and directs the code agent to rewrite the harness recursively (their term for the outer half is "meta-harness"; ours is outer/inner) | 2026 (Aug) | 78.32 on the PosterBench Main Track it introduces (+7.45 over the single closed baseline it compares against); the learned harness moves 54.99 → 67.39. One baseline only — thin by the hygiene bar the next section argues for |
| **autoresearch** (Karpathy) / **NVIDIA AutoResearch** ([repo](https://github.com/karpathy/autoresearch) · [NVIDIA](https://developer.nvidia.com/blog/how-to-run-an-autoresearch-workflow-with-rl-agent-skills-and-nvidia-nemo/)) | ML training code + config (`train.py`, NeMo Gym env) — **not** prompts or pipeline params | Open-ended single-agent edit → run → keep/revert; NVIDIA wraps the *same* framework with **NeMo RL + NeMo Gym** (SFT / GRPO / DPO) and three agent skills (brev-etiquette, session-memory, autoresearch) | 2026 (Karpathy; NVIDIA) | Karpathy: minimal PoC, the `n_variants=1` / no-population / no-L2-L3 degenerate case. NVIDIA: same loop driving real training runs — one demo 25% → 96.9% (LoRA SFT). It mutates *training code*; a run's output can be new **weights**, so it sits at the umbrella's training-spend end and does **not** do prompt / pipeline optimization. |
| **GEPA** ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457)) | Prompts in DSPy programs | Reflective prompt evolution with trajectory feedback, tree of candidates | 2025 (arXiv Jul; ICLR 2026 Oral, Stanford) | +12% over MIPROv2 on AIME-2025 and beats GRPO with far fewer rollouts; new SOTA on HotPotQA / HoVer / PUPA at the published settings |
| **AlphaEvolve** ([arXiv:2506.13131](https://arxiv.org/abs/2506.13131)) | Source-code algorithms | LLM mutation (Gemini 2.0 Flash + Pro ensemble) over an evolutionary pool, code-execution evaluators ground every proposal | 2025 (DeepMind); **GA 10 Jul 2026** | First improvement over Strassen for 4×4 complex matrix multiplication in 56 years; +0.7% compute recovered across Google data centers. **Now publicly available** on Google Cloud's Gemini Enterprise Agent Platform — see § The umbrella ships as product for access |
| **AlgoTuner** ([arXiv:2507.15887](https://arxiv.org/abs/2507.15887), [algotune.io](https://algotune.io/)) | Algorithm source code (Python, vs SciPy / scikit-learn / CVXPY reference solvers) | LLM agentic loop with profile-guided feedback; single trajectory, no population, no crossover. Ships the **AlgoTune** benchmark (154 numerical-programming tasks + correctness/timing harness + public leaderboard) | 2025 (NeurIPS, Tübingen + Princeton + Anthropic) | 1.72x avg speedup (paper); 2.05x harmonic mean (GPT-5.2 leaderboard); 681x peak on `cyclic_independent_set` |
| **OpenEvolve** ([repo](https://github.com/algorithmicsuperintelligence/openevolve)) | Source code, prompts, hyperparameters | Open re-implementation of AlphaEvolve; LiteLLM-routed; explicit prompt-optimization domain support | 2025 (community) | Circle packing n=26 matches AlphaEvolve SOTA (sum-of-radii 2.635); MLX kernel 2.8x; HotpotQA prompt evolution +10.69% multi-hop |
| **PromptPotter** (this work) | Prompts (8-field decomposition) + per-node `pipeline_params` | Critique-guided L1→L2→L3 loop + Bayesian PoBB best-arm-ID + cross-run SearchMemory | 2025 (independent) | Pipeline-aware optimization with population-aware statistical early-stopping; benchmarks: TermNorm, BBEH, AIME 2025 |
| **MIPROv2** ([arXiv:2406.11695](https://arxiv.org/abs/2406.11695)) | Prompts + few-shot demos in DSPy programs | Bayesian optimization over instruction and demo bootstraps | 2024 (EMNLP, Stanford) | Up to +13% accuracy on Llama-3-8B |
| **PromptWizard** ([arXiv:2405.18369](https://arxiv.org/abs/2405.18369)) | Prompts (single LLM call) | Critique-guided mutate/score/refine | 2024 (Microsoft) | Cost-efficient single-prompt optimization; PromptPotter's direct loop ancestor |

### Benchmark hygiene across the umbrella

The published numbers in the table above are not on equal footing. **OpenEvolve** — the most directly comparable open system — reports per-task speedups with `seed=42` only, no variance across seeds, no held-out test split, and baselines mixed between published SOTA (circle packing matches AlphaEvolve at 2.635) and naive in-repo references (MLX kernel 2.8x, function-min 100x, HotpotQA +10.69%). Default iteration budgets are 50–200 with cost framed in dollars per iter ($0.01–0.60). **AlphaEvolve** sets the methodological ceiling: peer-reviewed deployment numbers and Pareto fronts for the matrix-multiplication result. **AlgoTune** (the benchmark, not the agent) ships the cleanest harness — 154 tasks with reference solvers, correctness verification, wall-clock profiling, and a public leaderboard — and is reused as an evaluation suite by OpenEvolve.

**Takeaway for PromptPotter's benchmark track:** multi-seed runs, train/test split discipline, and explicit lift-over-reference reporting are a credibility gap the FunSearch / AlphaEvolve / OpenEvolve line has not closed for prompt-tooling-relevant tasks. Closing it is cheap — it is mostly bookkeeping on the existing PoBB ledger — and load-bearing for the BBEH and AIME tracks in [`benchmarks.md`](benchmarks.md).

### Agent-harness evolution — the crowded half of the umbrella

Three systems in four months evolve a **harness** for a frozen model: SkillOpt (May), DarwinX (Jul), AutoDesign (Aug). Freeze the weights and evolve what wraps them is therefore an occupied thesis, not a differentiator — and DarwinX states the *single-lineage search is path-dependent* critique in its abstract — the argument this repo had been using as its own differentiator, retired from [`../../README.md`](../../README.md) for exactly that reason. Two consequences, opposite in sign:

- **None is a runnable opponent here.** All three score on agent/tool environments — ALFWorld, SpreadsheetBench, Terminal-Bench, WebArena, PosterBench — which sit outside the connector boundary and PoBB's cost model, the rejection class [`benchmarks.md`](benchmarks.md) already applies. A head-to-head means building an agent harness first, not wiring a dataset. They contest the narrative, not the measurement.
- **The statistical layer is what none of them occupy.** DarwinX's preserve-and-extend contract is a deterministic admission gate; no system in this group runs sequential elimination, a cost-per-fitness model, or a subset-invariant ability scale. That, plus joint prompt + pipeline-parameter search, is where the distinguishing claim now has to sit.

One capability they have and PromptPotter does not: DarwinX takes fitness from each benchmark's own verifier, so it needs no labelled rows — where a labelled dataset is a stated hard limitation of ours.

Evolving an agent harness is a post-v1 target here → [`../specs/roadmap.md`](../specs/roadmap.md) § Evolving agent harnesses.

### The umbrella ships as product

Two entries above have left the paper stage. **AlphaEvolve** went generally available on Google Cloud's Gemini Enterprise Agent Platform on 10 July 2026 (private preview December 2025); the seven months bought customer evidence rather than a new mechanism — Klarna 2× ML-training throughput (~6,000 candidates in three weeks), PacBio −30% DNA-variant-detection error, JetBrains +15–20% IDE performance, and Google's own Spanner team −20% write amplification on already-tuned infrastructure.

**Palantir AIP Evolve** ("Automated AI Value Maximization," built on AIP Evals) is the closest commercial analogue to PromptPotter's own shape. It evolves a *pipeline* of LLM "Blocks" — e.g. a per-block model swap GPT-5.1 → GPT-5.4-nano — against a **multi-metric** score (compute cost / latency / quality; −97% / −69% / +7pp in the shipped demo), on an **iteration + holdout split** (20 / 10 of real production orders), behind a **human-review gate** ("Safe to proceed / Awaiting review / High confidence"). Pipeline-node config, multi-objective scoring, train/test discipline, and a self-healing review gate — the properties this doc argues distinguish PromptPotter — are now shipping inside an enterprise product, not just a research prototype. We characterize AIP Evolve here rather than grading it in the sibling table above: it is the closest commercial neighbour to PromptPotter's shape, but its search internals and per-metric baselines are not publicly disclosed, so a head-to-head table cell would be guesswork.

The umbrella **algorithm configuration** is borrowed from AutoML; in that community it has had a precise technical meaning for over two decades (detail below, § Algorithm configuration: the classical lineage). The systems above use the LLM to do what F-Race / irace / SMAC do with numerical sampling models: propose configurations, observe per-instance evaluator outcomes, eliminate or refine. None of them cite that lineage; the closest is CAPO, which implements paired t-test racing without naming F-Race as the precedent.

## Feature highlights

The capabilities that differentiate PromptPotter most sharply, with one column each for AlphaEvolve and the two prompt-tooling neighbors most often confused with PromptPotter (promptolution / `po`, promptfoo / `pf`).

| Capability | PP | AE | po | pf | How in PromptPotter |
|------------|:--:|:--:|:--:|:--:|---------------------|
| **Self-healing optimization** | 🟢 | 🟡 | 🔴 | 🔴 | L1-proposed values outside the declared allowed set are caught at parse time, scored 0 with no backend call, and fed to **L2 — a different layer, which rewrites the proposing layer's prompt**. AlphaEvolve re-enters failed-eval signals into the *same* mutation prompt, and has no declared allowed-set to violate (its search space is code), so the analogue is partial, not equivalent: it discards, it does not teach. See [../developer/self-healing-internals.md](../developer/self-healing-internals.md). |
| **Auto-injected scoring** | 🟢 | 🟢 | 🔴 | 🔴 | Per-dataset scoring formula from `campaign.json`, compiled once, injected into all eval paths. AlphaEvolve has the same property — code-execution evaluators are first-class. |
| **IDE-native operation** | 🟢 | 🔴 | 🔴 | 🔴 | `/potter-run` Claude Code skill — full campaign lifecycle from the terminal. AlphaEvolve is not publicised |
| **Prompt + pipeline optimization** | 🟢 | 🟢 | 🔴 | 🔴 | 8-field prompt decomposition + per-node `pipeline_params` — optimizes prompts AND pipeline config jointly. AlphaEvolve does the equivalent at the code level: it jointly mutates the algorithm and any tunable parameters embedded in it. |
| **Statistical early-stopping** | 🟢 | 🟢 | 🟡 | 🔴 | Bayesian Posterior-of-Being-Best (Russo 2016): per-query joint Normal-CLT posterior over candidate accuracy means, MC over independent Normals, stop a candidate when its `P(round-best) < ε` (default 0.05). Population-aware (uses joint posterior across all candidates) and variance-adaptive. AlphaEvolve uses tournament + Pareto selection — different test, same role: drop dominated candidates without consuming the full budget. |
| **Cross-run learning** | 🟢 | 🟢 | 🔴 | 🔴 | SearchMemory — parameter impact, axis exhaustion, value trends, query tractability, failure-group × axis correlation. AlphaEvolve persists evolutionary populations across iterations; structurally equivalent at the umbrella level. |
| **Building-block library** | 🟢 | 🟢 | 🔴 | 🔴 | Two libraries, and they answer different questions. The MeasurementArchive carries *statistics about* candidates; `config/prompt_variants.json` carries the candidates' *material* — reusable persona / task_intent / thinking_style / answer_format blocks (adopted from PromptWizard, whose thinking styles are the Self-Discover reasoning modules, plus our own runs), rendered to `l1_generate` as the `prompt_block_catalogue` injection so L1 reuses and recombines rather than always re-inventing. `OptimizationConfig.prompt_block_catalogue`: `guidance` \| `restrict` \| `off`. AlphaEvolve's analogue is its program database — a library of past *programs* to recombine. (This is the row a "library learning" framing is reaching for; that term is DreamCoder's, not AlphaEvolve's, and is not used here.) |
| **Code optimization** | 🔴 | 🟢 | 🔴 | 🔴 | The single axis where AlphaEvolve is unambiguously stronger: its configuration space reaches into source code, while PromptPotter's stops at the prompts and parameters wrapping pre-built pipeline nodes. |

PromptPotter and AlphaEvolve share the same statistical primitives, the same evaluator-driven loop, and the same population dynamics. They part on two axes, in opposite directions: **code optimization** is where AlphaEvolve is unambiguously stronger (its configuration space reaches into source; PromptPotter's stops at the prompts and parameters wrapping pre-built nodes), and **self-healing** is where PromptPotter is (a rejected proposal is routed to a *different* layer, which rewrites the proposer's prompt — AlphaEvolve discards and re-samples). The two are a matched pair: each follows from what the system is pointed at.

Two neighbours are worth naming precisely because they are routinely confused with this one and are not in the same class. **promptolution** (CAPO) optimizes a single monolithic instruction string plus few-shot demos for one LLM call — no pipeline, no cross-run memory, in-memory only. **promptfoo** is an assertion-and-red-teaming harness in which a human writes every variant: 50+ assertion types and a CI story we have nothing like, but no search loop at all. Neither optimizes per-node pipeline parameters, and only CAPO does statistical elimination (paired t-test racing, α=0.2).

---

## What a DSPy source study settles

The matchups above are published numbers. This section is a different kind of evidence: a read
of DSPy's own source (3.1.3, the installed revision), because DSPy is the system our peers are
compared *through* — MIPROv2, COPRO, BootstrapFewShot and GEPA all ship inside it — and because
it is the reference for any ecosystem expansion we plan ([`../specs/roadmap.md`](../specs/roadmap.md)
§ Application radius). Claims here are implementation facts, re-verified against our own tree
afterwards; two were confirmed by running them.

**Their ecosystem beats ours. Their code does not.** That is the finding, and both halves
matter — the second is why we do not reshape our engine toward theirs, the first is why we
must still be consumable by their world.

Where the engines diverge, in their source:

- **No statistical machinery at all.** Scoring is exhaustive `program × devset`, every time.
  PoBB has no counterpart — and the absence is not an oversight they haven't reached:
  `eval_candidate_program_with_pruning` exists as dead code, and `random_search.py` carries a
  comment *designing* progressive elimination that was never built. MIPROv2's minibatch
  compares candidates on **different** resampled subsets, unpaired, and promotes on the raw
  arithmetic mean, with its own source TODO conceding the weighting problem.
- **A cache hit reports as free.** On replay the response's usage is zeroed, so a re-run costs
  nothing in their accounting. Ours splits the two deliberately — `TokenUsageRecord.cached`
  exists so a replayed L4 arm cannot read as infinitely efficient. For a design whose premise
  is most-fitness-per-dollar, this is the sharpest single contrast in the comparison.
- **`KeyboardInterrupt` appears nowhere in the package.** Ctrl+C unwinds `compile()` and the
  run is lost — no partial best, no trial log. There is no optimizer-level checkpoint or resume;
  the one that exists lives in the external `gepa` package, not in DSPy.
- **The saved artifact is not self-describing and drifts silently.** Signature fields serialize
  positionally with no names or types, and reload through a `zip(..., strict=False)`: add one
  input field between save and load and the prompt reloads scrambled, with no error and healthy-
  looking instructions. It also carries zero provenance — no score, metric, dataset or run id.
- **Fallbacks are silent and billed.** Their chat adapter catches a bare exception and re-runs
  the *entire* call through the JSON adapter — a second billed request under a different prompt,
  with no telemetry. A run's prompts are not reproducible from its config. Our no-fallback rule
  (root `CLAUDE.md` § STOP) is the same judgment made the other way.
- **No byte budget anywhere** — no cap, no truncation policy, no per-section accounting, no
  untrusted-content boundary. Every signature field is always rendered, in declaration order.

Where they are ahead, and it is the half that decides reach:

- **Consumability.** MLflow's own integration extracts full per-call spans from DSPy while DSPy
  contains no MLflow code at all. Our Langfuse sink is first-party and works; our MLflow sink
  sees two events and is off by default. Their *ecosystem* does the work their *code* never did,
  which is exactly the property we are missing.
- **Extension cost for a new search regime.** A new DSPy optimizer is one file satisfying no
  contract; GEPA drops in as an external adapter. Our seams are declarative for policy and
  panels but not for algorithm — deliberate, and a real cost the day we want a second regime.

## Algorithm configuration: the classical lineage

The umbrella term **algorithm configuration** is borrowed from AutoML, where it has a 23-year-old technical meaning: systematically tuning the parameters of a fixed algorithm (a SAT solver, a metaheuristic, an ML training procedure) over a distribution of problem instances using statistical primitives — racing, successive halving, surrogate models, configuration-space sampling. Modern LLM-driven systems (AlphaEvolve, MIPROv2, GEPA, PromptWizard, PromptPotter) are re-deriving these primitives under different names, almost without exception failing to cite the AutoML lineage. This section is the methodological anchor: where the racing tests, sampling models, and termination criteria actually come from, and how PromptPotter maps onto them.

### The lineage

- **F-Race** (Birattari, Stützle, Paquete & Varrentrapp, 2002) — Friedman-test racing for metaheuristic configuration. Candidates race on problem instances; statistically inferior candidates are eliminated as soon as the test rejects them. The grandfather of modern algorithm configurators.
- **irace** (López-Ibáñez et al., 2016, *Operations Research Perspectives*) — iterated F-Race. Canonical AutoML algorithm-configurator: sample configurations from a model, race them on instances, eliminate losers, update the sampling model, repeat. Still the reference implementation the field measures against.
- **ParamILS** (Hutter, Hoos & Stützle, 2009) — iterated local search over parameter configurations. Different search operator (ILS rather than racing), same problem statement.
- **SMAC** (Hutter, Hoos & Leyton-Brown, 2011) — model-based (random-forest surrogate) algorithm configurator. The Bayesian-optimization branch of the same family.
- **Hyperband / BOHB** (Li et al., 2017; Falkner et al., 2018) — the hyperparameter-optimization branch: successive halving with early stopping on training curves. Same racing intuition, applied to ML training rather than algorithm runs.
- **Optuna** (Akiba et al., 2019) — the widely-adopted HPO framework; exposes the same primitives (search space, pruning, racing-style early stopping) to practitioners.

### Where PromptPotter sits

PromptPotter's sequential elimination (Bayesian Posterior-of-Being-Best with an ε futility threshold and a minimum-queries floor — knobs `pobb_epsilon` / `elimination_n_min`) **is** a racing procedure. The mapping to the algorithm-configuration framing is direct:

| Algorithm configuration | PromptPotter |
|-------------------------|--------------|
| Configuration space | `pipeline_params` + 8-field prompt decomposition |
| Problem instance | One dataset query |
| Runtime / cost metric | Scoring formula output |
| Racing test | Bayesian Posterior-of-Being-Best (Russo 2016) — `elimination_p_best`; the model is owned by [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) |
| Sampling model | L1 generator (LLM) + L1-critique-guided L2/L3 |
| Termination | `sp_budget_ttest` budget, convergence, or operator interrupt (Ctrl+C) |

PoBB sits closest to OCBA (Chen 2000) and Top-Two Thompson Sampling (Russo 2016) — the Bayesian descendants of the racing tradition the AutoML lineage didn't initially include, but that the bandit BAI literature has spent two decades developing.

`pipeline_params` optimization is the closest direct analogue to classical algorithm configuration — node parameters are exactly the kind of numerical/categorical knobs irace was built for. The 8-field prompt decomposition is the prompt-native extension: each field is a semantic parameter that the L1→L2→L3 critique loop mutates. That critique loop is the one piece irace lacks — irace's sampling models are numerical (truncated normals, discrete distributions); it has no notion of "reflect on why this configuration failed and propose a better one." Conversely, PromptPotter lacks irace's formal guarantees on configuration-space coverage and its convergence proofs.

### The gap in the LLM-era literature

MIPROv2, GEPA, PromptWizard, Promptomatix, adv-CoT, AFlow, ADAS, AlphaEvolve — none cite the algorithm-configuration lineage. The closest the field gets is **CAPO** (promptolution, 2025), which explicitly implements paired t-test racing with α=0.2 for candidate selection. Even CAPO's paper does not cite F-Race or irace; it frames the racing procedure as a novel contribution rather than a 23-year-old technique from operations research.

This is worth noting. The absence is not just a citation oversight — it means the LLM-driven configuration community is re-deriving AutoML primitives (racing, successive halving, surrogate models, configuration-space sampling) under different names, without the theoretical scaffolding the AutoML community has already built. Pointing at the lineage is a small contribution in itself: it opens the door to importing decades of proof technique (sample complexity bounds, anytime convergence, portfolio construction) into the LLM-driven setting.

### Lineage references

- Birattari, M., Stützle, T., Paquete, L., & Varrentrapp, K. (2002). *A racing algorithm for configuring metaheuristics.* GECCO.
- López-Ibáñez, M., Dubois-Lacoste, J., Pérez Cáceres, L., Stützle, T., & Birattari, M. (2016). *The irace package: iterated racing for automatic algorithm configuration.* Operations Research Perspectives, 3, 43–58.
- Hutter, F., Hoos, H. H., & Stützle, T. (2009). *ParamILS: an automatic algorithm configuration framework.* JAIR, 36, 267–306.
- Hutter, F., Hoos, H. H., & Leyton-Brown, K. (2011). *Sequential model-based optimization for general algorithm configuration.* LION.
- Li, L., Jamieson, K., DeSalvo, G., Rostamizadeh, A., & Talwalkar, A. (2017). *Hyperband: a novel bandit-based approach to hyperparameter optimization.* JMLR, 18, 1–52.
- Falkner, S., Klein, A., & Hutter, F. (2018). *BOHB: robust and efficient hyperparameter optimization at scale.* ICML.
- Akiba, T., Sano, S., Yanase, T., Ohta, T., & Koyama, M. (2019). *Optuna: a next-generation hyperparameter optimization framework.* KDD.
- Russo, D. (2016). *Simple Bayesian algorithms for best arm identification.* COLT. — The Bayesian BAI family PoBB belongs to.
- Chen, C.-H. (2000). *Optimal Computing Budget Allocation.* Operations Research. — Population-aware Bayesian budget allocation; closest classical relative of PoBB.
- Kalyanakrishnan, S., Tewari, A., Auer, P., & Stone, P. (2012). *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; the pairwise frequentist BAI we considered and rejected.

## Head-to-head matchups from the literature

**Numbers across matchups are NOT comparable** — different backbone models, token budgets and
eval protocols. Each is one paper's own table under its own conditions; read them there rather
than from a transcription here, which cannot be re-verified and goes stale against a v2.

| Matchup | Source | What it reports |
|---|---|---|
| GEPA vs MIPROv2 | [arXiv:2507.19457](https://arxiv.org/abs/2507.19457) (Qwen3-8B) | GEPA ahead on HotPotQA / HoVer / PUPA, and on AIME-2025 with GPT-4.1 Mini |
| CAPO vs field | [arXiv:2512.02840](https://arxiv.org/abs/2512.02840) (Gemma-3-27B, 1M-token budget) | CAPO ahead of OPRO / EvoPromptGA / AdalFlow / DSPy-GEPA on GSM8K + SST-5 |
| AdalFlow vs TextGrad vs DSPy | [arXiv:2501.16673](https://arxiv.org/abs/2501.16673) (GPT-3.5-turbo) | AdalFlow ahead on ObjectCount / TREC-10 / three HotPotQA settings |
| Trace vs DSPy | [arXiv:2406.16218](https://arxiv.org/abs/2406.16218) (GPT-3.5-turbo) | Trace+CoT ahead on BBH, at 3× TextGrad's wall-clock |
| AFlow vs ADAS | [arXiv:2410.10762](https://arxiv.org/abs/2410.10762) | AFlow ahead on average over six benchmarks; GPT-4o-mini + optimized workflow beats GPT-4o + manual at a fraction of inference cost |
| AlphaEvolve on classical targets | [arXiv:2506.13131](https://arxiv.org/abs/2506.13131) | 4×4 complex matrix multiplication 49 → 48 scalar mults, the first improvement over Strassen in 56 years; data-center scheduling deployed |

Full taxonomy: the [Compound AI Systems Optimization survey](https://arxiv.org/abs/2506.08234) (EMNLP 2025).

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
| [Test-time compute scaling](https://arxiv.org/abs/2408.03314) | — | 2024 | **The broadest frame** — compute-optimal allocation of inference budget; more effective than scaling parameters (§ Adjacent work) |
| [Inference-time algorithms](https://arxiv.org/abs/2406.16838) | TMLR | 2024 | Survey of the same family — decoding through meta-generation |
| [metaTextGrad](https://arxiv.org/abs/2505.18524) | NeurIPS | 2025 | Meta-optimization |
| [GEPA](https://arxiv.org/abs/2507.19457) | ICLR (Oral) | 2026 | Reflective prompt evolution |
| [CAPO](https://arxiv.org/abs/2504.16005) | — | 2025 | Cost-aware evolutionary prompt optimization; paired t-test racing. Which opponents the head-to-head runs is owned by [`bbeh-comparison/README.md`](bbeh-comparison/README.md) § The protocol |
| [promptolution](https://arxiv.org/abs/2512.02840) | — | 2025 | The framework CAPO ships in — and the source of § Matchup 2's cross-framework table. A *different paper* from CAPO's; cite by which one the number came from |
| [MO-CAPO](https://arxiv.org/abs/2605.18869) | — | 2026 | Multi-objective prompt optimization — accuracy jointly with inference cost |
| [AdalFlow](https://arxiv.org/abs/2501.16673) | — | 2025 | LLM AutoDiff (compound systems) |
| [Optimas](https://arxiv.org/abs/2507.03041) | ICLR | 2026 | Multi-component compound systems |
| [Survey](https://arxiv.org/abs/2506.08234) | EMNLP | 2025 | Compound AI optimization taxonomy |
| [APO survey](https://arxiv.org/abs/2502.16923) | — | 2025 | Systematic survey of automatic prompt optimization; five-part framework, evaluation as its own axis |
| [HybridFlow / verl](https://arxiv.org/abs/2409.19256) | EuroSys | 2025 | RLHF training framework — the weights-side reference for the harness-vs-weights dial (§ Adjacent work) |
| [p1](https://arxiv.org/abs/2604.08801) | — | 2026 | **When prompt optimization can work at all** — reward-variance decomposition → [`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § What this is for |
| [Optimization before Evaluation](https://arxiv.org/abs/2604.27637) | — | 2026 | **Evaluation methodology** — measuring against an unoptimized-prompt baseline misleads |
| [Winner's Curse in Adaptive Benchmarking](https://arxiv.org/abs/2605.05973) | — | 2026 | **Evaluation methodology** — the selected winner's own score is biased upward, and the correction for it |
| [SePO](https://arxiv.org/abs/2606.04465) | — | 2026 | Self-evolving prompt agent — newest in the line, unread |

---

## Adjacent work

Three nearby threads do not fit the umbrella as cleanly but inform the design.

- **Workflow architecture search.** AFlow ([arXiv:2410.10762](https://arxiv.org/abs/2410.10762)), ADAS ([arXiv:2408.08435](https://arxiv.org/abs/2408.08435)), Trace ([arXiv:2406.16218](https://arxiv.org/abs/2406.16218)), and AdalFlow ([arXiv:2501.16673](https://arxiv.org/abs/2501.16673)) optimize the *graph* of LLM calls rather than the parameters of a fixed graph. The configuration space is structurally different (edges, node identities) but the search-loop primitives are the same.
- **LLM-as-optimizer foundations.** APE ([arXiv:2211.01910](https://arxiv.org/abs/2211.01910)) and OPRO are the foundational instances of using an LLM to propose prompts; TextGrad ([arXiv:2406.07496](https://arxiv.org/abs/2406.07496)) lifts gradient-style backpropagation onto natural-language critique. They predate the umbrella as a coherent framing but seed every system above.
- **Compound-system surveys and meta-optimization.** The [Compound AI Systems Optimization survey](https://arxiv.org/abs/2506.08234) (EMNLP 2025) catalogues the compound-system branch; metaTextGrad ([arXiv:2505.18524](https://arxiv.org/abs/2505.18524)) and Optimas ([arXiv:2507.03041](https://arxiv.org/abs/2507.03041)) push toward second-order optimization of optimizers themselves.
- **Test-time compute — the broadest frame, and the one we are measured against by default.** Spending compute at inference rather than in training: repeated sampling with a verifier (best-of-N, self-consistency), sequential revision, and search over reasoning steps under a process reward model ([Snell et al. 2024](https://arxiv.org/abs/2408.03314); survey: [Welleck et al., TMLR](https://arxiv.org/abs/2406.16838)). **Same shape as ours — search under a verifier — but on the other side of the request boundary.** TTC puts the search *inside* the request: it runs per query, on the serving path, for the life of the system. PromptPotter is that search as **tuning infrastructure** — run separately, it yields a configuration that is then used on demand, with no search in the path and nothing learning at request time. The artifact it ships is static, which is also why it transfers across models and needs no serving infrastructure of its own. Two consequences worth stating explicitly. (1) **The machinery overlaps at budget allocation**: compute-optimal TTC asks which prompt deserves the next sample, PoBB asks which candidate deserves the next measurement — both best-arm-identification-shaped (§ Best-arm identification), which is why the allocation literature transfers here more directly than the decoding literature does. (2) **It is a benchmarking hazard.** The amount of test-time compute a prompt induces is itself a tunable axis here (`reasoning_effort`, `max_tokens`, model), so a comparison that does not hold the *inference* budget constant rewards a candidate for buying more thinking rather than for thinking better — the same class of confound [`benchmarks.md`](benchmarks.md) already refuses on the model axis. As a lever it is orthogonal to the harness/weights dial below: an operating agent holds three, not two.
- **Tuning the harness, by hand.** LangChain's [Nemotron 3 Ultra playbook](https://www.langchain.com/blog/tuning-the-harness-not-the-model-a-nemotron-3-ultra-playbook) is PromptPotter's exact thesis executed manually. Freeze the model, tune the *harness* — single-purpose system-prompt blocks, tool descriptions, middleware — against a Deep Agents eval suite through **Evaluate → Observe → Diagnose → Engineer-one-change → Re-evaluate on a cost ladder**, keeping only changes that survive repeated trials and regress nothing. Nemotron 3 Ultra reached 0.86 vs Claude Opus 4.8's 0.87 at ~10× lower cost, no retraining. That loop is what PromptPotter automates: single-purpose blocks ≙ the 8-field decomposition; one-change-that-survives-trials ≙ PoBB racing + holdout; cost ladder ≙ L1→L2→L3 escalation — driven by a human engineer + LangSmith traces where PromptPotter drives it with an optimizer. Their stated ceiling — "it can't add what isn't in the weights" — is PromptPotter's ceiling too, and exactly where the next entry begins. Whether LangChain is the harness *we* would evolve is a product question owned by [`../specs/roadmap.md`](../specs/roadmap.md) § Evolving agent harnesses.
- **When the loop is licensed to spend on training.** NVIDIA's **AutoResearch** workflow ([dev blog](https://developer.nvidia.com/blog/how-to-run-an-autoresearch-workflow-with-rl-agent-skills-and-nvidia-nemo/)) is **Karpathy's `autoresearch` framework above**, wrapped with **NeMo RL + NeMo Gym** as the training/eval backend and three RL agent skills (`brev-etiquette`, `session-memory`, `autoresearch`) — not a separate same-named system. It runs the same open-ended propose → evaluate → keep loop, and the agent mutates *training code and config* — "free to modify any aspect of the training pipeline," not a fixed search space — which places it inside the umbrella beside AlphaEvolve, not outside it. The one honest difference from PromptPotter is the **deliverable**: because the code the agent writes launches a training run, a run's output can be *new weights* (one demo drove Qwen3-VL-2B 25% → 96.9% via LoRA SFT), whereas PromptPotter's output is a harness for a frozen model. The axis is therefore **not** "mutates weights vs. prompts" — all three search over code / config / text with an LLM in the propose step — but whether the loop is *licensed to spend on training runs at all*. It does **not** address prompt / pipeline optimization — the complement, and the case for reaching for PromptPotter first, is in **§ PromptPotter × NVIDIA AutoResearch** below.

Those two mark the ends of one dial: **LangChain / LangSmith tunes the harness by hand with the model frozen; AutoResearch lets its agent spend on training runs, so a run's output can be new weights.** But all three — LangChain's hand-loop, AutoResearch, and PromptPotter — search over *code / config / text* with an LLM in the propose step; they differ only in what the loop is pointed at and licensed to spend on. The automated *middle* — an optimizer that evolves the harness with no human in the diagnose-and-engineer step — is what GEPA does for DSPy prompts and what PromptPotter does for pipelines. In an ecosystem that already pairs them (NVIDIA's Nemotron Coalition lists LangChain as a partner, and its NeMo suite advertises "agent optimization" alongside customization and governance), a drop-in automated harness-optimizer is the natural extension to a by-hand playbook — the integration point PromptPotter is shaped for.

### PromptPotter × NVIDIA AutoResearch: two levers, one operating agent

NVIDIA's AutoResearch does not do prompt or pipeline-parameter optimization at all — its agent edits training code and launches SFT / GRPO / DPO runs to change *weights*. That makes the relationship complementary, not competitive, and it runs in both directions. The product direction this implies — PromptPotter as a first-class agent-callable tool — is tracked in [roadmap.md](../specs/roadmap.md) § Agent-tool parity.

**Their agent should know about PromptPotter.** Faced with "improve this model on this task," the AutoResearch agent reaches straight for a training run. But for a large share of real tasks the win is in the harness, not the weights — and if the agent had PromptPotter as a tool it could call, it would often propose *that* instead: cheaper (inference-only — cents, not GPU-hours), faster (minutes, not a training job), transferable (a prompt / pipeline config carries across models; fine-tuned weights are locked to one base), and light to store and serve (a text artifact, not a multi-gigabyte checkpoint to host). The autoresearch loop already runs on markdown "skills" plus a ledger, so PromptPotter drops in as a *try-harness-first* skill beside NeMo RL rather than in place of it.

**Our agent should know about fine-tuning.** The mirror holds. An operator-agent driving PromptPotter should, when the harness ceiling is hit — the failure that survives every prompt/pipeline change because the capability genuinely is not in the weights — be able to say "consider SFT / GRPO / DPO here; reinforcement learning may be the right lever now" and route to a weight-training tool. That is a policy handed to the *driving* agent, not a new mechanism inside the loop: one operating agent that holds both levers and picks by the evidence, escalating from harness to weights only when the cheaper lever is exhausted.

**A same-architecture head-to-head.** The two approaches share enough shape to be measured against each other directly. Point *both* at the **same dataset and the same base model**: freeze the model and let PromptPotter tune its harness on one side, fine-tune that model's weights with the AutoResearch / NeMo workflow on the other, and compare accuracy *together with* dollar cost, latency, storage, and transferability. This is worth doing — it turns "try the harness first" from a rhetorical claim into a measured cost-multiple, and it fits the benchmark-hygiene discipline this doc argues for (same target model, held-out split, lift-over-reference). Two honest caveats: PromptPotter usually points at a hosted backend, so a fair run means aiming it at the *same open-weight model* AutoResearch fine-tunes (feasible — the backend is pluggable); and "what ships" is deliberately asymmetric — a portable prompt versus model-locked weights — which is itself a result to report, not a confound to control away. The natural first slice is the one `promptpotter-self` already runs (`justlogic-d234`): fine-tune the same base model on the same data and read the two curves side by side.

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

Three design choices, each dictated by a different need:

- **Population-aware over pairwise** — the question is "is this the round winner?", not "is it worse than each prior?"; only the joint posterior across all candidates answers it.
- **Bayesian over frequentist** — `P(c is best)` is one operator-readable number ("c042 73% probability of winning round"); a Holm-corrected p-value or Hoeffding bound is not.
- **Fixed-confidence (ε) over fixed-budget** — broken candidates stop as soon as the evidence floor is met (early high-signal regime), indistinguishable ones run to the cap (late low-signal regime); phased fixed-budget algorithms can't do the first.

For the implementation, two-regime analysis, tunable knobs, open questions, and the rationale for replacing Wilcoxon, see [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md).

### Comparison to MCTS

PromptPotter **is** AlphaZero-shaped MCTS over the lineage tree. The four phases, and where each lives:

- **Selection.** When L2/L3 judges the current subtree exhausted it emits `fork_proposal: {reason}` — *whether* to rewind, a judgment no rule makes well. *Where* to rewind is then decided by UCB1 over the backpropagated tree (`application/mask/backprop.py::select_rewind_round`): each ancestor's mean ability plus an exploration bonus for how little it has been tried. The runner mints a sibling cycle at that round and auto-continues there (`_mint_fork(L{2,3}_REBASE)`, capped at `MAX_AUTO_REBASES`). The layer deliberately does *not* name the round: no panel ever enumerated the ancestors and their fitness, so a free-form offset was an unanchored guess carrying the most expensive decision in the loop.
- **Expansion.** A round: L1 proposes a population from the selected node.
- **Simulation.** A deterministic forward pass on the eval set, not a random rollout. AlphaZero is the published precedent for exactly this swap (it replaces rollouts with a learned value), which is why the determinism does not make this not-MCTS. Within a round, [PoBB](#best-arm-identification--sequential-testing) prunes losers before they consume the budget — a sharper instrument than UCB1 for the *sibling* comparison, because a round's arms are measured on shared samples.
- **Backpropagation.** Each round's **Rasch ability θ** (`RoundResult.ability`) is rolled up to every ancestor as visit count + value (`accumulate_node_stats`). An ancestor's statistics therefore answer "what did re-expanding from here actually yield, everywhere it was tried?" — including in branches it never ran itself.

Two design points the naive construction gets wrong, both load-bearing:

**Value must be θ, not accuracy.** Rounds score different sample subsets, so accuracy is subset-relative: a deep branch that drifted onto easier samples would out-rank a shallow honest one, and averaging it up a lineage would quietly reward the drift. θ is subset-invariant by construction — it is why the frontier is persisted at all. (This is the "backpropagation semantics need care" worry, resolved: the ability scale, not the raw score, is what is meaningful to average across rounds run under different framings.)

**A fork's inherited prefix is not a fresh visit.** A fork physically copies its parent's rounds forward, so those rounds exist twice on disk under two `cycle_id`s while being the *same* logical node. Counted naively, an ancestor's visit count inflates by every descendant's copied prefix — worse the deeper the lineage, and silently, since the fold still returns a plausible number. The fold keeps only each cycle's own new rounds and re-attaches its spine to the branch-point.

Rollout cost is the one place we stay deliberately conservative: a "rollout" here is a full round of LLM calls per candidate, so exploration is sample-efficient by design — closer in spirit to AlphaZero's PUCT than to AlphaGo's vanilla UCT over free rollouts.

The capability this buys: **recovery from dead-end branches.** A trajectory that exhausts itself no longer just ends the cycle. Single-lineage search cannot do this at all; DarwinX's archive can, but it stores *candidates* where this stores a backpropagated value per ancestor — so which ancestor to re-expand is answered, not chosen.

