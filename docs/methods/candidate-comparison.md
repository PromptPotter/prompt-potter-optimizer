# Methods: Candidate Comparison and Elimination

## Setting

At each optimization round, the system generates *N* candidate pipeline
configurations (default *N* = 5) via an LLM meta-prompt.[^gen] Each candidate
is evaluated on a shared query set **Q** of size *K* (typically 50–500),
producing a per-query score in [0, 1] aggregated as a mean composite
score.[^scoring] The evaluation budget per round is *N* x *K* backend calls,
which dominates wall-clock time. The candidates are pre-enumerated — there is no
parameter space to search, only a fixed set to compare.

[^gen]: `services/campaign/l1_optimizer.py:l1_generate()`.
[^scoring]: `shared/scoring.py:compile_scorer()` — user-defined formula
compiled from `campaign.json`, e.g. `"rr(ground_truth_rank)"`.

## Candidate Elimination

We compare candidates using **batched sequential elimination via one-sided
paired Welch's *t*-tests** with Holm-Bonferroni correction.

**Q** is shuffled with a deterministic seed and partitioned into *B* equal
batches (default *B* = 4, minimum 20 queries per batch).[^batches] All
candidates are evaluated on queries in the same order, enabling a paired design.

After each batch, the provisional leader (highest running mean score) is
identified. For each remaining candidate, we compute per-query score
differences d_t = s_leader(q_t) - s_candidate(q_t) and apply a one-sided
paired *t*-test (H_1: leader is better). Candidates whose Holm-Bonferroni-
corrected *p*-value falls below alpha (default 0.05) are eliminated and
excluded from subsequent batches.

After the final batch, the survivor with the highest composite score is
declared the round winner, subject to an improvement threshold
(default delta > 0.01).[^winner]

[^batches]: `services/search/sequential_elimination.py:plan_batches()`.
[^winner]: `services/campaign/l1_optimizer.py:_select_round_winner()`.

### Design Rationale

**Paired design.** Query difficulty varies substantially — some queries resolve
via cache lookup, others require multi-step enrichment. Pairing on the same
query removes this nuisance variance. The paired design detects a 3–5% accuracy
difference with *n* = 50 queries, where an unpaired test requires *n* > 200.

**Welch's *t*-test.** Scores are continuous in [0, 1], and the *t*-test on
paired differences is robust to non-normality for *n* >= 20 (our batch floor).
When the scoring formula reduces to binary hit/miss, the *t*-test on {-1, 0, 1}
differences remains valid, generalizing across formulas without a
test-selection branch.

**One-sided test.** The elimination question is directional ("is the leader
better?"), so a one-sided test doubles power for detecting inferiority.
Candidates indistinguishable from the leader survive — a conservative bias that
avoids discarding competitive alternatives.

**Holm-Bonferroni correction.** Up to *N* - 1 pairwise tests per checkpoint.
Holm-Bonferroni controls FWER while remaining more powerful than classical
Bonferroni.

### Fallback

When *K* < 40, batched elimination is disabled (insufficient data for meaningful
testing) and all candidates are evaluated on the full query set.

## Limitations

- **Cluster-correlated queries** could inflate Type I error. The deterministic
  shuffle breaks ordering effects but not latent correlation.
- **Binary scoring** makes the *t*-test valid but suboptimal — McNemar's test
  would be more natural for paired binary outcomes.
- **Candidate stationarity** is assumed (performance stable across batches).
  Holds by construction for stateless pipeline configurations.
