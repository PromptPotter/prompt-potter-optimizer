# Verdict resolution — the statistical model and the two sample-selection mechanisms

The model every score in this repo is expressed in, and the two mechanisms that decide **which**
samples get measured and **in what order**. The split between those two is the whole point — they
answer different questions and only one of them is adaptive:

| | Question | Function | When |
|---|---|---|---|
| **Acquisition score** | Which sample is worth measuring at all? | `pick_value = decision_information_gain + delta_learning_gain` | **Between** rounds (`select_round_subset`) + the persisted hard-samples ranking |
| **Round order** | In what order do we score the round's samples? | `build_round_order` | **Within** a round — one deterministic order, shared by every candidate |

Both live in `application/intelligence/adaptive_queue_mechanism.py`. **There is no online per-sample
re-fit** — the round order is static by design (§ The round order for why adaptivity made it worse).

Which *candidate* wins is the sibling question, owned by
[`candidate-elimination.md`](candidate-elimination.md). Both read θ, which is why they never
disagree about what "better" means.

**Holm corrects across PAIRS and cannot correct across METRICS.** Any surface reporting a
pairwise comparison must say so: trying several metrics and keeping the tightest is a
comparison no column prices, and the correction does not reach it. `p` is null where nothing
was tested (below two shared cells) — a different fact from a test that returned 1.00.

**What this model does not cover.** Within LLM-driven program evolution, PromptPotter targets the
bounded case: fixed pipeline, labelled dataset, scalar fitness. Open-ended synthesis,
multi-objective fitness and unlabelled tasks remain open problems for the paradigm and are not
supported here — θ is a single latent ability per candidate, so a second objective has nowhere to
land.

---

## The model — Rasch θ/δ

```
P(hit_{c,s} = 1) = σ(θ_c − δ_s)
```

Candidate ability × sample difficulty, fit jointly by MAP (alternating Newton on the sparse
observation matrix, Laplace SEs, anchored to `mean(θ) == 0` for identifiability;
`intelligence/exploration.py::fit_rasch`). That joint fit runs ONCE, at ruler lock — every reading
after it holds δ fixed and solves θ alone (`::fit_theta_given_delta`), or the scale moves under the
round it judges. Two consumers: `δ_s` selects samples and ranks hardness; `θ_c` is the
**round-winner gate**.

**θ is why per-round resubsetting is safe at all.** Score each candidate on a different,
signal-chased subset and raw accuracy drifts — whoever drew the easier samples wins on paper — but
θ is *subset-invariant*, because clearing a hard sample is worth more than clearing an easy one
(Rasch specific objectivity). One small standard model applied wide, fixing sample-set drift at the
root instead of patching the symptom downstream.

**Responses are graded, not Bernoulli.** `Observation.response` is the continuous per-sample fitness
∈ [0,1] — the same score `accuracy` and `paired_fitness` read — never a binarized hit. The logistic
MAP maximizes cross-entropy `Σ y·log p + (1−y)·log(1−p)`, valid for any `y ∈ [0,1]`, so a binary
dataset is bit-identical to the old hit path while a graded backend (reciprocal-rank matching, the
L4 outer proxy) keeps its gradient instead of collapsing to an all-miss θ where every posterior ties.

**A graded response is not a coin flip, and θ's SE is corrected for it.** The logistic information
`Σ a²·p(1−p)` is the variance of a coin flip, and a graded response varies far less about the same
mean — a ranked answer at position 5 of 20 is neither a hit nor a miss — so assuming Bernoulli
variance OVERSTATES the uncertainty. Measured against the true sampling spread of θ̂ at n=28: ×1.02
on binary hit/miss, ×1.51 on reciprocal-rank-of-20, ×4.66 on the low-dispersion L4 outer composite —
the inflation that left the outer election unable to crown and PoBB pinned at a tie.
`fit_theta_given_delta` scales the SE by `√φ`, the Pearson dispersion estimated off the fit's own
residuals (Wedderburn 1974). **An estimate, not a knob**, failing safe in both directions: `φ ≈ 1`
leaves a dichotomous campaign unchanged, `φ < 1` returns a graded backend's real precision, `φ > 1`
widens the SE on an overdispersed one. It is floored — a response with no residual variance carries
no evidence about its own dispersion, and an unfloored `φ→0` would report infinite confidence.

**1PL today, 2PL when the data earns it.** The current model is difficulty-only. With enough
observations per sample a 2PL fit adds per-sample **discrimination** `aₛ` — how sharply a sample
separates able from unable candidates, i.e. its signal-to-noise — giving both selection and the gate
more power. It graduates **per-dataset**, behind the same θ interface, only when it provably beats
1PL out-of-sample (cross-validated held-out fit + hysteresis), so it never regresses a thin dataset.

---

## Reading a round — which column is the verdict

**θ is the verdict; accuracy is not.** Two things move raw accuracy without any change in ability,
so it is not comparable across rounds or across arms within a round:

- **The subset moves.** `per_round_resubset` is ON by default, so consecutive rounds are scored on
  different samples. Round N's accuracy and round N+1's are two different exams. It can also
  FREEZE: the acquisition is a function of the ruler and a leader θ pinned by the archive, so once
  both settle every round re-picks the same argmax — measured, two consecutive rounds drawing an
  identical 28 ids. Neither state makes the two rounds' accuracies comparable.
- **Arms are truncated at different depths.** PoBB cuts a weak arm early, and `build_round_order`
  front-loads the parent's misses (a hit enters only every 4th slot), so a cut arm was graded on
  a harder prefix than a survivor. Its raw rate is penalised for where it stopped.

So **a winner with lower accuracy than a rival, or than the previous round, is normally correct** —
`elect_round_winner` ranks difficulty-adjusted ability *against the noise it cleared*
(`P(θ_cand > θ_parent)`, the same reading PoBB cuts on), which corrects for both. When an operator
reads the accuracy column and calls the election wrong, say which column the election used; do not
treat the inversion as a defect on their word.

**States where θ is NOT ability, and the pushback above is wrong.** Count them off the list, not
off this sentence — it said "two" while listing three:

- **The 0% floor.** An arm that misses every cell gives the fit no information, so θ pins to the
  same constant regardless of which samples it saw — every zero arm reads identically. At 0% the
  column is a floor, not a measurement, and any lift computed from it is exactly `0.000`.
- **A cold ruler.** Before the δ ruler warms, θ degenerates to logit-accuracy over the arm's own
  subset and is not on the shared scale its name promises. Check `calibration_model` (`None` = cold)
  and `ruler_id` — two θ readings are comparable only when their `ruler_id` matches.
- **A collapsed band.** Acquisition buys the cells whose δ sits nearest the leader's θ, and against
  a wide bank that collapses onto a razor-thin range — measured, 28 cells spanning 0.63 logits on a
  ruler spanning 11.4. Inside a band that narrow every cell is equally hard, so the 1PL fit reduces
  to `θ = logit(accuracy) + c` and the adjustment does no work: ranking on θ and ranking on accuracy
  are the same ranking, and the parent's floor moves with the band's centre rather than its ability
  (one origin read `-0.340` on round 0's spread and `-1.385` on the band). Unlike the two above this
  one is SILENT — the ruler is warm, `ruler_id` matches, every number renders.
  Check the round's own cells against the ruler's δ spread before trusting a θ lift: a panel whose
  δ all sit within a logit of each other carries no difficulty information to adjust for, so the
  θ column there is logit-accuracy wearing a ruler's name.

  **The question this state makes unanswerable — "is the round-N winner better than C0?" — has its
  own answer, and it is not θ.** `RoundResult.overlap` (`domain/results.py::OverlapReading`) reads
  C0 and every winner since on ONE set of cells all of them answered, chosen from what the line
  already shares and topped up on the new winner alone. It is a rate, not an ability, so it needs
  no ruler and no adjustment — which is the point: it is what remains readable when the scale
  underneath θ has collapsed. It is REPORT-ONLY and deliberately so; fed to the election it would
  identify the parent better than the arms it judges. Round documents, `log.md`, `review.md`,
  the round-close terminal line and the candidates chart's `overlap` series all render the same
  reading, under that one name. It does not repair the acquisition — it measures around it.
- **A ruler HOLE — impossible now, and named because it was silent for so long.** A cell missing
  from a warm ruler was graded δ=0, which is a *position* on the scale rather than a neutral value:
  against a centre near +2.1 it scored an unmeasured cell as easier than anything ever measured and
  pulled θ down ~2 logits. `fit_theta_given_delta` raises on it now, and `Cycle.calibrate_ruler`
  makes coverage a postcondition by EXTENDING the ruler onto each round's cells.

## ⚠️ The crowning bar is an OPEN defect — be skeptical of anything resting on it

**Unresolved as of 2026-08-29. This is not a caveat on a working mechanism; it is a known bug with
no fix chosen.** Everything above tells you which COLUMN the election reads. This section says the
BAR it reads that column against does not currently test anything, so a crowned winner is weaker
evidence than the word "winner" implies. Treat any claim that rests on "this round improved" as
provisional, and say so rather than passing it on.

- **The bar is a bare point estimate.** `selection.py` admits on `lift > 0.0` — no interval, no
  multiplicity correction — and `winner.py` sets `improved = bool(winner_id)`. With three arms,
  P(at least one positive | every arm identical to the parent) is **0.875 per round**.
- **Almost nothing separates.** `separable=True` in 6 of 508 banked rounds; `round_not_separable`
  fired 362 times. `separable` gates the L1 patience reset — it does NOT gate adoption.
- **The posterior did no work, and that half is FIXED.** The quasi-likelihood dispersion φ was
  floored at a constant, which caught 8 of 9 outer arms (raw median 0.0127) and left θ_se not
  varying with the arm at all — `p_exceeds` reduced to a monotone map of the raw gap, so
  rank-by-posterior WAS rank-by-gap. φ is now shrunk toward the nominal dispersion on its own
  degrees of freedom (the inverse-gamma the two σ already use), so the same 9 arms carry 7
  distinct dispersions instead of 2. Posteriors are WIDER — the near-tie in
  `test_pobb_epsilon_is_graded_by_depth_not_scalar` now dies one sample later — so expect PoBB to
  eliminate slightly more slowly and a round to cost a little more.
- **The right toolkit exists and is not on this path.** `holm_adjusted`, `exact_paired_reading`,
  `exact_p_floor`, `cells_for_exact_verdict`, `min_detectable_effect`, `panel_precision` are wired
  to `application/evidence.py`, the offline read verb, and to nothing the election calls.
- **At the current width it could not pass anyway.** `cells_for_exact_verdict(3) = 7` against a
  6-cell panel, so no Holm-corrected exact verdict is reachable at α=0.05 at ANY effect size.

**Why it is still open rather than fixed.** The largest error was elsewhere — each inner cell fit
its own δ scale, so re-reading identical rows moved θ by a whole winning margin (§ above,
`runner/inner/ruler.py`). That is fixed, and it changes what a re-read of separability would say.
Redesigning the bar against the old numbers would be tuning to noise. **The next step is to
re-read separability on a run that used the shared ruler, and only then choose a bar** — not to
tighten one now.

**The ruler grows; the anchor does not.** `ruler_id` names the ANCHORING fit, not the membership, so
it is stable across the extensions that add cells within a cycle — that is what lets two θ read at
different depths stay comparable. `ruler_n` on each round says how many cells the scale carried when
that round was read. The ruler is persisted (`RulerRecord`), so a resume reuses it rather than
re-deriving a different one from a grown archive, and a fork inherits its parent's.

**On the recursion the scale is the SPAWNER's, not the cell's.** An inner cell's evidence epoch
hides everything banked before it started, so the only arms it could fit against were its own —
the δ came out of the treatment under test, and re-running byte-identical origin rows returned θ
spread over a whole winning margin. `application/runner/inner/ruler.py` fits it once at the outer
round boundary, pooled over every arm that dataset has banked, and hands it down through
instrument mode; the cell reads on it rather than deriving one. Same anchor for every candidate
measured against a cell, so the origin level genuinely cancels in `variant − origin`.

## What this is for — separability

Most candidates are dead. The job is to discover that in the fewest measurements possible, by
spending each measurement where it most changes what we believe.

**Separability is the precondition both mechanisms assume.** Per-sample score variance splits in two:
**within-candidate** (one candidate, one sample, re-run — generation stochasticity, noise) and
**between-candidate** (hold the sample, vary the candidate — the signal an optimizer climbs). A
dataset rewards optimization only where the second exceeds the first, and where it does not,
optimization fails *silently*: a round whose samples carry no between-candidate variance cannot rank
its arms however good the proposals are, so an unusable instrument reads as a stalled optimizer.
That is the whole reason to screen. The origin-*score* bar is a different question, owned by
[`../research/benchmarks.md`](../research/benchmarks.md) § The admission bar.

**Both halves already have an instrument.** *Within* → the `noise-floor` verb, but only where a
repeat is a real second call: above the sample level a re-ask replays the content-addressed caches
and its spread is zero by construction, which is why the L4 panel carries no within-cell term.
*Between* → discrimination `aₛ`, above: a high-`aₛ` item separates abilities by construction.
**Screen a whole dataset before wiring it, then pick the subset inside one** — the second use is the
hard-sample leaderboard, the same `pick_value` two sections down.

Source: **`p1`, [arXiv:2604.08801](https://arxiv.org/abs/2604.08801)** (its *system prompt* / *user
prompt* are our *candidate* / *sample*) — published justification for the open `aₛ`-weighted subset
pick, not a second mechanism. Two protocol findings travel with it: **a small high-separation subset
beats a large one** (separability is the axis that buys signal, not subset size), and **a temporally
later edition of a dated benchmark makes a contamination-resistant held-out set** (one year's AIME →
the next: transfer rather than fit; not a general substitute for a canonical split). L4 calls the
same property **informative width** ([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)).

---

## The acquisition score — `pick_value`, and it has two terms

For the current candidate `c` with 1PL ability posterior `(μ_c, var_c)`, against the seed
`(μ_s, var_s)`, on sample `s` with population profile `(δ_s, se_δ_s)`:

```
pick_value(s) = decision_information_gain(s) + delta_learning_gain(s)
```

**Term 1 — `decision_information_gain`: will this sample move the verdict?** The mutual information
(nats) between the sample's next outcome and the verdict `θ_c > θ_s`. It rewards a sample only when
the outcome is genuinely uncertain *and* either branch would shift the keep/abort belief. In the
means-known limit it recovers Bernoulli Chernoff information (Garivier–Kaufmann 2016,
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

**Term 2 — `delta_learning_gain`: will this sample teach us the ruler?** The expected entropy drop
(nats) in the sample's own difficulty `δ_s`. One Bernoulli outcome adds Fisher information
`p(1−p)/scale²` to the prior δ-precision `1/se_δ²`, giving `½·ln(1 + se_δ²·p(1−p)/scale²)` with
`scale² = 1 + π·se_δ²/8`.

**This second term is not optional, and it is exactly the "bias toward unsolved samples":** without
`delta_learning_gain` the seed-centred first pick degenerates to "prefer lowest `se_δ`" and
**starves the unmeasured headroom**. It is largest for *under-measured* samples (large `se_δ`) whose
outcome is a genuine coin flip (`p≈0.5`), and decays to zero both for well-measured samples and for
resolved always-hit / always-miss ones (`p(1−p)→0`) — so it explores the unknown without
re-promoting what the run has already pinned.

Together they are a **Knowledge Gradient** acquisition — the one-step Bayesian question "how much
would measuring `(c, s)` shift our point estimate of the best candidate?", closed-form for Bernoulli
observations under Laplace.

**Selection is parameter-free at the policy level** — no swap thresholds. `select_round_subset`
ranks the whole bank each round and takes the top `budget`: *exploit* falls out of the ranking
(samples on the contested band `δ_s ≈ leader θ` carry the most decision information and sort to the
top), *explore* falls out of the prior. Note the strength of that second term: an unmeasured sample
carries the population `σ_δ`, and `delta_learning_gain` rises with that SE, so unmeasured samples
**outrank** measured ones rather than merely competing — and they tie with each other, so the
tiebreak (ascending `sample_id`) drains the bank in stored order. On a bank stored grouped by label
that yields disjoint single-label panels per round, and cross-round accuracy stops being a series.
Cold start → bank-order prefix. The scoring-set floor is `elimination_n_min`.

**On by default — but warm-gated.** `mechanisms.selection.per_round_resubset` (default `True`):
while the δ ruler is still cold the subset stays frozen to the campaign-start prefix
(`bank[:budget]`), thawing to adaptive once the ruler locks. Off → that frozen prefix for the whole
campaign, so the sample set is fixed across rounds and accuracies are directly comparable.

---

## The round order — `build_round_order`, one static order per round

Within a round there is **one** scoring order, shared by every candidate, and nothing re-sorts
mid-round. It is built by partitioning on the **seed's** per-sample grades:

- **MISS-stratum** — seed grade < 1.0, *or not yet measured by the seed* (an unknown is a potential
  win, and fronting it warms the per-sample backfill earliest). Ordered by **ascending δ**: easiest
  win opportunities first — a live candidate proves itself immediately, and a dead one's misses on
  the easiest wins are the strongest futility evidence.
- **HIT-stratum** — seed grade ≥ 1.0. Ordered by **descending δ**: likeliest regression points first.

Every 4th position takes the next HIT-stratum sample (the regression probe); all other positions
take the next MISS-stratum sample; when a stratum runs dry the other's remainder follows.

**Why k=4.** Pure miss-first defers all regression evidence past the miss block. A proportional
interleave spreads the misses so thin that a futility kill lands at the very end. k=4 costs a
pure-tie kill a handful of extra samples and buys a regression probe inside the first
`elimination_n_min` window, plus steady loss accrual for regressors.

It is a **pure function** of (seed grades, ruler, sample ids), so a resumed round re-derives the
identical order with no recorded sidecar. The hard-samples artifact's `pick_score.sample_order` is
this same order seeded by the best candidate — the order the engine will actually execute next round.

**Why static beats adaptive here:** an ability re-fit after every measurement empirically front-loads
the seed's hit set — the zero-information region, where every early paired comparison ties, `p_best`
pins at 0.5, and the elimination gates go blind until the tail. The round's actual decision is "can
this candidate NET the adoption margin against the seed", and that evidence lives only in
discordance-potential samples.

---

## The two round-boundary mutations, and what each writes

Both run at end of round, **zero-signal first**:

1. **Zero-signal filter** — samples with no variance across observations are physically dropped from
   the active dataset, on disk. Pre-policy: they carry no signal to either exploit or explore. Answers
   *"is this sample dead across the campaign?"*
2. **Round-subset selection** — `select_round_subset` picks the `budget` most-informative samples for
   the active scoring set, in memory only. Answers *"given everything measured, which samples should
   the next round score?"*

Beside them, deterministic triage reads each sample's failure streak: **zero-signal** (always-hit or
always-miss) is naturally deprioritized by `p(1−p)→0` with no physical removal; **chronically
failing** is surfaced to Critique and L2/L3; **intermittent** is kept — it has the discrimination.

The post-evolution fit is rewritten at every round-end finalize into two `hard_samples.json` files,
one per scope: `campaigns/{id}/cycles/{id}/hard_samples.json` (this cycle's rounds) and
`campaigns/{id}/hard_samples.json` (those folded with the campaign's archive observations). The
active scoring set is in-memory only — restored on resume by re-running both mutations against the
rebuilt observation history. **Dataset scope is never persisted**: it is cross-campaign, so no
campaign owns it, and `GET /datasets/{name}/heatmap` folds it from the archive per request.

---

## Where it lives in code

- Acquisition score — `intelligence/adaptive_queue_mechanism.py::pick_value` (with
  `::decision_information_gain` + `::delta_learning_gain`).
- Round order — `::build_round_order`, called once per round at
  `optimization/l1/score/loop.py::score_population`.
- Between-round subset pick — `intelligence/exploration.py::select_round_subset`, off the LOCKED
  ruler (still **1PL**: feeding graduated discrimination `aₛ` in here is open,
  [`../specs/roadmap.md`](../specs/roadmap.md) § Fitness comparability).
- Persisted ranking writer — `intelligence/hard_sample_sorter.py::build_hard_samples_artifact_from_observations`,
  which calls the same `pick_value` the between-round pick calls: one function, two trigger points.

## Phase 2 sketch — origin-relative weighting (not shipped)

Weight each archive observation by the producing candidate's relevance to the current one (similarity
+ recency) instead of equally; same scoring framework, richer conditioning. Needs `Observation`
extended with a timestamp + lineage hint — the open design question (archive-wide drift estimate vs
per-node capability curve) determines that schema.
