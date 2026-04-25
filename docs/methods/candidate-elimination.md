# Methods: Candidate Elimination

## Setting

At each optimization round, the system generates *N* candidate pipeline configurations (default *N* = 5) via an LLM meta-prompt.[^gen] Each candidate is evaluated on a shared query set **Q** of size *K* (typically 50–500), producing a per-query score in [0, 1] aggregated as a mean composite score.[^scoring] The evaluation budget per round is *N* × *K* backend calls, which dominates wall-clock time. The candidates are pre-enumerated — there is no parameter space to search, only a fixed set to compare.

[^gen]: `promptpotter/application/optimization/nodes/l1/generate.py::l1_generate()`.
[^scoring]: `promptpotter/shared/scoring.py::compile_scorer()` — user-defined formula compiled from `campaign.json`, e.g. `"rr(ground_truth_rank)"`.

## Candidate elimination

Candidates are evaluated sequentially on **Q** in the same deterministic order. The first candidate runs to completion (*K* queries), establishing a reference population. Each subsequent candidate is evaluated query by query; once a minimum sample *n_min* (default 20) is reached, a **one-sided paired Wilcoxon signed-rank test** is computed after every query against **all** previously evaluated candidates on the shared query prefix.[^elim]

Holm-Bonferroni correction is applied across the pairwise tests. If any corrected *p*-value falls below alpha (default 0.05), the candidate is stopped early. The round winner is selected from all candidates (including early-stopped) by composite score, subject to an improvement threshold (default delta > 0.01).[^winner]

[^elim]: `promptpotter/shared/statistics.py::should_stop_early()` (driven by `promptpotter/application/optimization/elimination.py`).
[^winner]: `promptpotter/application/optimization/nodes/l1/winner.py::select_round_winner()`.

---

## Design rationale

**Paired design.** Query difficulty varies substantially — some queries resolve via cache lookup, others require multi-step enrichment. Pairing on the same query removes this nuisance variance. The paired design detects a 3–5% accuracy difference with *n* = 50 queries, where an unpaired test requires *n* > 200.

**Wilcoxon signed-rank.** Per-query scores in PromptPotter are often binary (`{0, 1}`) or concentrated near the endpoints, so paired differences have heavy mass at zero and the normal approximation used by the paired *t*-test is weak exactly at the small-*n* regime where elimination fires. The signed-rank test is the paired, non-parametric analogue: it drops the normality assumption, keeps the per-query pairing that makes the design powerful, and reduces to the sign test (with continuity correction) on `{-1, 0, +1}` differences — subsuming the binary case without a test-selection branch.

**One-sided test.** The elimination question is directional ("is a prior candidate better?"), so a one-sided test doubles power for detecting inferiority. Candidates indistinguishable from prior populations survive.

**All priors, not just the leader.** A candidate worse than *any* prior population is unlikely to win. Holm-Bonferroni controls FWER across the multiple comparisons.

## Fallback

When *K* < 2 × *n_min*, early stopping is disabled and all candidates are evaluated on the full query set.

## Limitations

- **Cluster-correlated queries** could inflate Type I error. The deterministic query order mitigates ordering effects but not latent correlation.
- **Candidate stationarity** is assumed (performance stable across queries). Holds by construction for stateless pipeline configurations.

---

## The full elimination ladder

Six independent mechanisms can end a candidate's evaluation early or annotate a query. They run in a fixed order and each owns its own memory field and display annotation. Maintainers tracing "why did this candidate die at n=1?" should walk this ladder from top to bottom.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.memory.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `memory.validation_failures` | `application/optimization/nodes/l1/measure.py::score_candidates` |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | same candidate, annotated + possibly re-measured / swapped | — | `application/scoring/stale_data.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query carries a `FATAL_WARNINGS` code | every query | **1** | eliminated; synthesises `RuntimeFailure` | `memory.runtime_failures` | `application/optimization/elimination.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; synthesises `RuntimeFailure` | `memory.runtime_failures` | `application/optimization/elimination.py` |
| 5 | **`EmptyOutputCheck`** — `empty_predicted_rate >= threshold` | every query | **3** | eliminated | — | `application/optimization/elimination.py::EmptyOutputCheck` |
| 6 | **`EliminationCheck`** (Wilcoxon signed-rank vs completed priors) | every query | **4** | eliminated; records `elimination_cut` decision | — | `application/optimization/elimination.py` |

### Ordering inside the query loop

For each query, the scoring loop runs:

1. Prior-result cache lookup (may replay a cached result).
2. If result is degraded → `execute_stale_data_protocol` (may decorate with `degraded_observed`, trigger rerun/samplescan/sampleswitch, or return unchanged).
3. `on_result` fires → display renders the query line with annotations.
4. Iterate every enabled check in the shared `degradation_checks` list; first one to return a signal ends the candidate.

Mechanisms 3–6 all co-exist in that final list, so the *first-to-fire-wins* ordering inside the list matters. Fatal warnings beat any rate check; rate checks beat the Wilcoxon signed-rank gate.

### `FATAL_WARNINGS` is a hardcoded invariant

`FATAL_WARNINGS = frozenset({"llm_only:empty_content_reasoning_fallback"})`. These codes are deterministic for the whole config — one sighting proves the candidate is broken for every remaining query, so spending more backend calls to "confirm" is waste. The fast-path bypasses `min_queries` and `threshold` entirely. Grow this set (don't expose it as a tunable) when a new warning proves equally conclusive.
