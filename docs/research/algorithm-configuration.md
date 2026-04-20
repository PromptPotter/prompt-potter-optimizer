# Algorithm Configuration: The Missing Lineage

## Why this file exists

Prompt-optimization papers position themselves against Bayesian optimization, evolutionary search, reinforcement learning, or LLM-as-optimizer (OPRO). The **automatic algorithm configuration** lineage out of AutoML — F-Race → irace → ParamILS → SMAC — is structurally closer to what tools like PromptPotter and CAPO actually do, but the connection is almost never made explicit. This note flags that gap for the paper's related-work section.

## The lineage

- **F-Race** (Birattari, Stützle, Paquete & Varrentrapp, 2002) — Friedman-test racing for metaheuristic configuration. Candidates race on problem instances; statistically inferior candidates are eliminated as soon as the test rejects them. The grandfather of modern algorithm configurators.
- **irace** (López-Ibáñez, Dubois-Lacoste, Pérez Cáceres, Stützle & Birattari, 2016, *Operations Research Perspectives*) — iterated F-Race. Canonical AutoML algorithm-configurator: sample configurations from a model, race them on instances, eliminate losers, update the sampling model, repeat. Still the reference implementation the field measures against.
- **ParamILS** (Hutter, Hoos & Stützle, 2009) — iterated local search over parameter configurations. Different search operator (ILS rather than racing), same problem statement.
- **SMAC** (Hutter, Hoos & Leyton-Brown, 2011) — model-based (random-forest surrogate) algorithm configurator. The Bayesian-optimization branch of the same family.
- **Hyperband / BOHB** (Li et al., 2017; Falkner et al., 2018) — the hyperparameter-optimization branch: successive halving with early stopping on training curves. Same racing intuition, applied to ML training rather than algorithm runs.
- **Optuna** (Akiba et al., 2019) — the widely-adopted HPO framework; exposes the same primitives (search space, pruning, racing-style early stopping) to practitioners.

## Where PromptPotter sits

PromptPotter's sequential elimination (paired Wilcoxon signed-rank + Holm-Bonferroni, α=0.05, minimum 6 queries before any candidate can be dropped — single threshold, no separate enable gate) **is** a racing procedure. The mapping to the algorithm-configuration framing is direct:

| Algorithm configuration | PromptPotter |
|-------------------------|--------------|
| Configuration space | `pipeline_params` + 8-field prompt decomposition |
| Problem instance | One dataset query |
| Runtime / cost metric | Scoring formula output (`compile_scorer()`) |
| Racing test | Wilcoxon signed-rank, Holm-Bonferroni correction |
| Sampling model | L1 generator (LLM) + critique-guided L2/L3 |
| Termination | `sp_budget_ttest` budget, convergence, or HITL pause |

`pipeline_params` optimization is the closest direct analogue to classical algorithm configuration — node parameters are exactly the kind of numerical/categorical knobs irace was built for. The 8-field prompt decomposition is the prompt-native extension: each field is a semantic parameter that the L1→L2→L3 critique loop mutates. That critique loop is the one piece irace lacks — irace's sampling models are numerical (truncated normals, discrete distributions); it has no notion of "reflect on why this configuration failed and propose a better one." Conversely, PromptPotter lacks irace's formal guarantees on configuration-space coverage and its convergence proofs.

## The gap in the prompt-optimization literature

MIPROv2, GEPA, PromptWizard, Promptomatix, adv-CoT, AFlow, ADAS — none cite the algorithm-configuration lineage. The closest the field gets is **CAPO** (promptolution, 2025), which explicitly implements paired t-test racing with α=0.2 for candidate selection. Even CAPO's paper does not cite F-Race or irace; it frames the racing procedure as a novel contribution rather than a 23-year-old technique from operations research.

This is worth noting in the paper's related-work section. The absence is not just a citation oversight — it means the prompt-optimization community is re-deriving AutoML primitives (racing, successive halving, surrogate models, configuration-space sampling) under different names, without the theoretical scaffolding the AutoML community has already built. Pointing at the lineage is a small contribution in itself: it opens the door to importing decades of proof technique (sample complexity bounds, anytime convergence, portfolio construction) into prompt optimization.

## References

- Birattari, M., Stützle, T., Paquete, L., & Varrentrapp, K. (2002). *A racing algorithm for configuring metaheuristics.* GECCO.
- López-Ibáñez, M., Dubois-Lacoste, J., Pérez Cáceres, L., Stützle, T., & Birattari, M. (2016). *The irace package: iterated racing for automatic algorithm configuration.* Operations Research Perspectives, 3, 43–58.
- Hutter, F., Hoos, H. H., & Stützle, T. (2009). *ParamILS: an automatic algorithm configuration framework.* JAIR, 36, 267–306.
- Hutter, F., Hoos, H. H., & Leyton-Brown, K. (2011). *Sequential model-based optimization for general algorithm configuration.* LION.
- Li, L., Jamieson, K., DeSalvo, G., Rostamizadeh, A., & Talwalkar, A. (2017). *Hyperband: a novel bandit-based approach to hyperparameter optimization.* JMLR, 18, 1–52.
- Falkner, S., Klein, A., & Hutter, F. (2018). *BOHB: robust and efficient hyperparameter optimization at scale.* ICML.
- Akiba, T., Sano, S., Yanase, T., Ohta, T., & Koyama, M. (2019). *Optuna: a next-generation hyperparameter optimization framework.* KDD.
