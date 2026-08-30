# Candidate Elimination

**Method:** stop a candidate when its posterior probability of being the round's best drops below ε.

## Setting

Each round evolves *N* individuals (default *N* = 5) via an LLM optimizer prompt. Each is measured on a shared query set **Q** of size *K* (50–500), producing a per-sample score in `[0, 1]` aggregated as a mean composite. Evaluation budget per round is *N* × *K* backend calls, dominating wall-clock. Population is pre-enumerated — there's no parameter space to search, only a fixed set to compare.

The statistical model underneath — Rasch θ/δ, the graded response, the `√φ` SE correction — is owned by [`verdict-resolution.md`](verdict-resolution.md), and so is the **round order** this page depends on one property of: it is *shared, never re-ranked per candidate*, so shared prefixes keep the paired stats comparable. A per-candidate re-rank front-loads the seed's own hit set and blinds every gate here until the tail.

## Why the comparison has to be paired

PoBB (Russo 2016) assumes every arm is observed on an i.i.d. sample of one distribution. The shared round order deliberately violates that: it front-loads the decision-relevant samples (the seed's misses — the only place a candidate can win) so a dead candidate is abandoned within a handful of queries. Unpaired, two arms measured on near-disjoint sets get compared as though they were iid — a leader that ran only the easy prefix reads as unbeatable, and every later candidate, measured hard-first, is eliminated against a rate the leader never had to earn.

The fix is one design choice with several call-site consequences: **PoBB priors are sample-keyed, and the leader is backfilled onto the candidate's upcoming samples before each comparison.**

- **Priors are stored sample-keyed**, not as flat vectors: `priors_by_sample: cid → sample_id → graded fitness`, plus `prior_sps: cid → JobSearchPoint` so a prior's measurements can be extended later. `register_completed` ingests full `QueryMeasurement`s and excludes error/deprecated samples.
- **Backfill is reactive, per sample.** `PoBBCheck` takes a `backfill_fn`; the query loop fires it after each sample lands, before degradation checks read prior coverage. `backfill_for_sample` is idempotent — priors already covering the id are skipped and the telemetry event suppresses itself when everything was cached. Priors are caught up sample-by-sample as the candidate measures them, so paired comparison always sees current priors without a full-dataset upfront wall, and a candidate eliminated early never pays for coverage it won't reach. New pairs land in `measurements/` and are reusable by every future round.
- **A backfill row is not a panel row.** It is stamped with the PRIOR's identity and `MeasurementRole.BACKFILL` (`shared/instrument.py`) — the closure receives the prior's id precisely so it cannot inherit the foreground candidate's, which is how two backfills that ran C1.1's prompts came to be recorded as C1.2's. Reuse for a *paired comparison* is the point; reuse as some candidate's own **panel** evidence is not, because a backfill is measured outside the round's shared order — resume's hole repair refuses it and re-measures.
- **What it costs.** Backfill runs per candidate over its sample order (~6–10 hard samples). The
  round's first candidate pays a few fresh leader measurements; later candidates' orders overlap
  heavily and hit the cache, and by round 3+ the leader has near-full coverage. Net: roughly **one
  extra candidate-equivalent of LLM spend per round** — the price of statistical validity.
- **Lucky-prefix inflation is self-correcting.** Backfill forces a locked leader onto the candidate's hard-first order, so its recorded mean deflates toward its true rate before the comparison. No threshold to lower, no display patch: the inflation was a mechanical consequence of unpaired comparison and paired comparison mechanically removes it.

## The θ rule

Individuals are evaluated sequentially on **Q** in the shared round order. The first candidate runs to completion, establishing a reference. Each subsequent candidate is measured query by query; once `elimination_n_min` is reached, after every query:

1. Build the paired comparison set — each prior mapped onto the candidate's exact sample ids. Priors that cannot be caught up are **excluded, never zero-filled**.
2. One joint 1PL Rasch fit over the candidate + every paired prior yields each arm's ability `θ` and its Laplace `se` on the cycle's fixed δ ruler.
3. `P(θ_cand > θ_prior) = Φ(Δθ / √(se_c² + se_p²))` — closed-form, no Monte Carlo. `p_best = min` over priors (bounded above by the hardest prior).
4. Stop when `p_best < ε(n)` — ε is graded by depth, not scalar (see `pobb_epsilon_floor`).

This is the **same difficulty-adjusted ability the round-winner election ranks by**, so mid-round elimination and end-round election cannot disagree about what "better" means — and because it is difficulty-adjusted it stays valid across partial prefixes, where a raw hit-rate would crown whoever banked the easy samples. The pairing still earns its keep: backfill guarantees priors have outcomes on the candidate's *contested* samples, which is exactly where the θ comparison gets its information.

Code: `application/scoring/selection.py::elimination_p_best` (the one θ rule, shared by live `check()` and the resume replayer), driven by `application/optimization/pobb/checks.py::PoBBCheck`. Cross-cycle comparison is the deterministic A/B replay engine (`resume_and_fork/ab_replay.py`, the `ab` verb) — it re-derives recorded decisions under the current engine, no new measurements.

## Two regimes

**Both manifest** over a campaign.

- **Early — high-signal.** LLM-generated prompts differ a lot; some clearly dominate. The θ posteriors separate fast and `P(cand > prior)` becomes lopsided within 3–5 queries. Wilcoxon needed ≥8 queries at α=0.2 because it is variance-agnostic.
- **Late — low-signal.** L2/L3 escalation has narrowed the population and true gaps are ≤0.02. The Bayesian *best*-test cannot confidently abort a near-tie (`P(best) ≈ 0.5`), so a tie rides to the sample cap and the winner is picked by the θ election.

PoBB beats LUCB-style pairwise tests by sampling the joint posterior over **all** candidates and asking the actually-relevant question — population-aware, not pairwise.

## Tunable knobs

- `OptimizationConfig.pobb_epsilon` (default `0.15`, `POBB_DEFAULT_EPSILON`) — smaller = more conservative. The one ε: "stop measuring a candidate whose probability of being the round's best is below ε". A stop ends measurement; it is **not** a verdict, and never removes the candidate from the election (`is_leader_eligible`).
- `OptimizationConfig.pobb_epsilon_floor` (default `0.15`, `POBB_DEFAULT_EPSILON`) — the ε applied at **both ends** of the panel, ramping linearly to `pobb_epsilon` over `elimination_n_min` cells on each side and holding it in between (`PoBBCheck.epsilon_at`). Equal to `pobb_epsilon` — the default — leaves the bar flat and elimination bit-identical, so grading exists only where ε was deliberately raised above it. Why it is not one scalar: **the bar tracks what cutting still saves.** At the floor a single discordant sample already drives `p_best` to ~0.2, so a lone ε is too eager there; deep in the panel there is almost nothing left to save, so it is too permissive. Measured on `justlogic-d234` (ε raised to 0.30) three arms were cut at n=6 on exactly one discordant loss apiece — `p_best` 0.199, 0.2989, 0.2989 — while on `__960ea6` r2 an arm died at **q26 of 34 holding `p_best` 0.275**, three cells before the tail guard would have protected it, and the round then reported "no arm cleared the parent" on an arm it had discarded rather than resolved. The ramp-out lands on the floor exactly where that guard begins, so the two meet instead of cliffing. Aggression belongs here and never in `elimination_n_min`, which also gates ruler warmth. A floor set above `pobb_epsilon` would grade the bar downward; the ramp goes flat at `pobb_epsilon` instead and the `epsilon_floor_inverted` coupling reports it.
- `OptimizationConfig.elimination_n_min` (default `6`) — the single min-samples floor. It gates PoBB (below it a candidate's θ posterior is too under-determined to act on) **and** the difficulty-ruler warmth: the per-cycle δ ruler stays flat (δ≡0 ⇒ θ = logit-accuracy) until at least this many grade-A samples are banked. Difficulty and ability become trustworthy at the same evidence threshold — one knob, no separate ruler-only constant.

## The full elimination ladder

Five independent mechanisms can end a candidate's evaluation early or annotate a query. Fixed order; each owns its own memory field and display annotation.

| # | Mechanism | Fires | `n_min` | Candidate fate | Memory | Source |
|---|---|---|---|---|---|---|
| 1 | **Validation skip** — `OptSearchPoint.wounds.validation_failures` non-empty | pre-score | — | synthetic `{accuracy: 0.0, invalid: True}` (no backend calls) | `wounds.validation_failures` | `optimization/l1/score/candidate.py::score_one_candidate` |
| 2 | **Stale-data protocol** — cached *or* fresh result carries `diagnostics.warnings` | every degraded query | — | annotated + possibly re-measured / swapped | — | `scoring/sample_measurement.py::execute_stale_data_protocol` |
| 3 | **`DegradationCheck` — fatal fast-path** — latest query's `classify_result()` returns a fatal code | every query | **1** | eliminated; `RuntimeFailure` | `runtime_failures` | `optimization/pobb/checks.py` |
| 4 | **`DegradationCheck` — rate-based** — `degraded_rate >= threshold` | every query | **3** | eliminated; `RuntimeFailure` | `runtime_failures` | `optimization/pobb/checks.py` |
| 5 | **`PoBBCheck`** — three exits, in order: answer-collapse, leader lock-in, paired `P(best) < ε(n)` | every query | `n_min` | eliminated; records `elimination_cut` decision | — | `optimization/pobb/checks.py` |

**Ordering inside the query loop.** For each query: (1) prior-result cache lookup; (2) if degraded → `execute_stale_data_protocol`; (3) `on_result` fires → display renders the line; (4) iterate every enabled check in `degradation_checks`; first to return a signal ends the candidate. Mechanisms 3–5 co-exist in that final list — fatal beats rate beats Bayesian PoBB.

**One comparator, one stop rule — do not add a sixth.** A paired-margin futility gate ran here and was removed. Anything that counts discordant binary wins is a second comparator beside the θ ruler the election actually ranks on, so the two disagree by construction; it re-encodes the election's bar a second time; and it goes inert on a graded backend, where a per-sample fitness of 0.63 is neither a win nor a loss. Its kill payload also stamped a hardcoded `p_best: 0.0`, which `is_leader_eligible` reads as a PoBB loss — silently barring a cut candidate from the round election, so whole rounds closed with no winner while the real θ lift was positive. Buying futility back means one gate **on the θ ruler**.

## On-disk shape and replay

Each `ELIMINATION_CUT` / `LEADER_LOCK_IN` decision record (in `rounds/round_NNNN.json`) carries the paired snapshot under `data`: `p_best`, `leader_id`, `candidate_sample_ids` (the ordered list the candidate had measured at decision time) and `prior_histories[cid]` (each prior's grades restricted to exactly those samples, after backfill). `inputs_ref` records the gate parameters in force **and which `EliminationGate` fired** — only ε computed a posterior, so a replayer re-deriving a collapse cut under the ε rule tests a real `p_best` against a bar nobody set.

That makes the divergence replayer self-contained (`resume_and_fork/replayers.py::_pobb_replay_snapshot`): it rebuilds the candidate vector from the rescored results keyed by `candidate_sample_ids`, pairs each prior via `prior_histories`, and calls the same `elimination_p_best` on the cycle's fixed δ ruler. **No cross-round "find R1_winner in prior rounds" logic and no backfill during replay** — the decision record is the entire input, and the θ rule is closed-form and deterministic, so replay is bit-for-bit when no scorer change moved the candidate's grades. When the active scorer differs the candidate side is rescored and the prior side stays at the recorded grades; a scorer change that materially shifts priors surfaces as divergence via the candidate side.

Recorded booleans from pre-graded decisions coerce to 0.0/1.0 — the identical values the live path fed.

## Open questions

1. **Tie-breaking at budget cap.** When the round cap is reached and the top 2–3 candidates have similar `P(best)`, no test declares a clean winner. Ship pick-by-point-estimate; design a cap-extension policy after observing how often this fires.
2. **Small-*n* θ edge cases.** With few observations the Laplace `se` is wide, so `p_best` sits near 0.5 and the gate stays conservative until evidence accumulates — the EB hyperprior on the ability variance is what keeps the small-*n* fit from collapsing.

## References

- **Russo, D. (2016).** *Simple Bayesian Algorithms for Best Arm Identification.* COLT. — the PoBB / Top-Two Thompson Sampling family.
- **Maurer & Pontil (2009).** *Empirical Bernstein bounds and sample-variance penalization.* COLT.
- **Kalyanakrishnan et al. (2012).** *PAC subset selection in stochastic multi-armed bandits.* ICML. — LUCB; rejected as too pairwise.
- **Audibert, Bubeck, Munos (2010).** *Best arm identification in multi-armed bandits.* COLT. — Successive Rejects; rejected for not adapting within-round.

The `classify_result()` rule table and its three load-boundary effects: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md#classify_result--fatal-classification). Operator framing: [`../concepts/scoring-and-memory.md`](../concepts/scoring-and-memory.md#deprecated-samples).
