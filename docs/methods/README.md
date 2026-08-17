# Methods

Two pages, split by the question they answer — **which samples** vs **which candidate**.

| Page | Covers |
|------|--------|
| [Verdict resolution](verdict-resolution.md) | The Rasch θ/δ model everything here is expressed in (incl. the graded response and its `√φ` SE correction), the separability precondition it assumes, and the two sample-selection mechanisms: the between-round acquisition score that feeds `hard_samples.json`, and the static within-round order. |
| [Candidate elimination (PoBB)](candidate-elimination.md) | Which candidate is winning, mid-round: the paired-sample fix that makes PoBB valid on a non-iid order, the θ stop rule, the five-mechanism ladder, and the on-disk shape replay reads. |

Hand-tuning the optimizer's own optimizer prompts, and measuring whether an edit helped: [`../manual/06-going-deeper.md`](../manual/06-going-deeper.md) § Iterating on prompts manually.

Within LLM-driven program evolution, PromptPotter targets the bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended synthesis, multi-objective fitness, and unlabelled tasks remain open problems for the paradigm and are not supported here.

Research positioning: [`../research/related-work.md`](../research/related-work.md), which also covers the classical AutoML ancestry (F-Race → irace → SMAC) in § Algorithm configuration: the classical lineage.
