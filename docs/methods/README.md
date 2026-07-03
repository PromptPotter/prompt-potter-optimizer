# Methods

The two spend-control procedures.

| Page | Covers |
|------|--------|
| [Mid-round elimination (PoBB)](candidate-elimination.md) | Search-only-with-evidence: each variant runs ~3–5 samples; only those with statistical evidence of being round's best get extended. Bayesian Posterior-of-Being-Best, joint-posterior MC. |
| [Hard-sample leaderboard (Rasch + KG)](exploration-exploitation.md) | Between rounds, swap understood samples for high-information ones. Same posterior feeds the standalone hard-sample sorter. |

Iteration workflow / sweep mode for hand-tuning prompts: [`../manual/06-going-deeper.md`](../manual/06-going-deeper.md).

Within LLM-driven program evolution, PromptPotter targets the bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended synthesis, multi-objective fitness, and unlabelled tasks remain open problems for the paradigm and are not supported here.

Research positioning: [`../research/related-work.md`](../research/related-work.md), which also covers the classical AutoML ancestry (F-Race → irace → SMAC) in § Algorithm configuration: the classical lineage.
