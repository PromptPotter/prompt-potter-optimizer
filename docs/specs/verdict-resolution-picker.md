# Spec: Verdict-Resolution Sample Picker

**Status:** Draft. Supersedes [`bayesian-sample-picker.md`](bayesian-sample-picker.md).

---

## What this is for

For the candidate we're currently evaluating, pick samples that let us decide as early as possible whether to keep it or abort it against the seed. Most candidates are dead; the picker's job is to discover that fact in the fewest measurements possible — ideally 3 to 6 — by always selecting the sample whose outcome would resolve the keep/abort question the most.

This is a sequential-testing problem with a statistical model behind it. The model is what gives us provable efficiency: if the model's prediction for a sample is genuinely uncertain, measuring is informative; if its prediction is confident, measuring is wasted. The picker maximizes that information per measurement.

---

## The model — one model, conditioning evolves

There is one statistical model. It produces one ranking. The ranking is updated every time we learn something new about the current candidate.

**The prediction layer.** For every sample, the model predicts the probability that the current candidate will hit it. The prediction draws on two sources of data:

- All historical observations on that sample, across the entire dataset-scoped archive — every measurement every candidate has ever made on it.
- The current candidate's own measurements so far in this run.

In Phase 1, every historical observation counts equally — the population's behaviour on a sample is the prior, regardless of which candidate produced each measurement. As we measure the current candidate, those measurements sharpen the prediction further. The mechanism is the same one already running in the live picker: a per-candidate ability estimate that folds in observations as they arrive.

**The verdict layer.** Independently, the model tracks our current belief about whether the candidate will beat the seed. Every measurement updates that belief.

**The score per sample.** How much would measuring this sample change our verdict belief? That's the score. It rewards two things at once, both already in the prediction:

- **Predictive uncertainty.** A sample whose outcome the model expects with high confidence is wasted to measure — we'd see the predicted value, learn nothing. A sample whose outcome is a genuine coin flip is gold — either outcome would shift something.
- **Verdict relevance.** Even an uncertain outcome doesn't help if it can't move the keep/abort verdict. A sample whose outcome (either way) leaves the verdict unchanged is also wasted.

The product of these two — formally the mutual information between the sample's outcome and the verdict — is one number. That number is the score. Highest score wins; that's the next sample to measure.

**The bias toward unsolved samples emerges naturally** from this — it does not need a separate term. If the current candidate's running estimate puts it near the top of the ability range (it has been hitting things), then samples the population almost never solves still carry genuine uncertainty for *this* candidate — and either outcome moves the verdict. If the current candidate looks weak, those same samples have near-zero predicted hit probability — both branches of the expectation collapse to "miss" — and the score drops to zero on its own. No knob, no explicit bias, no headroom filter. The candidate's standing flows through the prediction; the prediction flows through the score.

---

## How the model evolves over time

The ranking is alive. It updates whenever the conditioning changes:

- At round boundary, the dataset-scoped Rasch fit is refreshed across all archive observations.
- When the current candidate's evaluation starts, the ranking is computed against the candidate's prior (a mutation of the seed inherits the seed's ability prior).
- Every time we measure the current candidate on a sample, its ability estimate updates, the prediction over every other sample shifts accordingly, and the ranking re-sorts.

The ranking is written to `hard_samples_*.json` after each round boundary. That file is the webapp's read target. Reading it gives the latest serialized state of the same model — not a separate concept, not a frozen snapshot, just the current ranking. The webapp polls it; the live picker writes it.

---

## What's already correct and stays

The math for "expected information about the verdict" is in the code. `decision_information_gain` (`adaptive_picker.py:137-162`) computes the mutual information between a Bernoulli outcome and the keep/abort verdict, conditioned on the candidate's current ability posterior. The hierarchical-EB Rasch fit (`exploration.py::fit_rasch`) supplies the per-sample population profile. The per-candidate posterior fold (`loop.py:240-296`) updates the ability estimate after each measurement. All of this is sound.

---

## What's broken

The current picker adds a second term to the score — an "exploration bonus" weighted at 0.05 — that rewards samples we have *less* data on. The intent was to keep some pull toward sharpening the population model; the effect is that at step 0 (where the verdict information is small for every sample because no candidate measurements exist yet) the exploration bonus dominates the ranking and promotes stranded, poorly-measured, always-miss samples to the top. This is the exact opposite of what we want: the table looks dumb because the tiebreaker is dumb.

There is also one degeneracy at the call site that has nothing to do with the formula: when ranking samples for a candidate at step 0, the current code evaluates the verdict at `μ_c = μ_seed` exactly, which makes the keep-or-abort prior 50/50 by construction. That's correct for a fresh mutation (its prior over ability genuinely is centred on the parent), so the prior probability is 50/50 — but the math for *expected* information still varies across samples through the prediction layer. Removing the exploration bonus is what restores that variation; the call site does not need changing beyond that.

---

## Phase 1 — what changes

One model, one score, one call site. No knobs.

- Remove the exploration bonus term entirely. Drop `model_information_gain`, `predictive_hit_prob`, and the `explore_weight` argument from `pick_value` / `expected_order` / `next_sample` in `adaptive_picker.py`. Drop `ExplorationConfig.explore_weight` from `application/config.py`.
- The single per-sample score is mutual information between the sample's outcome and the keep/abort verdict (`decision_information_gain`, unchanged math).
- The call site that writes the persisted ranking (`hard_sample_sorter.py::_pick_score_under_prior`) calls the same scoring path the live picker calls. One function, two trigger points.
- Everything else stays: `RaschPosterior`, the hierarchical-EB fit, the candidate posterior fold, the heatmap axis sorts, PoBB.

---

## Phase 2 sketch — origin-relative weighting

Not in Phase 1. Outlined here so the substrate doesn't paint into a corner.

In Phase 1, every archive observation contributes equally to the population profile of a sample. Phase 2 will weight each observation by how relevant the producing candidate is to the current one — using similarity (lineage distance, prompt distance, or pipeline-config distance — undecided) and recency (older observations weighted lower, because pipeline capability drifts over time). The same scoring framework applies; only the conditioning is richer. The breaking primitive change Phase 2 needs is extending `Observation` (`exploration.py:38-44`) with a timestamp and a lineage hint, or routing those through a sidecar lookup.

---

## Verification

Three checks; each one fails against the current behaviour.

1. **Ranking quality.** Open the operator's hard-samples table after the change. Samples where the population almost always hits or almost always misses sink toward the bottom. Genuinely contested samples (mixed hit/miss in the archive) sit at the top, ordered by how much they could shift the current candidate's verdict. Manual eyeball on any populated campaign.
2. **Convergence speed.** Replay a recorded round where a candidate should die in 3–6 measurements against the seed. New picker should match or beat the current picker's measurement count on the same `(cycle_id, candidate_id, sample seed)`. New fixture: `tests/test_picker_convergence.py`.
3. **PoBB integration unchanged.** `tests/test_pobb_check_*.py` must pass without modification — the verdict gate is unchanged; only the sample order changes.

---

## What gets deleted (Phase 1)

- `adaptive_picker.py::model_information_gain`
- `adaptive_picker.py::predictive_hit_prob`
- `explore_weight` argument from `pick_value`, `expected_order`, `next_sample`
- `ExplorationConfig.explore_weight` field
- All call-site references to `explore_weight` in `loop.py` and `hard_sample_sorter.py`

`explore_weight` is a `policy` field; removing it on `resume` forks a sibling cycle. Expected, no migration.

---

## Pre-flight gate

1. **§0 bucket:** central loop (sample selection). No new bucket.
2. **Existing channel:** yes — `RaschPosterior`, the `hard_samples_*.json` artifact, `loop.py`'s scoring path. No sidecar.
3. **Distinct name:** no new identifiers; uses the existing `decision_information_gain` and `pick_score` artifact key.
4. **Self-describing:** "mutual information between sample outcome and keep/abort verdict" reads in one line.
5. **Rides existing infra:** yes — no new persisted state.
6. **AI-readable:** the artifact's `pick_score.per_sample` and `pick_score.sample_order` shapes are unchanged; semantics are what the table behaviour already says they should be.
7. **§0 update:** none required.
8. **Langfuse:** picker is pure math, untraced.

---

## Math (reference)

Single per-sample score for the current candidate `c` with running ability posterior `(μ_c, var_c)`, against the seed posterior `(μ_s, var_s)`, on sample `s` with population profile `(δ_s, se_δ_s)`:

```
p0      = Φ((μ_c − μ_s) / √(var_c + var_s))                    # current verdict belief
μ⁺, v⁺  = update(μ_c, var_c, δ_s, se_δ_s, hit=True)            # candidate posterior after hit
μ⁻, v⁻  = update(μ_c, var_c, δ_s, se_δ_s, hit=False)           # candidate posterior after miss
p⁺      = Φ((μ⁺ − μ_s) / √(v⁺ + var_s))
p⁻      = Φ((μ⁻ − μ_s) / √(v⁻ + var_s))
p̄       = E[Bernoulli outcome] marginalized over candidate posterior

score(s) = H(p0) − [ p̄ · H(p⁺) + (1 − p̄) · H(p⁻) ]            # mutual info, in nats
```

Exactly what `decision_information_gain` already computes (`adaptive_picker.py:137-162`). The math is correct; the spec only removes the exploration term that was added on top.

---

## Open triangulation questions

To be answered with the operator before implementation:

1. **Phase 2 timing.** Spec-only, or wire similarity-weighting within the next 1–2 weeks? If the latter, Phase 1 should pre-extend `Observation` with a timestamp now — one breaking primitive change is cheaper than two.
2. **Aging mechanism scope (Phase 2 design only).** Archive-wide capability-drift estimate, or per-pipeline-node capability curve? Determines `Observation` schema in Phase 2.

---

## References

- Picker math: `promptpotter/application/intelligence/adaptive_picker.py::decision_information_gain`
- Per-candidate posterior fold: `promptpotter/application/optimization/l1/score/loop.py:240-296`
- Population profile fit: `promptpotter/application/intelligence/exploration.py::fit_rasch`
- Observation: `promptpotter/application/intelligence/exploration.py:38-44`
- Persisted ranking writer: `promptpotter/application/intelligence/hard_sample_sorter.py::build_hard_samples_artifact_from_observations`
- Companion: [`hard-sample-sorter.md`](hard-sample-sorter.md)
- Superseded: [`bayesian-sample-picker.md`](bayesian-sample-picker.md)
