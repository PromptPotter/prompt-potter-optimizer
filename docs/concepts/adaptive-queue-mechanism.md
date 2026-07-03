# Online Adaptive Queue Mechanism

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

[Paired-sample PoBB](paired-sample-pobb.md) makes the mid-round elimination
comparison statistically valid by backfilling priors on the candidate's
sample order; this page covers what decides that order in the first place —
the per-candidate **iteration order** that makes the comparison cheap.

Code: `promptpotter/application/intelligence/adaptive_queue_mechanism.py`
(`update_theta_posterior`, `decision_information_gain`, `pick_value`,
`next_sample`, `expected_order`).

## The mechanism

The adaptive queue mechanism is a 1PL Item Response Theory online sequential
selector — at each step it folds the candidate's measured `(δ_s, se_δ_s,
hit)` outcomes into a Gaussian Laplace-approximation posterior on `θ_c`, then
picks the next sample by maximizing a one-step-greedy objective — the
**pick-value**, in nats:

- **decision information gain:** `I(Y_s ; verdict)`,
  the mutual information between the next outcome and the keep/abort
  verdict `θ_c > θ_s` against the seed (`μ_s` the seed prior's fitted
  ability). Picks the sample whose outcome maximally separates candidate
  from seed — directly minimizes expected queries to a confident verdict.
  The means-known limit recovers Bernoulli Chernoff information
  (Garivier-Kaufmann 2016).

`pick_value = decision_information_gain` — a single objective (the earlier
blended `+ explore_weight · model_information_gain` explore term was dropped
2026-05; see [`../specs/verdict-resolution.md`](../specs/verdict-resolution.md)).
PoBB *is* a keep/abort decision and that's what the evaluation budget should
buy down.

The heatmap's hardest-first `sample_order` (the spec's `|δ_s|` sort)
lives on the per-cycle artifact for display; the artifact's
`pick_score.per_sample` is the blended pick-value for a fresh mutation of
the seed — ability prior centred on the seed's ability `θ_seed`, not the
population-mean anchor 0 — a descriptive snapshot of "how informative is
this sample on a brand-new candidate." The **live** adaptive queue
mechanism uses its own per-candidate posterior, so the artifact's order
and the candidate's actual measurement order can diverge — the artifact
is what the operator sees in the webapp, the live order is what the
candidate ran against on disk.

Why this beats a static "hardest first" iteration: hardest-first treats
the queue mechanism as a property of the *dataset* (Rasch δ alone). The
adaptive queue mechanism treats it as a property of the
*candidate-vs-seed comparison*, which is what PoBB actually tests. The
decision term gives zero score to samples where the seed and candidate
are predicted to agree (both unanimous-HIT or both unanimous-MISS)
regardless of how hard they look on the δ axis; samples in the gap
between `μ̂_c` and `μ_s` carry maximum info — and the queue mechanism
re-evaluates that gap after every measurement, so the order is
*responsive* to the candidate's running evidence rather than frozen at
candidate-start.

## Related concepts

* [`paired-sample-pobb.md`](paired-sample-pobb.md) — why the comparison
  needs backfilled, sample-keyed priors in the first place.
* [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md) —
  sample selection across the hard-sample leaderboard.
* [`../specs/verdict-resolution.md`](../specs/verdict-resolution.md) — the
  statistical model behind the pick-value objective.
