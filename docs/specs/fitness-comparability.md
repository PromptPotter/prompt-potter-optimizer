# Fitness comparability — collapse the θ/accuracy boundary

> **Status:** design. **Prerequisite to [`l4-outer-loop.md`](l4-outer-loop.md)** — the L4 outer fitness reads inner-campaign improvement measured over samples; if per-candidate fitness is sample-drift-distorted, the outer loop inherits the distortion. Build this first.

## Why — one quality number, accidentally split into two

A candidate's quality is computed **twice today, by two modules that never reconcile**:

- **Ability (θ)** — `application/intelligence/exploration.py::fit_rasch(observations) -> RaschPosterior` fits a hierarchical **1PL Rasch** model (EB / Laplace-EM) and returns per-candidate `theta[cid]` + `theta_se` and per-sample difficulty `delta[sid]` + `delta_se`. θ is **difficulty-aware**: it discounts for *which* samples a candidate saw. It is refit every round and every online measurement. But θ is consumed **only** by sample selection (`select_round_subset`, the online adaptive queue) and the hard-samples heatmap ordering (`hard_sample_sorter.py`) + the artifact writer. **θ never reaches fitness.**
- **Fitness (accuracy / composite)** — `application/scoring/metrics.py::compute_composite_fitness` averages raw per-sample `fitness`/`hit` over **the candidate's own measured subset**. It is **difficulty-blind**. This is the number that actually gates: election, PoBB elimination, and the `improved` flag all compare candidates on it.

The two are a **dormant duplication**. With a *fixed* subset (per-round resubset OFF — the current default), every candidate sees the same samples, so raw accuracy is monotone with θ — the two paths agree and the split is invisible. The moment per-round resubset turns **ON** (candidates measured on different, signal-chased subsets), accuracy stops tracking difficulty and the paths **disagree**: the heatmap orders candidates by ability while election/PoBB order them by raw subset accuracy. That disagreement *is* the per-candidate-fitness distortion — and it is why resubset had to be defaulted off.

The existing fitness path is not naïve — `elect_round_winner` already does *paired* matching (candidate vs origin on their common samples, `paired_delta_lcb`), a partial drift-guard. But that is candidate-vs-origin only; across candidates on different subsets the headline number still drifts. θ is the complete, cross-candidate-invariant version of what the paired matching is reaching for.

## Decision — gating fitness IS θ

**Collapse the boundary: the gating fitness becomes the latent ability θ that already exists.** This is wiring, not building — the 1PL estimator runs every round; it is trapped on the selection/display side of a wall that should not exist.

This is the **standard** fix, not a bespoke one: difficulty-adjusted ability is exactly what Item Response Theory / Computerized Adaptive Testing use to compare test-takers who answered *different* question sets — the Rasch "specific objectivity" property. It is a small, simple statistical model applied **wide**: one fit, consumed by both sample selection and the gate, that removes the per-round sample-set drift at the root rather than patching each downstream symptom. The cost is one joint fit per round (already paid for selection).

- **θ gates; accuracy/composite stay as recorded display.** Election, PoBB elimination, and the improvement gate compare candidates on θ (with `theta_se`). Raw `accuracy` and `composite_fitness` remain recorded and shown to the operator — but as *subset-relative* display numbers, explicitly flagged `mode: measured`, never the cross-candidate gate. (Today the inverse holds: accuracy gates, composite is "recorded, not gating".)
- **The improvement gate moves to θ-units (logits).** `delta_ok = θ_best − θ_matched_origin > improvement_threshold_logits`; `c0_ok` is θ relative to the origin's θ. The origin must be in the joint Rasch fit (it is scored, so it is in the observation pool). The current accuracy-space `improvement_threshold` (0.02) is recalibrated once into a θ-logit threshold.
- **Comparability lock — stabilize the δ bank.** θ is comparable *within* a joint fit (shared δ, mean-θ=0 anchor), but δ currently re-fits over an accumulating observation union each round, so the difficulty scale drifts between rounds. Pin it: the per-dataset δ bank is the **accumulating grade-A measurement archive** (the provenance grade, `domain/measurement_provenance.py`) — calibrated from clean measurements, refreshed at round boundaries with **fixed-parameter (anchor) updating** rather than a from-scratch refit, so δ moves smoothly and θ stays on one scale across rounds. The bank *is* the clean corpus L4 later ingests — same accreting asset.

## Selection + efficiency (already mostly built)

Per-round resubset can turn back **ON** once fitness is θ, because θ makes the drifting subsets comparable. The adaptive queue already chooses informatively: `adaptive_queue_mechanism.py::pick_value = decision_information_gain (verdict MI: will this sample move θ_c past θ_s?) + delta_learning_gain (δ Fisher-information entropy drop)`. That is the "always be in the maximum-signal range" behavior, already wired into both `select_round_subset` (between-round) and the online `_next_sample` (within-round). Textbook **maximum-Fisher-information-on-θ** selection is a small refinement of the headline term if wanted; the information-theoretic acquisition is present today.

**Stopping = sequential CI on θ.** Point `pobb/elevation.py::elevate_to_decisive` at θ: keep measuring the fewest, most-informative samples until the candidate **ranking** is decisive (the CI on θ separates the arms). Today `elevation.py` compares arms on the *mean of per-sample fitness* and tops up by **random** sample — swap to θ-mean comparison + information-driven top-up. This is "confident rankings with fewer items," the efficiency win, made principled.

## 1PL → 2PL graduation (per-dataset, gated)

1PL fixes the drift now with the data we have; **2PL adds power once enough data is collected.** "Some samples carry more signal" is **item discrimination** — a per-sample **2PL** parameter (aᵢ) that 1PL/Rasch cannot represent: it is the sample's **signal-to-noise**, how sharply it separates able from unable candidates. 1PL says only *how hard* a sample is; 2PL also says *how much it tells you*, so both selection and the gate can weight high-signal samples harder and discount noisy ones instead of treating every sample as equally informative. Build it as an enhancement, adopt it only where it provably wins:

- **Both estimators behind one interface; callers read θ** from whichever model the bank currently uses — the switch is invisible above the seam.
- **Switch is per-dataset bank**, gated on **both**: (1) enough responses per sample to estimate discrimination (judge by the discrimination SE, not a magic count); (2) 2PL fits better *out-of-sample* — **cross-validated held-out log-likelihood** on (sample, hit) pairs as the primary test (won't reward overfitting), with **likelihood-ratio test** (1PL is nested in 2PL) or BIC as the cheap pre-check.
- **Hysteresis** — 2PL must win by a margin and the decision is only re-evaluated at each calibration refresh, so the bank does not flip-flop round to round. The cross-val gate also means 2PL can never *regress* a dataset.

## Implementation order

1. **Route 1PL θ into the gating seams + turn resubset safely ON (the boundary collapse).** Make `elect_round_winner` / the `delta_ok`/`c0_ok`/`improved` gate / `PoBBCheck` compare on θ (+`theta_se`) instead of per-sample `fitness`; keep accuracy/composite as recorded display; recalibrate `improvement_threshold` into logits; flip `per_round_resubset` to a safe default. **Done when:** with resubset ON, two candidates measured on different subsets rank by ability not by who got the easier samples — the drift distortion is gone, and round-over-round θ is monotone where accuracy was not. Standalone value; unblocks the efficiency win.
2. **Stabilize the δ bank + θ-based stopping.** Anchor-update the per-dataset δ bank from the grade-A archive (smooth cross-round scale); point `elevate_to_decisive` at θ with information-driven top-up. **Done when:** δ scale is stable across rounds and cross-cycle comparison is θ-based + decisive with fewer samples.
3. **2PL discrimination + auto-switch gate.** Add the 2PL estimator behind the θ interface; per-dataset cross-val + hysteresis switch. **Done when:** a data-rich dataset graduates to 2PL only after it wins held-out fit, and signal-chasing uses real discrimination.

## Named seams (verified against the tree; not edited by this spec)

| Concern | File |
|---|---|
| 1PL estimator (already built) | `application/intelligence/exploration.py::fit_rasch` → `RaschPosterior` (`theta`/`theta_se`/`delta`/`delta_se`), `build_observations`, `posterior_from_outcomes` |
| Adaptive selection (already built) | `application/intelligence/adaptive_queue_mechanism.py::pick_value`; `exploration.py::select_round_subset`; online `l1/score/loop.py::_next_sample` |
| Resubset / reorder toggles | `…/config.py::SelectionMechanisms.per_round_resubset` (False), `online_reorder` (True) |
| Round-winner election → θ | `application/scoring/metrics.py::elect_round_winner`, `paired_delta_lcb`, `paired_fitness`, `matched_origin_stats` |
| Improvement gate → θ-logits | `application/optimization/l1/score/winner.py::l1_score` (`delta_ok`/`c0_ok`/`improved`, `improvement_threshold`) |
| PoBB elimination → θ | `application/optimization/pobb/elimination/checks.py::PoBBCheck` (`register_completed`/`check`/`_dominance_check`, read `r["fitness"]`) |
| Cross-cycle decisive compare → θ | `application/optimization/pobb/elevation.py::elevate_to_decisive` (`_load_arm_history`, `posterior_best_probabilities`, random top-up → information top-up) |
| Recorded display fitness | `application/scoring/metrics.py::compute_composite_fitness` (stays accuracy/composite, flagged subset-relative) |
| δ bank source | `domain/measurement_provenance.py` grade-A archive |

## Non-goals + validation

- **Non-goals:** removing accuracy/composite as *display* (they stay, flagged subset-relative); a webapp surface; changing the inner scoring backend.
- **Validation (silent-harm — a wrong fitness is invisible):** a regression test in the numerics suite (`tests/test_numerics.py`) that constructs two candidates on *different* subsets where raw accuracy and θ disagree, and asserts election/PoBB now follow θ (the candidate with higher ability on harder samples wins) — the exact drift the boundary caused. Plus an efficiency check: resubset-ON reaches a decisive θ-ranking in fewer measurements than resubset-OFF reaches a stable accuracy ranking.
