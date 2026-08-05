# Adaptive queue mechanism — sample ordering: the shared round order (+ between-round CAT primitives)

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

[Paired-sample PoBB](paired-sample-pobb.md) makes the mid-round elimination
comparison statistically valid by backfilling priors on the candidate's
samples; this page covers what decides the **iteration order** — the order
that determines how fast the elimination gates can accumulate evidence.

Code: `promptpotter/application/intelligence/adaptive_queue_mechanism.py`
(`build_round_order`, `pick_value`, `decision_information_gain`,
`delta_learning_gain`, `update_theta_posterior`, `expected_order`,
`marginal_hit_probability`).

## The shared round order (`build_round_order`)

Within a round, every candidate walks **one deterministic shared order**,
built once from the seed's per-sample outcomes (origin in round 1, the prior
winner in round 2+):

- **Seed-MISS samples first** (ascending δ — easiest win opportunities
  first). The round's actual decision is "can this candidate NET the
  adoption margin against the seed", and net movement only comes from
  discordant pairs — a candidate can only *win* where the seed missed. A
  live candidate proves itself immediately; a dead one's misses on the
  easiest wins are the strongest futility evidence the
  [θ ε-gate](../methods/candidate-elimination.md) can get.
- **A seed-HIT regression probe every 4th slot** (descending δ — likeliest
  regression points first), so losses accrue steadily and the θ ε-gate can
  kill regressors early instead of waiting for the tail.
- Samples the seed hasn't been measured on ride the MISS stratum (an
  unknown is a potential win, and fronting it warms the per-sample backfill
  earliest). Cold ruler → δ = 0; all ties break on ascending sample id.

The order is a pure function of (seed grades, δ ruler, sample ids), so a
resumed round re-derives it exactly — no recorded sidecar. Shared prefixes
across candidates keep the paired running stats comparable and the running
score display honest.

### Why not the online adaptive picker (deleted 2026-07-04)

The previous mechanism re-ranked the unscored samples after every
measurement by per-candidate information gain. Measured live (justlogic,
cycle_f72747c26407), it front-loaded **exactly the seed's hit set**: every
early paired comparison was a tie, p_best pinned at 0.5 (or spiked on one
lucky win), the raw-rate futility gate extrapolated an easy-prefix-inflated
hit rate — and across the whole cycle **zero eliminations ever fired**;
every dead candidate rode its full budget. Ordering for θ-measurement
efficiency and ordering for the keep/kill decision are different
objectives; the shared order optimizes the latter, which is what the
evaluation budget should buy down.

## Between-round CAT primitives (`pick_value`)

`pick_value = decision_information_gain + delta_learning_gain` (1PL Rasch,
nats) survives for **between-round** uses:

- `select_round_subset` (exploration.py) ranks the train bank with
  `expected_order` when `per_round_resubset` re-picks the scoring subset.
- The hard-samples artifact's `pick_score.per_sample` column — a
  round-boundary snapshot of how contested each sample is for a fresh
  mutation of the seed (ability prior centred at θ_seed). The artifact's
  `pick_score.sample_order` is `build_round_order` seeded by the best
  candidate — i.e. the order the engine will actually execute next round.

## Related concepts

* [`paired-sample-pobb.md`](paired-sample-pobb.md) — why the comparison
  needs backfilled, sample-keyed priors in the first place.
* [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) —
  the θ ε-gate this order feeds.
* [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md) —
  sample selection across the hard-sample leaderboard.
* [`../specs/verdict-resolution.md`](../specs/verdict-resolution.md) — the
  statistical model behind the pick-value objective.
