# Algorithm Configuration: the Classical Lineage

The umbrella term **algorithm configuration** in [`related-work.md`](related-work.md) is borrowed from AutoML, where it has a 23-year-old technical meaning: systematically tuning the parameters of a fixed algorithm (a SAT solver, a metaheuristic, an ML training procedure) over a distribution of problem instances using statistical primitives — racing, successive halving, surrogate models, configuration-space sampling. Modern LLM-driven systems (AlphaEvolve, MIPROv2, GEPA, PromptWizard, PromptPotter) are re-deriving these primitives under different names, almost without exception failing to cite the AutoML lineage. This page is the methodological anchor: where the racing tests, sampling models, and termination criteria actually come from, and how PromptPotter maps onto them.

---

## The lineage

- **F-Race** (Birattari, Stützle, Paquete & Varrentrapp, 2002) — Friedman-test racing for metaheuristic configuration. Candidates race on problem instances; statistically inferior candidates are eliminated as soon as the test rejects them. The grandfather of modern algorithm configurators.
- **irace** (López-Ibáñez et al., 2016, *Operations Research Perspectives*) — iterated F-Race. Canonical AutoML algorithm-configurator: sample configurations from a model, race them on instances, eliminate losers, update the sampling model, repeat. Still the reference implementation the field measures against.
- **ParamILS** (Hutter, Hoos & Stützle, 2009) — iterated local search over parameter configurations. Different search operator (ILS rather than racing), same problem statement.
- **SMAC** (Hutter, Hoos & Leyton-Brown, 2011) — model-based (random-forest surrogate) algorithm configurator. The Bayesian-optimization branch of the same family.
- **Hyperband / BOHB** (Li et al., 2017; Falkner et al., 2018) — the hyperparameter-optimization branch: successive halving with early stopping on training curves. Same racing intuition, applied to ML training rather than algorithm runs.
- **Optuna** (Akiba et al., 2019) — the widely-adopted HPO framework; exposes the same primitives (search space, pruning, racing-style early stopping) to practitioners.

---

## Where PromptPotter sits

PromptPotter's sequential elimination (Bayesian Posterior-of-Being-Best, ε=0.05, minimum 4 queries before any candidate can be dropped) **is** a racing procedure. The mapping to the algorithm-configuration framing is direct:

| Algorithm configuration | PromptPotter |
|-------------------------|--------------|
| Configuration space | `pipeline_params` + 8-field prompt decomposition |
| Problem instance | One dataset query |
| Runtime / cost metric | Scoring formula output |
| Racing test | Bayesian Posterior-of-Being-Best (Russo 2016): joint Normal-CLT posterior over candidate accuracy, MC argmax, stop when `P(c is best) < ε` |
| Sampling model | L1 generator (LLM) + L1-critique-guided L2/L3 |
| Termination | `sp_budget_ttest` budget, convergence, or operator interrupt (Ctrl+C) |

### Lineage entry: Wilcoxon → PoBB transition

Until this revision, PromptPotter's racing test was paired Wilcoxon signed-rank + Holm-Bonferroni (α=0.2). It was retired in favor of Bayesian PoBB on three grounds:

1. **Pairwise → population.** Wilcoxon compared the current candidate against each prior independently and Holm-corrected across the comparisons. PoBB samples the joint posterior over all candidates, asking the actually-relevant question "what is each candidate's probability of being the round winner?"
2. **Variance-agnostic → variance-adaptive.** Signed-rank uses ranks of paired differences. PoBB's Normal-CLT posterior tightens with observed variance, so high-signal regimes (low variance + clear gap) abort within 3–5 queries vs Wilcoxon's ≥8.
3. **Operator-illegible → operator-readable.** P(c is best) renders per-query in the live dashboard ("c042 73% probability of winning round"); Wilcoxon's Holm-stepped p-values do not.

The replacement does not change PromptPotter's standing in the lineage table — both Wilcoxon and PoBB are racing procedures in the F-Race / irace family. PoBB is closer to OCBA (Chen 2000) and Top-Two Thompson Sampling (Russo 2016), the Bayesian descendants of the racing tradition that the AutoML lineage didn't initially include but that the bandit BAI literature has spent two decades developing.

`pipeline_params` optimization is the closest direct analogue to classical algorithm configuration — node parameters are exactly the kind of numerical/categorical knobs irace was built for. The 8-field prompt decomposition is the prompt-native extension: each field is a semantic parameter that the L1→L2→L3 critique loop mutates. That critique loop is the one piece irace lacks — irace's sampling models are numerical (truncated normals, discrete distributions); it has no notion of "reflect on why this configuration failed and propose a better one." Conversely, PromptPotter lacks irace's formal guarantees on configuration-space coverage and its convergence proofs.

---

## The gap in the LLM-era literature

MIPROv2, GEPA, PromptWizard, Promptomatix, adv-CoT, AFlow, ADAS, AlphaEvolve — none cite the algorithm-configuration lineage. The closest the field gets is **CAPO** (promptolution, 2025), which explicitly implements paired t-test racing with α=0.2 for candidate selection. Even CAPO's paper does not cite F-Race or irace; it frames the racing procedure as a novel contribution rather than a 23-year-old technique from operations research.

This is worth noting. The absence is not just a citation oversight — it means the LLM-driven configuration community is re-deriving AutoML primitives (racing, successive halving, surrogate models, configuration-space sampling) under different names, without the theoretical scaffolding the AutoML community has already built. Pointing at the lineage is a small contribution in itself: it opens the door to importing decades of proof technique (sample complexity bounds, anytime convergence, portfolio construction) into the LLM-driven setting.

---

## References

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
