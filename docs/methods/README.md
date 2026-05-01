# Methods

Statistical and algorithmic foundations of PromptPotter. Two independent procedures; each has its own page.

| Page | What it covers |
|------|----------------|
| [Individual elimination](candidate-elimination.md) | Bayesian Posterior-of-Being-Best (PoBB) — population-aware joint-posterior MC; stops a candidate when its P(round-best) drops below ε |
| [Exploration / exploitation sample selection](exploration-exploitation.md) | Rasch + Knowledge Gradient scoring-set evolution, sample tiering, zero-signal filter. Companion capability: [hard-sample sorter](../specs/hard-sample-sorter.md) |
| [Manual prompt tuning](manual-prompt-tuning.md) | M10's iteration framework — round-1 gate, sweep mode, behaviour checks, `/potter-review` skill, `proxy_lift_corr` validation procedure |

Within the LLM-driven program evolution paradigm, PromptPotter targets the bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended program synthesis, multi-objective fitness, and unlabelled tasks remain open problems for the paradigm and are not supported here.

For the research positioning of these methods alongside AlphaEvolve, MIPROv2, GEPA, PromptWizard et al., see [`../research/related-work.md`](../research/related-work.md). For the classical AutoML ancestry (F-Race → irace → SMAC) and the racing-primitive mapping, see [`../research/algorithm-configuration-lineage.md`](../research/algorithm-configuration-lineage.md).
