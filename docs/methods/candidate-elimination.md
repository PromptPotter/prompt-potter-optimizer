# Methods: Candidate Elimination

## Setting

At each optimization round, the system evolves *N* individuals (default *N* = 5) via an LLM meta-prompt.[^gen] Each individual's fitness is measured on a shared query set **Q** of size *K* (typically 50–500), producing a per-query score in [0, 1] aggregated as a mean composite score.[^scoring] The evaluation budget per round is *N* × *K* backend calls, which dominates wall-clock time. The population is pre-enumerated — there is no parameter space to search, only a fixed set to compare.

[^gen]: `promptpotter/application/optimization/l1.py::l1_generate()`.
[^scoring]: `promptpotter/application/scoring/formula.py::compile_scorer()` — user-defined formula compiled from `campaign.json`, e.g. `"rr(ground_truth_rank)"`.

## Individual elimination — Bayesian PoBB

**One-sentence method**: *stop a candidate when its posterior probability of being the round's best drops below ε.*

Individuals are evaluated sequentially on **Q** in the same deterministic order. The first individual runs to completion, establishing a reference. Each subsequent individual is measured query by query; once a minimum sample *n_min* (default 4) is reached, after every query we recompute each candidate's **Posterior-of-Being-Best** probability and stop the current candidate when its P(best) drops below ε (default 0.05).[^pobb]

Mechanics:

1. For each candidate (priors + current) we maintain a Normal posterior on its mean accuracy via CLT on observed per-sample scores: `mean = sample mean`, `variance = s² / n`. Variance is floored at `1/(4n)` (Beta-Binomial worst case) to prevent over-confidence on small-*n* binary scores.
2. Each query, draw `n_samples` (default 1000) joint samples from the per-candidate Normals (independent per candidate given its data).
3. For each candidate *c*, count the fraction of joint draws where *c*'s accuracy is argmax over the population — that's `P(c is best)`.
4. Stop candidate *c* when `P(c is best) < ε`.

[^pobb]: `promptpotter/shared/statistics.py::posterior_best_probabilities()` and `pobb_should_stop()`, driven by `promptpotter/application/optimization/elimination.py::PoBBCheck`.

## Two-regime analysis

**Both regimes will manifest** over the course of a campaign. PoBB is at-least-as-good-as Wilcoxon in every regime and strictly better in the early high-signal regime where over-investment costs the most.

### Early rounds — high-signal (candidates differ a lot)

Population members come from very different LLM-generated prompts; some clearly dominate. Scores have low within-candidate variance (consistent hits / consistent misses) and large between-candidate gaps. The Normal posterior tightens fast, the joint argmax distribution becomes lopsided within 3–5 queries, and `P(loser is best)` collapses below ε quickly. Worked numerics: with one candidate at accuracy 0.8 and another at 0.4, both at variance 0.05, `P(loser is best)` falls below 0.05 by query 4–5. Wilcoxon signed-rank on the same data needs ≥8 queries to fire at α=0.2 because it is variance-agnostic.

### Late rounds — low-signal (candidates have converged)

L2/L3 escalation has narrowed the population to similar prompts. True accuracy gaps are small (≤ 0.02). No statistical test can confidently abort: `P(c is best)` for all candidates hovers near 1/K (uniform across the population), and Wilcoxon stays above α. Both methods run candidates to budget cap. PoBB matches Wilcoxon — neither helps. The round winner is selected by point-estimate accuracy at cap (see [Open questions § Tie-breaking](#open-questions)).

### Why PoBB beats LUCB

LUCB-style pairwise tests (Kalyanakrishnan et al. 2012) only ever compare a candidate to one other (typically the leader). PoBB samples the joint posterior over **all** candidates and asks the actually-relevant question: "what's the probability *c* is the round winner?" Every candidate's data informs every other candidate's stopping decision through the argmax computation. Strictly more population-aware than LUCB; also strictly cheaper code-wise (~60 LOC vs ~120).

## Why Wilcoxon was retired

Wilcoxon signed-rank + Holm-Bonferroni was the prior abortion mechanism. Three reasons it lost its place:

1. **Pairwise, not population.** Wilcoxon compares the current candidate against each prior independently and Holm-corrects across the comparisons. It never sees the joint shape of the population's accuracy distributions. PoBB's joint posterior naturally aggregates across all candidates.
2. **Variance-agnostic.** Signed-rank uses ranks of paired differences. Two candidates with a clear gap and small variance get treated the same as two with the same gap and large variance. Empirical Bernstein and Normal-CLT both adapt to observed variance — they're tighter when scores are clearly separated.
3. **Operator-illegible.** Wilcoxon spits out p-values + Holm step-down indices that don't map onto operator intuition. P(best) is one number per candidate, displayable per-query in the live dashboard ("c042 73% probability of winning round").

The justification is positive: PoBB is the population-aware Bayesian best-arm-identification choice, well-grounded in the BAI literature (Russo 2016), and PoBB ≥ Wilcoxon in every regime our campaigns touch.

## Tunable knob — ε

`OptimizationConfig.pobb_epsilon` (default `0.05`). Smaller → more conservative (fewer stops). Empirical calibration vs Wilcoxon's α=0.2 baseline is pending the first BBEH run; do not lock the default in CLAUDE.md as canonical until we have data.

`OptimizationConfig.pobb_mc_samples` (default `1000`). Joint-draw count for the Monte Carlo argmax. Sub-ms per check; raise to 5000 only if observed stop decisions are noisy.

`OptimizationConfig.elimination_n_min` (default `4`). Floor on the candidate's query count before PoBB starts firing — below this, the Normal-CLT posterior isn't meaningful.

## Open questions

The following are explicitly **not** solved here; they are deferred until empirical data from BBEH/TermNorm runs informs the design.

1. **Tie-breaking at budget cap.** When the round cap is reached and the top 2–3 candidates have similar P(best) (e.g. each between 0.25 and 0.45 in a 3-way tie), no statistical test can declare a clean winner. We ship the simple version: pick top-by-point-estimate as the round winner. The proper fix is a *cap-extension policy* — when leaders' P(best) is statistically tied at cap, optionally extend their budget by Δ to attempt separation, but only if expected separation gain exceeds the cost. Designing that policy after observing how often this fires.
2. **ε default.** `0.05` is an educated initial pick. Empirical calibration pending — first BBEH run will tell us whether it's too conservative (PoBB barely fires) or too aggressive (round winners swap round-over-round).
3. **Normal-CLT edge cases.** The CLT approximation holds well for `n ≥ 4` paired observations on continuous scores. On pure binary hits the Normal is rough at small *n*; the variance floor `1/(4n)` is our mitigation. Revisit if BBEH shows pathology.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* Conference on Learning Theory. — Foundational for the PoBB / Top-Two Thompson Sampling family.
- **Maurer, A., & Pontil, M. (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT. — Variance-aware concentration; cited as the variance-tightening machinery PoBB displaces.
- **Kalyanakrishnan, S., Tewari, A., Auer, P., & Stone, P. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB best-arm-ID; compared and rejected as too pairwise.
- **Audibert, J.-Y., Bubeck, S., & Munos, R. (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.

---

## The full elimination ladder

Five independent mechanisms can end a candidate's evaluation early or annotate a query. They run in a fixed order and each owns its own memory field and display annotation. Maintainers tracing "why did this candidate die at n=1?" should walk this ladder from top to bottom.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `validation_failures` | `application/optimization/l1.py::score_population` |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | same candidate, annotated + possibly re-measured / swapped | — | `application/scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; synthesises `RuntimeFailure` | `runtime_failures` | `application/optimization/elimination.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; synthesises `RuntimeFailure` | `runtime_failures` | `application/optimization/elimination.py` |
| 5 | **`PoBBCheck`** (Bayesian Posterior-of-Being-Best vs completed priors) | every query | **4** | eliminated; records `elimination_cut` decision | — | `application/optimization/elimination.py` |

### Ordering inside the query loop

For each query, the scoring loop runs:

1. Prior-result cache lookup (may replay a cached result).
2. If result is degraded → `execute_stale_data_protocol` (may decorate with `degraded_observed`, trigger rerun/samplescan/sampleswitch, or return unchanged).
3. `on_result` fires → display renders the query line with annotations.
4. Iterate every enabled check in the shared `degradation_checks` list; first one to return a signal ends the candidate.

Mechanisms 3–5 all co-exist in that final list, so the *first-to-fire-wins* ordering inside the list matters. Fatal warnings beat any rate check; rate checks beat the PoBB gate.

### `classify_result()` is a hardcoded invariant

The classifier in `application/optimization/elimination.py` derives fatal codes from the backend's neutral advisory (e.g. `llm_only:content_empty`) and the raw response shape (`finish_reason`, `reasoning` token count) carried in `pipeline_data.step_tokens.{node}`. Initial rule table:

- `content_empty` + `finish_reason=length` + `reasoning_tokens > 0` → `reasoning_budget_exhausted`
- `content_empty` + `finish_reason=length` + `reasoning_tokens = 0` → `output_truncated`
- `content_empty` + any other `finish_reason` → `empty_response`
- `*:content_filtered` → passthrough as fatal

Fatal codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, so spending more backend calls to "confirm" is waste. The fast-path bypasses `min_queries` and `threshold` entirely. Grow the rule table (don't expose it as a tunable) when a new pattern proves equally conclusive.

Legacy archive alias: rows captured before TermNorm renamed the advisory carry `llm_only:empty_content_reasoning_fallback`; the classifier maps that directly to `reasoning_budget_exhausted` so resume on old cycles still deprecates correctly.

The classifier does triple duty: it drives mechanism 3 above (candidate elimination) **and** is consumed by `score_search_point::_filter_deprecated_priors` (cache eviction at load) and `_compute_accuracy` (primary-stats exclusion) — both via the `is_deprecated()` wrapper in `application/optimization/elimination.py`. See [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result--fatal-classification) for the three load-boundary effects and [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md#deprecated-samples) for the operator framing.
