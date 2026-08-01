# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε.

## Setting

Each round evolves *N* individuals (default *N* = 5) via an LLM optimizer prompt. Each is measured on a shared query set **Q** of size *K* (50–500), producing a per-sample score in `[0, 1]` aggregated as a mean composite. Evaluation budget per round is *N* × *K* backend calls, dominating wall-clock. Population is pre-enumerated — there's no parameter space to search, only a fixed set to compare.

## Sample iteration order — the shared round order

Every candidate in a round walks **one deterministic shared order**, built once from the seed's per-sample outcomes (`build_round_order`, `promptpotter/application/intelligence/adaptive_queue_mechanism.py`): seed-MISS samples first (ascending δ — the only place a candidate can *win*, so both futility evidence and the deterministic-exhaustion bound accrue fastest), a seed-HIT regression probe every 4th slot (descending δ — likeliest regression points first, feeding losses to the gates early), unknowns riding the MISS stratum. The order is a pure function of (seed grades, δ ruler, sample ids) — resume re-derives it exactly; shared prefixes keep the paired stats comparable across candidates.

The previous per-candidate online CAT re-rank was deleted 2026-07-04: measured live it front-loaded the seed's exact hit set (zero-information ties), pinning p_best at 0.5 and blinding every gate until the tail — zero eliminations across a full cycle. Detail: [`../concepts/adaptive-queue-mechanism.md`](../concepts/adaptive-queue-mechanism.md).

## Bayesian PoBB

Individuals are evaluated sequentially on **Q** in the shared round order. The first candidate runs to completion, establishing a reference. Each subsequent candidate is measured query by query; once `elimination_n_min` is reached, after every query we recompute each candidate's **Posterior-of-Being-Best** probability and stop the current candidate when it falls below ε.

Mechanics — P(best) is computed on **difficulty-adjusted ability θ**, the same metric the round-winner election ranks by (so mid-round elimination and end-round election never disagree on what "better" means):

1. Build the candidate's paired comparison set: each prior is backfilled onto the candidate's exact samples, so every arm has a hit on the same (hard-first) sample IDs (priors that can't be caught up are excluded, never zero-filled).
2. One joint 1PL Rasch fit over the candidate + every paired prior yields each arm's ability `θ` and its Laplace `se` on a shared difficulty scale.
3. For each prior, `P(θ_cand > θ_prior) = Φ(Δθ / √(se_c² + se_p²))` — closed-form, no Monte Carlo. `p_best = min` over priors (bounded above by the hardest prior).
4. Stop the candidate when `p_best < ε`.

Because the comparison is difficulty-adjusted, it stays valid across partial prefixes (an eliminated candidate stops early) — raw hit-rate would crown whoever banked the easy samples, θ does not.

Code: `application/scoring/metrics.py::elimination_p_best` (the shared θ rule on the cycle's fixed δ ruler, used by live `check()` and the resume replayer), driven by `application/optimization/pobb/checks.py::PoBBCheck`. Cross-cycle/engine comparison is the deterministic A/B replay engine (`resume_and_fork/ab_replay.py`, the `ab` verb) — it re-derives recorded decisions under the current engine, no new measurements.

## Two regimes

**Both manifest** over a campaign. PoBB is at-least-as-good-as Wilcoxon in every regime and strictly better in early high-signal where over-investment costs the most.

- **Early — high-signal.** LLM-generated prompts differ a lot; some clearly dominate. Between-candidate ability gaps large. The θ posteriors separate fast; `P(cand > prior)` becomes lopsided within 3–5 queries. Example: candidates at 0.8 vs 0.4 hit-rate → `P(loser > leader)` < 0.05 by query 4–5. Wilcoxon needs ≥ 8 queries at α=0.2 because it's variance-agnostic.
- **Late — low-signal.** L2/L3 escalation has narrowed the population. True gaps ≤ 0.02. The Bayesian *best*-test cannot confidently abort a near-tie (P(best) ≈ 0.5), so a tie rides to the sample cap and the winner is picked by the θ election. A futility gate used to cut those early; it was deleted for the reasons in the table above, and buying that back means one gate on the θ ruler, never a second comparator beside it.

PoBB beats LUCB-style pairwise tests by sampling the joint posterior over **all** candidates and asking the actually-relevant question. Population-aware; ~60 LOC vs LUCB's ~120.

## Tunable knobs

- `OptimizationConfig.pobb_epsilon` (default `0.15`, `POBB_DEFAULT_EPSILON`) — smaller = more conservative. The one ε: "stop measuring a candidate whose probability of being the round's best is below ε". A stop ends measurement; it is **not** a verdict, and never removes the candidate from the election (`is_leader_eligible`).
- `OptimizationConfig.elimination_n_min` (default `6`) — the single min-samples floor. It gates PoBB (below this a candidate's θ posterior is too under-determined to act on) **and** the difficulty-ruler warmth: the per-cycle δ ruler stays flat (δ≡0 ⇒ θ = logit-accuracy) until at least this many grade-A samples are banked. Difficulty and ability become trustworthy at the same evidence threshold — one knob, no separate ruler-only constant.

## Open questions

Deferred until empirical data informs the design.

1. **Tie-breaking at budget cap.** When the round cap is reached and top 2–3 candidates have similar P(best) (e.g. each between 0.25 and 0.45), no test declares a clean winner. Ship pick-by-point-estimate; design a cap-extension policy after observing how often this fires.
2. **ε default.** `0.05` is an educated initial pick. First BBEH run will tell whether too conservative (PoBB barely fires) or too aggressive (round winners swap round-over-round).
3. **Small-*n* θ edge cases.** With few observations the Laplace `se` on θ is wide, so `p_best` sits near 0.5 and the gate stays conservative (won't eliminate) until evidence accumulates — the EB hyperprior on the ability variance is what keeps the small-*n* fit from collapsing.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — Foundational for the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; rejected as too pairwise.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.

---

## The full elimination ladder

Six independent mechanisms can end a candidate's evaluation early or annotate a query. Fixed order; each owns its own memory field and display annotation.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.wounds.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `wounds.validation_failures` | `application/optimization/l1/score/loop.py::score_population` |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | annotated + possibly re-measured / swapped | — | `application/scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `application/optimization/pobb/checks.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `application/optimization/pobb/checks.py` |
| 5 | **`PoBBCheck` — Bayesian** — paired `P(best) < ε` | every query | `n_min` | eliminated; records `elimination_cut` decision | — | `application/optimization/pobb/checks.py` |

**Ordering inside the query loop.** For each query: (1) prior-result cache lookup; (2) if degraded → `execute_stale_data_protocol`; (3) `on_result` fires → display renders the line; (4) iterate every enabled check in `degradation_checks`; first to return a signal ends the candidate. Mechanisms 3–5 co-exist in that final list — fatal beats rate beats Bayesian PoBB.

**There was a sixth: a paired-margin futility gate, deleted 2026-07-27.** It was a second comparator beside the θ ruler — counting discordant binary wins while the election ranked on difficulty-adjusted ability — and a second encoding of `improvement_threshold`, with a duplicate implementation in the replayer. It was also inert on a graded backend, where a fitness of 0.63 is neither a win nor a loss. Its kill payload stamped a hardcoded `p_best: 0.0`, which `is_leader_eligible` read as a PoBB loss and so barred the candidate from the round election: on `promptpotter-self` every candidate in rounds 2–3 stopped that way and four rounds closed with no winner while the real θ lift was **+0.099**. One comparator, one stop rule.

The `classify_result()` rule table and its three load-boundary effects: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result--fatal-classification). Operator framing: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
