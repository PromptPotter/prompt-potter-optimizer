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

Candidates are evaluated sequentially on **Q** in the same deterministic
order. The first candidate runs to completion (*K* queries), establishing a
reference population. Each subsequent candidate is evaluated query by query;
once a minimum sample *n_min* (default 20) is reached, a **one-sided paired
Welch's *t*-test** is computed after every query against **all** previously
evaluated candidates on the shared query prefix.[^elim]

Holm-Bonferroni correction is applied across the pairwise tests. If any
corrected *p*-value falls below alpha (default 0.05), the candidate is stopped
early. The round winner is selected from all candidates (including
early-stopped) by composite score, subject to an improvement threshold
(default delta > 0.01).[^winner]

[^elim]: `services/search/sequential_elimination.py:should_stop_early()`.
[^winner]: `services/campaign/l1_optimizer.py:_select_round_winner()`.

### Design Rationale

**Paired design.** Query difficulty varies substantially — some queries resolve
via cache lookup, others require multi-step enrichment. Pairing on the same
query removes this nuisance variance. The paired design detects a 3–5% accuracy
difference with *n* = 50 queries, where an unpaired test requires *n* > 200.

**Welch's *t*-test.** Scores are continuous in [0, 1], and the *t*-test on
paired differences is robust to non-normality for *n* >= 20 (the minimum sample
floor). When the scoring formula reduces to binary hit/miss, the *t*-test on
{-1, 0, 1} differences remains valid, generalizing across formulas without a
test-selection branch.

**One-sided test.** The elimination question is directional ("is a prior
candidate better?"), so a one-sided test doubles power for detecting
inferiority. Candidates indistinguishable from prior populations survive.

**All priors, not just the leader.** A candidate worse than *any* prior
population is unlikely to win. Holm-Bonferroni controls FWER across the
multiple comparisons.

### Fallback

When *K* < 2 * *n_min*, early stopping is disabled and all candidates are
evaluated on the full query set.

## Limitations

- **Cluster-correlated queries** could inflate Type I error. The deterministic
  query order mitigates ordering effects but not latent correlation.
- **Binary scoring** makes the *t*-test valid but suboptimal — McNemar's test
  would be more natural for paired binary outcomes.
- **Candidate stationarity** is assumed (performance stable across queries).
  Holds by construction for stateless pipeline configurations.
