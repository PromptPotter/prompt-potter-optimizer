# PoBB stops wrongly far more often than it states

A pilot finding (2026-11-01), re-measured on the landed rule (2026-11-06), offline and seeded:
`python scripts/pobb_false_stops.py` reproduces the table.

**Equal arms (no real difference), 200 rounds of 6 candidates, 20 cells walked hardest-first, cold
ruler, ε 0.15, `n_min` 6, no backfills, seed 0:**

| Rule | Rounds with a false lock | False locks per arm | False cuts per arm | Mean cells per arm |
|---|---|---|---|---|
| Landed: θ-lift mixture ratio, α/K = 0.15/6 = 0.025 an arm | 0.0% | 0.0% | 42.3% | 15.6 |
| Before it: PoBB's `p_best ≥ 0.85` read after every cell | 96.5% | 18.4% | 34.4% | 14.0 |

The landed lock-in holds the 15% a round states; the posterior bar it replaced locked an equal arm
in almost every round. Single-arm, as the pilot ran it (`--arms 1 --alpha 0.15`), the landed rule
locks falsely in 2.5% of runs at a stated 15% (the pilot's interval rule: 6%; PoBB's bar: 24%).
A false cut is ε's futility stop on a tie: it ends measurement and is no verdict, which the
election still reads.

**The price is power.** One arm a true +1 logit ahead, walked first, locks in within 20 cells in
1.0% of rounds at α/K = 0.025 (21% single-arm at 0.15; 7.5% at +2 logits; 66% at +1.5 logits on 40
cells), against the bar's 98%, which its false locks inflate. The lift is read on the cold ruler's
MAP fit, shrunk toward its N(0, 1.5²) prior, and the Gaussian mixture is closed with no
one-sidedness credit, so the bound is conservative: a sharper anytime-valid test is M19's.

**The rule:** a normal-mixture likelihood ratio over a N(0, 1.5²) prior on the θ lift over each
prior, closed by Ville's inequality ([2210.01948](https://arxiv.org/abs/2210.01948) §2.9,
[1512.04922](https://arxiv.org/abs/1512.04922) §5.2), at level α/K an arm
([`candidate-elimination.md`](../../docs/methods/candidate-elimination.md) § The lock-in). It is model-based, so it stays valid under the hard-first walk; the model-free Hsu &
Shekhar sequence would need random cell picks. It is the statistical-layer ablation of
[`../README.md`](../README.md) § The sequence, stage 7.
