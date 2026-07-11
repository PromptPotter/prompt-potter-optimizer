# Verdict-Resolution Adaptive Queue Mechanism

Documents the **live** mechanism. There are **two**, and the split is the whole
point — they answer different questions and only one of them is adaptive:

| | Question | Function | When |
|---|---|---|---|
| **Acquisition score** | Which sample is worth measuring at all? | `pick_value = decision_information_gain + delta_learning_gain` | **Between** rounds (`select_round_subset`) + the persisted hard-samples ranking |
| **Round order** | In what order do we score the round's samples? | `build_round_order` | **Within** a round — one deterministic order, shared by every candidate |

Both live in `application/intelligence/adaptive_queue_mechanism.py`.
**There is no online per-sample re-fit** — the round order is static by design
(see § The round order for why adaptivity there made it worse).

---

## What this is for

Most candidates are dead. The job is to discover that in the fewest measurements
possible, by spending each measurement where it most changes what we believe.

That splits cleanly into the two mechanisms above, and conflating them is the
error this spec exists to prevent: *which samples are informative* is a
population-level question answered from the archive between rounds; *what order
to score them in* is a within-round question answered from the seed's own
outcomes. The second one does **not** need to be adaptive, and making it adaptive
made it worse.

---

## The acquisition score — `pick_value`, and it has two terms

For the current candidate `c` with 1PL Rasch ability posterior `(μ_c, var_c)`,
against the seed `(μ_s, var_s)`, on sample `s` with population profile
`(δ_s, se_δ_s)`:

```
pick_value(s) = decision_information_gain(s) + delta_learning_gain(s)
```

**Term 1 — `decision_information_gain`: will this sample move the verdict?**
The mutual information (nats) between the sample's next outcome and the verdict
`θ_c > θ_s`. It rewards a sample only when the outcome is genuinely uncertain
*and* either branch would shift the keep/abort belief. In the means-known limit
it recovers Bernoulli Chernoff information (Garivier–Kaufmann 2016,
Track-and-Stop).

```
p0      = Φ((μ_c − μ_s) / √(var_c + var_s))                    # current verdict belief
μ⁺, v⁺  = update(μ_c, var_c, δ_s, se_δ_s, hit=True)            # posterior after a hit
μ⁻, v⁻  = update(μ_c, var_c, δ_s, se_δ_s, hit=False)           # posterior after a miss
p⁺      = Φ((μ⁺ − μ_s) / √(v⁺ + var_s))
p⁻      = Φ((μ⁻ − μ_s) / √(v⁻ + var_s))
p̄       = marginal_hit_probability(...)

decision_information_gain(s) = H(p0) − [ p̄ · H(p⁺) + (1 − p̄) · H(p⁻) ]
```

**Term 2 — `delta_learning_gain`: will this sample teach us the ruler?**
The expected entropy drop (nats) in the sample's own difficulty `δ_s`. One
Bernoulli outcome adds Fisher information `p(1−p)/scale²` to the prior
δ-precision `1/se_δ²`, giving `½·ln(1 + se_δ²·p(1−p)/scale²)` with
`scale² = 1 + π·se_δ²/8`.

**This second term is not optional, and it is exactly the "bias toward unsolved
samples":** without `delta_learning_gain` the seed-centred first pick degenerates
to "prefer lowest `se_δ`" and **starves the unmeasured headroom**. It is largest for
*under-measured* samples (large `se_δ`) whose outcome is a genuine coin flip
(`p≈0.5`), and it decays to zero both for well-measured samples and for resolved
always-hit / always-miss samples (`p(1−p)→0`) — so it explores the unknown
without re-promoting what the run has already pinned.

**Responses are graded, not Bernoulli.** `Observation.response` is the sample's
continuous per-sample fitness ∈ [0,1] — the same score `accuracy` and
`paired_fitness` read — **not** a binarized hit. The logistic MAP maximizes
cross-entropy `Σ y·log p + (1−y)·log(1−p)`, valid for any `y ∈ [0,1]`, so a
binary dataset is bit-identical to the old hit path while a continuous-fitness
backend (reciprocal-rank matching, the L4 outer proxy) keeps its gradient instead
of collapsing to an all-miss θ.

---

## The round order — `build_round_order`, one static order per round

Within a round there is **one** scoring order, shared by every candidate, and
nothing re-sorts mid-round (`l1/score/loop.py`: *"The round's frozen plan — a
single step; there is no per-sample re-rank."*).

It is built by partitioning on the **seed's** per-sample grades:

- **MISS-stratum** — seed grade < 1.0, *or not yet measured by the seed* (an
  unknown is a potential win, and fronting it warms the per-sample backfill
  earliest). Ordered by **ascending δ**: easiest win opportunities first — a live
  candidate proves itself immediately, and a dead one's misses on the easiest
  wins are the strongest futility evidence.
- **HIT-stratum** — seed grade ≥ 1.0. Ordered by **descending δ**: likeliest
  regression points first.

Every 4th position takes the next HIT-stratum sample (the regression probe); all
other positions take the next MISS-stratum sample; when a stratum runs dry the
other's remainder follows.

**Why k=4.** Pure miss-first defers all regression evidence past the miss block.
A proportional interleave spreads the misses so thin that the paired-margin
gate's deterministic-exhaustion kill lands at the very end. k=4 costs a pure-tie
kill a handful of extra samples and buys a regression probe inside the first
`elimination_n_min` window, plus steady loss accrual for regressors.

It is a **pure function** of (seed grades, ruler, sample ids), so a resumed round
re-derives the identical order with no recorded sidecar.

**Why static beats adaptive here:** an ability re-fit after every measurement
empirically front-loads the seed's hit set — the zero-information region, where
every early paired comparison ties, `p_best` pins at 0.5, and the elimination
gates go blind until the tail. The round's actual decision is "can this candidate
NET the adoption margin against the seed", and that evidence lives only in
discordance-potential samples — hence the shared, seed-stratified order.

---

## Where it lives in code

- Acquisition score — `intelligence/adaptive_queue_mechanism.py::pick_value`
  (with `::decision_information_gain` + `::delta_learning_gain`).
- Round order — `intelligence/adaptive_queue_mechanism.py::build_round_order`,
  called once per round at `optimization/l1/score/loop.py::score_population`.
- Between-round subset pick — `intelligence/exploration.py::select_round_subset`
  (still fits **1PL** via `::fit_rasch`; see `fitness-comparability.md`).
- Population profile fit — `intelligence/exploration.py::fit_rasch`.
- Persisted ranking writer —
  `intelligence/hard_sample_sorter.py::build_hard_samples_artifact_from_observations`,
  which calls the same `pick_value` the between-round pick calls: one function,
  two trigger points.

The ranking is written to `hard_samples.json` (cycle + campaign scope) at each
round boundary; the webapp polls it. The suffixed
`archive/measurements/hard_samples_{backend}_{dataset}.json` is the separate
archive-scope file.

---

## Phase 2 sketch — origin-relative weighting (not shipped)

Weight each archive observation by the producing candidate's relevance to the
current one (similarity + recency) instead of equally; same scoring framework,
richer conditioning. Needs `Observation` extended with a timestamp + lineage
hint — the open design question (archive-wide drift estimate vs per-node
capability curve) determines that schema.
