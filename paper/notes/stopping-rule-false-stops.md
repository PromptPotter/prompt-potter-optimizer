# PoBB stops wrongly far more often than it states

A pilot finding (2026-11-01), offline and seeded: re-measure on the landed rule before it enters
the results table.

**Equal arms (no real difference), 200 simulations, 20 cells walked hardest-first:**

| Rule | Wrong stops | of which false locks | false cuts | Mean cells per arm |
|---|---|---|---|---|
| Anytime-valid interval on the Rasch θ, stated level 15% | 15.5% | 6% | 9.5% | 18.5 |
| PoBB (today's default) | 57.5% | 24% | 33.5% | 13.7 |

The interval rule delivers the level it states; PoBB's posterior bar, read after every cell, does
not. The price is cells: about 2–6 more per arm. With a real gain of +1 logit the interval rule locks
the true winner in 41.5% of runs within 20 cells, against PoBB's 74.5%, which its false stops inflate.

**The rule:** a mixture likelihood ratio over a N(0, 1.5²) prior on each arm's θ, closed by Ville's
inequality ([2210.01948](https://arxiv.org/abs/2210.01948) §2.9,
[1512.04922](https://arxiv.org/abs/1512.04922) §5.2). It is model-based, so it stays valid under the
hard-first walk; a model-free sequence would need random cell picks.

**Owed before quoting:** re-run on the landed rule (level α/K per arm, so a round holds its stated
level; no backfills on a warm ruler); move the simulation script into the repo so the table can be
reproduced. It is the statistical-layer ablation of [`../README.md`](../README.md) § The sequence,
stage 7.
