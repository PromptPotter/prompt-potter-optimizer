# Methods

Statistical and algorithmic foundations of PromptPotter. Two independent procedures; each has its own page.

| Page | What it covers |
|------|----------------|
| [Candidate elimination](candidate-elimination.md) | Paired Wilcoxon signed-rank test + Holm-Bonferroni correction — how candidates are dropped before consuming full budget |
| [Exploration / exploitation sample selection](exploration-exploitation.md) | Rasch + Knowledge Gradient prefix evolution, sample tiering, zero-signal filter. Companion capability: [hard-sample sorter](../specs/hard-sample-sorter.md) |

For the research positioning of these methods under the algorithm-configuration umbrella (AlphaEvolve, MIPROv2, GEPA, PromptWizard et al.), see [`../research/related-work.md`](../research/related-work.md). For the classical AutoML ancestry (F-Race → irace → SMAC) and the racing-primitive mapping, see [`../research/algorithm-configuration-lineage.md`](../research/algorithm-configuration-lineage.md).
