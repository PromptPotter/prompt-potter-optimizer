# Methods: Candidate Comparison and Elimination

## Setting

At each optimization round, the system generates *N* candidate pipeline
configurations (default *N* = 5) via an LLM meta-prompt.[^gen] Each candidate
is evaluated on a shared query set **Q** of size *K* (typically 50–500),
producing a per-query score in [0, 1] aggregated as a mean composite
score.[^scoring] The evaluation budget per round is *N* x *K* backend calls,
which dominates wall-clock time. The candidates are pre-enumerated — there is no
parameter space to search, only a fixed set to compare.

[^gen]: `promptpotter/application/optimization/nodes/generate.py:l1_generate()`.
[^scoring]: `promptpotter/shared/scoring.py:compile_scorer()` — user-defined formula
compiled from `campaign.json`, e.g. `"rr(ground_truth_rank)"`.

## Candidate Elimination

Candidates are evaluated sequentially on **Q** in the same deterministic
order. The first candidate runs to completion (*K* queries), establishing a
reference population. Each subsequent candidate is evaluated query by query;
once a minimum sample *n_min* (default 20) is reached, a **one-sided paired
Wilcoxon signed-rank test** is computed after every query against **all**
previously evaluated candidates on the shared query prefix.[^elim]

Holm-Bonferroni correction is applied across the pairwise tests. If any
corrected *p*-value falls below alpha (default 0.05), the candidate is stopped
early. The round winner is selected from all candidates (including
early-stopped) by composite score, subject to an improvement threshold
(default delta > 0.01).[^winner]

[^elim]: `promptpotter/shared/statistics.py:should_stop_early()` (driven by `promptpotter/application/optimization/elimination.py`).
[^winner]: `promptpotter/application/optimization/nodes/score.py:_select_round_winner()`.

### Design Rationale

**Paired design.** Query difficulty varies substantially — some queries resolve
via cache lookup, others require multi-step enrichment. Pairing on the same
query removes this nuisance variance. The paired design detects a 3–5% accuracy
difference with *n* = 50 queries, where an unpaired test requires *n* > 200.

**Wilcoxon signed-rank.** Per-query scores in PromptPotter are often binary
(`{0, 1}`) or concentrated near the endpoints, so paired differences have
heavy mass at zero and the normal approximation used by the paired *t*-test
is weak exactly at the small-*n* regime where elimination fires. The
signed-rank test is the paired, non-parametric analogue: it drops the
normality assumption, keeps the per-query pairing that makes the design
powerful, and reduces to the sign test (with continuity correction) on
`{-1, 0, +1}` differences — subsuming the binary case without a
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
- **Candidate stationarity** is assumed (performance stable across queries).
  Holds by construction for stateless pipeline configurations.



# Rasch + KG, not heuristics

Rasch (`application/intelligence/rasch.py`) is the joint logistic-IRT model `P(hit_{c,s} = 1) = σ(θ_c − δ_s)`: candidate ability × sample difficulty. Joint MAP via alternating Newton on the sparse observation matrix; Laplace standard errors for posterior CIs. Anchored to `mean(θ) == 0` for identifiability. The fit gives a first-class **sample-difficulty parameter** (`δ_s`, surfaces directly as the hardness leaderboard) and a first-class **candidate-ability parameter** (`θ_c`).

Knowledge Gradient is the one-step Bayesian acquisition function: how much would measuring `(c, s)` shift our point estimate of the best candidate? Closed-form for Bernoulli observations under Laplace.

All swap decisions reduce to **float thresholds on these statistical quantities**:
- `swap_out_delta_se` — SE on `δ_s` below which the sample is "understood" (default 0.25 logits ≈ 95% CI width 1.0).
- `swap_in_kg_threshold` — minimum `KG(s)` to be swap-in eligible (default 0.01).
- `max_swaps_per_round` — cap on prefix churn per round (default 3).
- `min_prefix_size` — floor on prefix size; never drops below `elimination_n_min` (defaults to 4).

### Relationship to Wilcoxon

`EliminationCheck` is created **fresh inside `score_candidates()` per round** — Wilcoxon priors are per-round-internal, not cross-round. Adaptive prefix changes the slice between rounds, but within any given round all candidates score the same prefix so the paired-test invariant holds. The two mechanisms run at different cadences answering different questions:

| Mechanism | Cadence | Question | Statistical tool |
|---|---|---|---|
| `EliminationCheck` (Wilcoxon) | Mid-evaluation, every query after `n_min` | Is this in-progress candidate decisively worse than the round's completed priors? | Paired signed-rank, Holm-Bonferroni |
| `evolve_prefix` (Rasch + KG) | Once per round, between rounds | Which samples should the next round score to maximize information gain about the best candidate? | MAP fit + closed-form one-step KG |

The Wilcoxon gate stays untouched. A future iteration could replace it with a Rasch-posterior elimination (`P(θ_c < θ_winner | data) > 0.95`); out of scope today.