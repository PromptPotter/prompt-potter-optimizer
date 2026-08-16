# Methods

The statistical model and the two spend-control procedures that read it.

| Page | Covers |
|------|--------|
| [Verdict resolution](verdict-resolution.md) | The single statistical model behind the live adaptive queue + the persisted `hard_samples.json` ranking — the model both procedures below draw from, and the separability precondition it assumes. |
| [Mid-round elimination (PoBB)](candidate-elimination.md) | Search-only-with-evidence: every variant runs the `elimination_n_min` floor before it may be cut at all; only those with statistical evidence of being the round's best get extended. Bayesian Posterior-of-Being-Best, joint-posterior MC. |
| [Hard-sample leaderboard (Rasch + KG)](exploration-exploitation.md) | Between rounds, swap understood samples for high-information ones. Same posterior feeds the standalone hard-sample sorter. |

Hand-tuning the optimizer's own optimizer prompts, and measuring whether an edit helped: [`../manual/06-going-deeper.md`](../manual/06-going-deeper.md) § Iterating on prompts manually.

Within LLM-driven program evolution, PromptPotter targets the bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended synthesis, multi-objective fitness, and unlabelled tasks remain open problems for the paradigm and are not supported here.

Research positioning: [`../research/related-work.md`](../research/related-work.md), which also covers the classical AutoML ancestry (F-Race → irace → SMAC) in § Algorithm configuration: the classical lineage.
