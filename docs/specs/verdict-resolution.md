# Verdict-Resolution Adaptive Queue Mechanism

**Status:** Documents the **live** mechanism. Phase 1 shipped (`c714bffd`) — ranking unified on `decision_information_gain`; the blended `explore_weight · model_information_gain` term removed. Phase 2 (origin-relative observation weighting) deferred — sketched below. Supersedes `bayesian-sample-picker.md`.

---

## What this is for

For the candidate we're currently evaluating, pick samples that let us decide as early as possible whether to keep it or abort it against the seed. Most candidates are dead; the adaptive queue mechanism's job is to discover that fact in the fewest measurements possible — ideally 3 to 6 — by always selecting the sample whose outcome would resolve the keep/abort question the most.

This is a sequential-testing problem with a statistical model behind it. The model is what gives us provable efficiency: if the model's prediction for a sample is genuinely uncertain, measuring is informative; if its prediction is confident, measuring is wasted. The adaptive queue mechanism maximizes that information per measurement.

---

## The model — one model, conditioning evolves

There is one statistical model. It produces one ranking. The ranking is updated every time we learn something new about the current candidate.

**The prediction layer.** For every sample, the model predicts the probability that the current candidate will hit it. The prediction draws on two sources of data:

- All historical observations on that sample, across the entire dataset-scoped archive — every measurement every candidate has ever made on it.
- The current candidate's own measurements so far in this run.

Every historical observation counts equally — the population's behaviour on a sample is the prior, regardless of which candidate produced each measurement. As we measure the current candidate, those measurements sharpen the prediction further. The mechanism is a per-candidate ability estimate that folds in observations as they arrive.

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

The ranking is written to `hard_samples_*.json` after each round boundary. That file is the webapp's read target. Reading it gives the latest serialized state of the same model — not a separate concept, not a frozen snapshot, just the current ranking. The webapp polls it; the live adaptive queue mechanism writes it.

A fresh mutation is ranked at `μ_c = μ_seed`, so its keep-or-abort prior is 50/50 by construction — correct, because its prior over ability genuinely is centred on the parent. The *expected* information still varies across samples through the prediction layer.

---

## Where it lives in code

- `decision_information_gain` (`adaptive_queue_mechanism.py:137-162`) computes the mutual information between a Bernoulli outcome and the keep/abort verdict, conditioned on the candidate's current ability posterior.
- The hierarchical-EB Rasch fit (`exploration.py::fit_rasch`) supplies the per-sample population profile.
- The per-candidate posterior fold (`loop.py:240-296`) updates the ability estimate after each measurement.
- The persisted ranking writer (`hard_sample_sorter.py::build_hard_samples_artifact_from_observations`) calls the same scoring path the live queue calls — one function, two trigger points.

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

Exactly what `decision_information_gain` computes (`adaptive_queue_mechanism.py:137-162`).

---

## Phase 2 sketch — origin-relative weighting

Not shipped. Outlined here so the substrate doesn't paint into a corner.

Today every archive observation contributes equally to a sample's population profile. Phase 2 will weight each observation by how relevant the producing candidate is to the current one — using similarity (lineage distance, prompt distance, or pipeline-config distance — undecided) and recency (older observations weighted lower, because pipeline capability drifts over time). The same scoring framework applies; only the conditioning is richer. The breaking primitive change Phase 2 needs is extending `Observation` (`exploration.py:38-44`) with a timestamp and a lineage hint, or routing those through a sidecar lookup. Open design question: archive-wide capability-drift estimate vs per-pipeline-node capability curve — this determines the `Observation` schema.

---

## References

- Adaptive queue mechanism math: `promptpotter/application/intelligence/adaptive_queue_mechanism.py::decision_information_gain`
- Per-candidate posterior fold: `promptpotter/application/optimization/l1/score/loop.py:240-296`
- Population profile fit: `promptpotter/application/intelligence/exploration.py::fit_rasch`
- Observation: `promptpotter/application/intelligence/exploration.py:38-44`
- Persisted ranking writer: `promptpotter/application/intelligence/hard_sample_sorter.py::build_hard_samples_artifact_from_observations`
- Superseded: `bayesian-sample-picker.md`
