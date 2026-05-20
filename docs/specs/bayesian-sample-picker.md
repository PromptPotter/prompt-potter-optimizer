# Spec: Bayesian Sample Picker — Hierarchical IRT + Expected Information Gain

**Status:** Shipped (Phases 1–3). Superseded the plug-in MFI / Track-and-Stop
objective in `adaptive_picker.py`.

---

## Context

The adaptive picker decides which sample a candidate measures next, and the
hard-sample sorter ranks samples by how informative they are. Both answer
"how valuable is sample `s`?" — and both answer it **wrong for
barely-measured samples**.

Concrete failure: a sample observed once, as a HIT, sinks to the bottom of
`hard_sample_order` and gets a near-zero `pick_score`. The Rasch fit reads
one HIT as "easy item", pins `δ_s` low, and the MFI objective scores it
`p·(1−p) ≈ 0`. A sample we know *almost nothing* about is treated as
*settled*.

Three root causes, each an imposed assumption rather than a fact:

1. **The priors are hand-set.** `fit_rasch` hardcodes `theta_prior_sigma=1.5`,
   `delta_prior_sigma=2.0` (`exploration.py:70-71`). The shrinkage a 1-obs
   sample receives is `2.0` because someone typed `2.0` — not because the
   sample population says so. `cold_start_prior_sigma` is the same knob
   surfaced in `ExplorationConfig`.

2. **The objective is a plug-in statistic on point estimates.** `fit_rasch`
   already computes the full Laplace posterior — `delta_se`, `theta_se` —
   then `fisher_info`, `expected_order_mfi`, `chernoff_info_pair` and friends
   discard the SEs and evaluate a deterministic function at `δ̂_s`, `μ̂_c`.
   MFI and Chernoff-info are only exact when `δ_s` and `θ_c` are known. They
   never are. A sample seen once and a sample seen 50× score identically if
   their point estimates match.

3. **The fit ignores the archive.** `_delta_map_from_obs` (`score.py:598`)
   refits Rasch on the current round's observations only. Every
   `(searchpoint, sample, hit)` triple from prior cycles — sitting in the
   `MeasurementArchive` — is thrown away. A new candidate starts from
   `PRIOR_MU=0` even when the dataset has a thousand archived measurements.

This spec replaces all three with one coherent statistical model. Each phase
**removes** an imposed number; none adds one.

---

## The model

### Change 1 — Hierarchical IRT (empirical Bayes)

Keep the 1PL IRT likelihood `P(hit | c, s) = σ(θ_c − δ_s)`. Make the priors
hierarchical:

```
θ_c ~ N(0,    σ_θ²)        # mean anchored at 0 for identifiability
δ_s ~ N(μ_δ,  σ_δ²)        # μ_δ = mean dataset difficulty
```

Estimate `η = (σ_θ, σ_δ, μ_δ)` by maximizing the **marginal likelihood**
`p(data | η)` — Type-II MLE / empirical Bayes. The marginal is approximated
with the Laplace expansion already available from the Newton fit:

```
log p(data | η) ≈ log p(data, θ̂, δ̂ | η) − ½ log det( H(θ̂, δ̂) / 2π )
```

The outer optimization is 3-dimensional; each inner evaluation is one
`fit_rasch` pass. ~10–30 inner fits → seconds.

Result: shrinkage is data-determined. A 1-obs sample's posterior mean is
`δ̂_s = μ_δ + (δ_obs − μ_δ)·σ_δ²/(σ_δ² + 1/info_obs)` — pulled toward `μ_δ`
by exactly the fraction the *rest of the sample population* warrants. No `2.0`.

`RaschPosterior` gains `sigma_theta`, `sigma_delta`, `mu_delta` so the
estimated hyperparameters land on disk (`hard_samples_*.json::rasch`) and are
operator-readable without re-running.

### Change 2 — Expected Information Gain objective

Replace MFI / Chernoff with the **expected information gain** of a
measurement — the entropy reduction of the posterior, integrating over both
the unknown outcome and the joint posterior of `(θ_c, δ_s)`.

Predictive hit probability, marginalized over the posterior (probit
approximation, exact form for `E[σ(N(m,v))]`):

```
m   = θ̂_c − δ̂_s
v   = se_θ_c² + se_δ_s²                    # diagonal-Laplace approx
p̄  = σ( m / √(1 + π·v/8) )
w̄  = p̄·(1 − p̄)
```

One Bernoulli observation on `(c, s)` informs only the **difference**
`θ_c − δ_s` — its observed-info contribution is the rank-1 matrix
`w̄·[[1,−1],[−1,1]]`. The priors break the degeneracy. The total entropy
reduction of the joint `(θ_c, δ_s)` posterior is closed-form:

```
EIG(c, s) = ½ · log( 1 + w̄ · (se_θ_c² + se_δ_s²) )
```

This is the whole objective. It uses **only quantities `fit_rasch` already
returns**. Read it directly:

- `se_δ_s` large (few observations) ⇒ EIG large. Barely-measured samples
  rank **high** — measuring them sharpens `δ_s`, enriching the model and the
  archive. The 0/4/7 failure is gone, with no `κ` term.
- `se_δ_s → 0` (sample fully understood) ⇒ `EIG → ½log(1 + w̄·se_θ²)`,
  monotone in `w̄ = p̄(1−p̄)` — **MFI is recovered exactly** as the
  δ-known limit.
- The picker's per-step choice is `argmax_s EIG(c, s)`; the dashboard order
  is `EIG` descending; `pick_score` becomes `EIG`. One number, three
  consumers.

`knowledge_gradient` (`exploration.py:169`) — the one-step KG the
scoring-set evolution uses — is a first-moment proxy of this same quantity.
It is deleted; scoring-set swap-in ranks on `EIG` too. One notion of sample
value across the whole codebase.

**Decision-aligned variant.** When the loop's question is purely
keep/abort (the current `track_and_stop` intent), `Q` is the verdict, not
the model: `EIG_decision(c, s) = I(Y_s ; verdict)` against the seed's
predictive. Still knob-free, still computed over the posterior — Chernoff-
info is its means-known plug-in limit. Retained as a mode, not the default.

### Change 3 — Archive-wide fit

The hierarchical posterior is fit over the **entire dataset-scoped
`MeasurementArchive`** — every `(searchpoint, sample, hit)` triple from
every cycle — not the current round. `δ_s` then carries all cross-cycle
evidence; a new candidate's `θ_c` prior is a genuine draw from the learned
population `N(0, σ_θ²)`, not `PRIOR_MU=0`. The archive is dataset-scoped
already (v2 schema), so the fit is per dataset — abilities and difficulties
share one scale.

Fit once per round at the round boundary (sparse vectorized Newton +
empirical-Bayes outer loop, sub-second to seconds); the picker reads the
cached posterior. Not per candidate.

---

## Phased path

### Phase 1 — Hierarchical `fit_rasch` ✓ shipped

Empirical-Bayes the priors inside `fit_rasch`. Drop `theta_prior_sigma` /
`delta_prior_sigma` params and `ExplorationConfig.cold_start_prior_sigma` —
estimated, not configured. Surface `sigma_theta`, `sigma_delta`, `mu_delta`
on `RaschPosterior` and the artifact. Every consumer (sorter, scoring-set
evolution, picker) improves with no objective change. **Verify:** a 1-obs
sample shrinks toward `μ_δ`; estimated `σ_δ` matches the spread of
well-measured samples.

### Phase 2 — EIG objective ✓ shipped

Replace `adaptive_picker.py`'s MFI/Chernoff surface with
`predictive_hit_prob`, `expected_information_gain`, `next_sample_eig`,
`expected_order_eig`. Rewire `score.py` and the hard-sample sorter's
`pick_score` to EIG. Delete `knowledge_gradient`; route scoring-set swap-in
through EIG. `picker_objective` becomes `Literal["model", "decision"]`,
default `"model"`. **Verify:** 0/4/7-style 1-obs samples rank above
fully-pinned samples; `EIG` collapses to MFI ranking when all `se_δ → 0`.

### Phase 3 — Archive-wide fit ✓ shipped

Point the round-boundary fit at the `MeasurementArchive`; candidate `θ_c`
priors from the population posterior. Drop `PRIOR_MU` / `PRIOR_VAR`
constants. **Verify:** δ_s on a cold dataset sample reflects archived
cross-cycle measurements; picker order on round 0 is non-trivial.

---

## What gets deleted (no backward compat)

- `adaptive_picker.py`: `fisher_info`, `next_sample_mfi`, `expected_order_mfi`,
  `chernoff_bernoulli`, `chernoff_info_pair`, `next_sample_track_and_stop`,
  `expected_order_track_and_stop`, `predicted_hit_probability`.
- `exploration.py`: `knowledge_gradient`; `fit_rasch`'s `*_prior_sigma` params.
- `config.py`: `ExplorationConfig.cold_start_prior_sigma`; `picker_objective`
  Literal values `"mfi"` / `"track_and_stop"` → `"model"` / `"decision"`.
- `score.py`: `PRIOR_MU`, `PRIOR_VAR` module constants.
- Tests pinned to the old objective (`test_intelligence.py`,
  `test_optimizer.py`, `test_pobb_check_*`) — rewritten against EIG, not
  shimmed.

`picker_objective` and `exploration` are `policy` knobs (`config.py:82-83`):
changing them on `resume` forks a sibling cycle — expected, no migration.

---

## Resolves the webapp sort-label gap

Once `pick_score` *is* the EIG and `hard_sample_order` is EIG-descending, the
table's "Pick" column literally is the sort key. `HardSamplesTable.tsx` can
mark the `pick_score` header as the live-sort column instead of suppressing
every sort indicator — closing the "table doesn't show the metric it's
sorted by" gap in the same arc.

---

## Pre-flight gate

1. **§0 bucket:** central loop (sample selection) + archive (the fit). No new
   bucket. **Verify** `docs/architecture.md` §0 doesn't name `mfi` /
   `track_and_stop` literally; if it does, update §0 in a prior PR.
2. **Existing channel:** yes — rides `RaschPosterior`, the round-boundary
   artifact, `dashboard.json::hard_sample_order`. No sidecar.
3. **Distinct name:** `expected_information_gain` / `EIG` — grep-clean.
4. **Self-describing:** EIG is standard Bayesian-experimental-design term.
5. **Rides existing infra:** yes — no new persisted state.
6. **AI-readable:** `sigma_theta` / `sigma_delta` / `mu_delta` and per-sample
   `EIG` land in `hard_samples_*.json`.
7. **§0 update:** see (1).
8. **Langfuse:** no new LLM call — picker is pure math, untraced today.

---

## Open questions

- **Off-diagonal covariance.** Phase 2 uses `v = se_θ² + se_δ²`, dropping
  `Cov(θ_c, δ_s)`. The exact 2×2 block of the inverse Hessian is more
  faithful but costs an inverse. Diagonal is the proven-cheap default;
  exact covariance is a refinement only if validation shows it matters.
- **Archive fit cost ceiling.** A large dataset's archive (thousands of
  candidates × samples) — confirm the sparse Newton + empirical-Bayes loop
  stays sub-second; cap or incrementally update if not.
- **Empirical-Bayes degeneracy on tiny data.** With <~3 observations the
  marginal likelihood is flat in `σ_δ`. Fall back to a weakly-informative
  hyperprior on `η` (full hierarchical Bayes for the variance components),
  not a hardcoded floor.

---

## References

- Picker: `promptpotter/application/intelligence/adaptive_picker.py`
- Rasch fit: `promptpotter/application/intelligence/exploration.py::fit_rasch`
- Sorter: `promptpotter/application/intelligence/hard_sample_sorter.py`
- Picker call site: `promptpotter/application/optimization/l1/score.py:595-711`
- Companion: [`hard-sample-sorter.md`](hard-sample-sorter.md),
  [`rasch-validation-plan.md`](rasch-validation-plan.md)
- Method: [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md)
