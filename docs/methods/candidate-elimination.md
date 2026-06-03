# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε.

## Setting

Each round evolves *N* individuals (default *N* = 5) via an LLM meta-prompt. Each is measured on a shared query set **Q** of size *K* (50–500), producing a per-sample score in `[0, 1]` aggregated as a mean composite. Evaluation budget per round is *N* × *K* backend calls, dominating wall-clock. Population is pre-enumerated — there's no parameter space to search, only a fixed set to compare.

## Sample iteration order — online adaptive queue mechanism

Within each candidate, samples are selected **per step** by an online 1PL Item Response Theory adaptive queue mechanism (`promptpotter/application/intelligence/adaptive_queue_mechanism.py`). The hierarchical Rasch posterior — the sample-difficulty "leaderboard" — is **re-fit on every measurement** over the dataset-scoped archive plus everything scored so far this run, so a sample found informative mid-round is recognised immediately rather than at the next round boundary. The queue mechanism maintains a Gaussian-approximation posterior on the candidate's latent ability `θ_c` and re-picks the next sample after every measurement to maximize a one-step-greedy objective — the **pick-value**, in nats:

- **decision information gain** — `I(Y_s ; verdict)`, the mutual information between the next outcome and the keep/abort verdict `θ_c > θ_s` against the seed. Picks the sample whose outcome most moves the decision; the means-known limit recovers Bernoulli Chernoff information.

`pick_value = decision_information_gain` — a single objective (the earlier blended `+ explore_weight · model_information_gain` explore term was dropped 2026-05; see [`../specs/verdict-resolution.md`](../specs/verdict-resolution.md)). The seed is origin in R1 and the prior round winner R2+.

Both the per-step queue mechanism and the round-subset selector (`select_round_subset`) **centre the candidate's ability prior on the seed** — a mutation is a small edit of its parent, so it starts at the parent's ability `θ_seed`, not the population-mean anchor 0. This is load-bearing: centred at 0 the decision term goes flat (no sample is contested for a candidate that could be anywhere) and the round sweeps up a fresh contiguous block of never-measured samples every round instead of re-measuring the contested band. Detail and tradeoff: [`../concepts/paired-sample-pobb.md`](../concepts/paired-sample-pobb.md#sample-selection-online-adaptive-queue-mechanism).

## Bayesian PoBB

Individuals are evaluated sequentially on **Q**, with the adaptive queue mechanism choosing each candidate's next sample per measurement. The first candidate runs to completion, establishing a reference. Each subsequent candidate is measured query by query; once `n_min` (default 4) is reached, after every query we recompute each candidate's **Posterior-of-Being-Best** probability and stop the current candidate when its P(best) drops below ε (default 0.05).

A pure-arithmetic **dominance gate** fires before the Bayesian posterior: if `cand_hits + (budget − queries_scored) < seed_total_hits`, the candidate can't catch the seed prior even by hitting every remaining sample. Eliminate immediately. This is SPRT's deterministic corner (probability of catching up = 0) — stronger than the posterior gate, so it's checked first.

Mechanics:

1. For each candidate (priors + current) maintain a Normal posterior on its mean accuracy via CLT on observed scores: `mean = sample mean`, `variance = s² / n`. Variance floored at `1/(4n)` (Beta-Binomial worst case) to prevent over-confidence on small-*n* binary scores.
2. Each query, draw `n_samples` (default 1000) joint samples from per-candidate Normals (independent per candidate given its data).
3. For each candidate *c*, count the fraction of joint draws where *c*'s accuracy is argmax — that's `P(c is best)`.
4. Stop candidate *c* when `P(c is best) < ε`.

Code: `promptpotter/shared/statistics.py::posterior_best_probabilities()` and `pobb_should_stop()`, driven by `application/optimization/pobb/elimination/checks.py::PoBBCheck`.

## Two regimes

**Both manifest** over a campaign. PoBB is at-least-as-good-as Wilcoxon in every regime and strictly better in early high-signal where over-investment costs the most.

- **Early — high-signal.** LLM-generated prompts differ a lot; some clearly dominate. Within-candidate variance low, between-candidate gaps large. Normal posterior tightens fast; argmax becomes lopsided within 3–5 queries. Example: candidates at 0.8 vs 0.4 accuracy, both variance 0.05 → `P(loser is best)` < 0.05 by query 4–5. Wilcoxon needs ≥ 8 queries at α=0.2 because it's variance-agnostic.
- **Late — low-signal.** L2/L3 escalation has narrowed the population. True gaps ≤ 0.02. No test can confidently abort; both methods run to budget cap. Round winner selected by point-estimate accuracy at cap.

PoBB beats LUCB-style pairwise tests by sampling the joint posterior over **all** candidates and asking the actually-relevant question. Population-aware; ~60 LOC vs LUCB's ~120.

## Tunable knobs

- `OptimizationConfig.pobb_epsilon` (default `0.05`) — smaller = more conservative.
- `OptimizationConfig.elimination_n_min` (default `4`) — floor on query count before PoBB fires. Below this, Normal-CLT posterior isn't meaningful.

## Open questions

Deferred until empirical data informs the design.

1. **Tie-breaking at budget cap.** When the round cap is reached and top 2–3 candidates have similar P(best) (e.g. each between 0.25 and 0.45), no test declares a clean winner. Ship pick-by-point-estimate; design a cap-extension policy after observing how often this fires.
2. **ε default.** `0.05` is an educated initial pick. First BBEH run will tell whether too conservative (PoBB barely fires) or too aggressive (round winners swap round-over-round).
3. **Normal-CLT edge cases.** The CLT approximation holds well for `n ≥ 4` paired observations on continuous scores. On pure binary hits the Normal is rough at small *n*; the variance floor `1/(4n)` is the mitigation.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — Foundational for the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; rejected as too pairwise.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.

---

## The full elimination ladder

Five independent mechanisms can end a candidate's evaluation early or annotate a query. Fixed order; each owns its own memory field and display annotation.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.wounds.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `wounds.validation_failures` | `application/optimization/l1/score/loop.py::score_population` |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | annotated + possibly re-measured / swapped | — | `application/scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `application/optimization/pobb/elimination/checks.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `application/optimization/pobb/elimination/checks.py` |
| 5 | **`PoBBCheck` — dominance** — `cand_max_final_hits < seed_total_hits` | every query | **1** | eliminated; `elimination_cut` with `data.dominance` | — | `application/optimization/pobb/elimination/checks.py::_dominance_check` |
| 6 | **`PoBBCheck` — Bayesian** — paired `P(best) < ε` | every query | **4** | eliminated; records `elimination_cut` decision | — | `application/optimization/pobb/elimination/checks.py` |

**Ordering inside the query loop.** For each query: (1) prior-result cache lookup; (2) if degraded → `execute_stale_data_protocol`; (3) `on_result` fires → display renders the line; (4) iterate every enabled check in `degradation_checks`; first to return a signal ends the candidate. Mechanisms 3–6 co-exist in that final list — fatal beats rate beats dominance beats Bayesian PoBB. Inside `PoBBCheck.check()` the dominance gate runs first (pure arithmetic) and the Bayesian posterior runs only if dominance didn't fire.

The `classify_result()` rule table and its three load-boundary effects: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result--fatal-classification). Operator framing: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
