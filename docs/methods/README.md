# Methods

The two procedures that keep PromptPotter's spend low.

| Page | What it covers |
|------|----------------|
| [Mid-round elimination (PoBB)](candidate-elimination.md) | Search-only-with-evidence: each variant runs ~3–5 samples; only those with statistical evidence of being the round's best get extended. Bayesian Posterior-of-Being-Best, joint-posterior MC. |
| [Hard-sample dashboard (Rasch + KG)](exploration-exploitation.md) | Between rounds, swap understood samples for high-information ones. Same posterior feeds the standalone hard-sample sorter. |
| [Manual prompt tuning](manual-prompt-tuning.md) | M10 iteration framework — round-1 gate, sweep mode, behaviour checks, `proxy_lift_corr` validation. |

Within LLM-driven program evolution, PromptPotter targets the bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended program synthesis, multi-objective fitness, and unlabelled tasks remain open problems for the paradigm and are not supported here.

Research positioning: [`../research/related-work.md`](../research/related-work.md). Classical AutoML ancestry (F-Race → irace → SMAC): [`../research/algorithm-configuration-lineage.md`](../research/algorithm-configuration-lineage.md).
