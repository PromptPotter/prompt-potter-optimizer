# Verdict resolution — the statistical model and the two sample-selection mechanisms

The model every score here is expressed in, and the mechanisms that decide **which** samples get
measured and **in what order**. They answer different questions, and the one the election reads is
blind to every outcome:

| | Question | Function | When |
|---|---|---|---|
| **Round draw** | Which samples does the round score? | `AdaptiveQueue.draw`: a seeded uniform draw, stratum-covered | **Between** rounds — reads no outcome and no ability (§ The between-round draw) |
| **Acquisition score** | Which sample is hardest to call? | `pick_value = decision_information_gain + delta_learning_gain` | the persisted hard-samples ranking alone |
| **Round order** | In what order do we score the round's samples? | `build_round_order` | **Within** a round — one deterministic order, shared by every candidate |

**There is no online per-sample re-fit** — the round order is static by design (§ The round order).
Which *candidate* wins is [`candidate-elimination.md`](candidate-elimination.md)'s; both read θ, so
they never disagree about what "better" means.

**Holm corrects across PAIRS and cannot correct across METRICS.** Any surface reporting a pairwise
comparison must say so: trying several metrics and keeping the tightest is a comparison no column
prices. `p` is null where nothing was tested (below two shared cells) — not a test that returned 1.00.

**Scope:** fixed pipeline, labelled dataset, scalar fitness. θ is one latent ability per candidate,
so multi-objective fitness, open-ended synthesis and unlabelled tasks are not supported.

---

## The model — Rasch θ/δ

```
P(hit_{c,s} = 1) = σ(θ_c − δ_s)
```

Candidate ability × sample difficulty, fit jointly by MAP (alternating Newton, Laplace SEs,
`mean(θ) == 0`; `intelligence/rasch.py::fit_rasch`) ONCE, at ruler lock. Every later reading holds δ
fixed and solves θ alone (`::fit_theta_given_delta`), or the scale moves under the round it judges.
`δ_s` selects samples and ranks hardness; `θ_c` is the **round-winner gate**.

**θ is why per-round resubsetting is safe.** On different subsets raw accuracy drifts — whoever drew
the easier samples wins — but θ is subset-invariant (Rasch specific objectivity).

**Responses are graded, not Bernoulli.** `Observation.response` is the continuous per-sample fitness
∈ [0,1]; the cross-entropy MAP is valid for any `y ∈ [0,1]`, so a binary dataset is unchanged and a
graded backend (reciprocal rank, the L4 outer proxy) keeps its gradient.

**θ's SE is corrected for graded variance.** The logistic information `Σ a²·p(1−p)` assumes coin-flip
variance and OVERSTATES a graded response's uncertainty — measured against θ̂'s true spread at n=28:
×1.02 binary, ×1.51 reciprocal-rank-of-20, ×4.66 on the L4 outer composite (which left the outer
election unable to crown). `fit_theta_given_delta` scales the SE by `√φ`, the Pearson dispersion off
the fit's residuals (Wedderburn 1974) — **an estimate, not a knob**, shrunk toward 1.0 by an
inverse-gamma prior so `φ→0` cannot report infinite confidence. **An all-0/1 sheet takes `φ = 1`
exactly** (`rasch.py::_dispersion`): its residuals against a δ fit on the same rows read as
underdispersion (measured `φ = 0.10`, θ_se 0.28 where the information gives 0.89).

**1PL by default; a dataset graduates to 2PL where it wins held-out CV** (`enable_2pl_graduation`, on).
2PL is the field default, so 1PL is what needs defending —
[`../research/external-constraints.md`](../research/external-constraints.md) § M14 carries the
benchmarks. 2PL adds per-sample discrimination `aₛ`; it graduates per dataset, behind the same θ
interface, only when it beats 1PL out-of-sample (with hysteresis), so a thin dataset never regresses.

---

## Reading a round — which column is the verdict

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-dark.png" alt="Optimizer warnings: rounds 3 to 6 each resolved nothing, because every arm's lift interval spans zero" width="100%">
  </picture>
</p>

**It does not grade its own homework.** With three arms and no real difference, *some* arm beats its
parent most of the time. PromptPotter promotes the best it saw and says, per round, on screen and on
disk, that the lift interval spans zero (`(ns)` beside its `p`). Degenerate candidates (a constant
answer, a scorer-gaming shape) are caught before they count, and the layer that validates a fix is
never the one that proposed it.

**θ is the verdict; accuracy is not** — raw accuracy moves without ability:

- **The subset moves.** `per_round_resubset` is on, so consecutive rounds are different exams.
- **Arms are truncated at different depths.** PoBB cuts a weak arm early and `build_round_order`
  front-loads the parent's misses, so a cut arm was graded on a harder prefix.

So **a winner with lower accuracy than a rival or the previous round is normally correct** —
`elect_round_winner` ranks `P(θ_cand > θ_parent)`, the reading PoBB cuts on. When an operator calls
the election wrong off the accuracy column, say which column it used.

**No harness rule ranks rounds by their own readings.** The `--halt-at` stop
(`runner/termination.py::target_tripped`) reads the optimizer's declared pick (`Cycle.selection`) on
the round that picked it, never each round's composite high-water; the overlap line's members are
those picks too. Only potter's L2/L3 stall comparator keeps the high-water, as its own signal.

**States where θ is NOT ability, and the pushback above is wrong** — enumerated by
`domain/ruler.py::ThetaCaveat`, served so the screen and the optimizer's `confounds` panel read one
verdict. Read per level (`AbilityReading.caveat`), per arm (`ArmFit.theta_caveat`) and per series
(`LevelSeries.caveat`, asked before differencing two levels); a sound round can carry a pinned arm.
Checked in severity order (`ruler.py::theta_caveat`):

- **Disjoint datasets** (`disjoint_scopes`). Each `RulerScope` is its own scale; a lift paired on
  shared cells still holds.
- **The 0% floor** (`floor_pinned`, per arm). An all-miss arm pins θ to a constant whatever it saw,
  so every lift from it is `0.000`. Not answer-collapse (one answer everywhere, which PoBB cuts): an
  arm can answer differently every time and be wrong every time.
- **A flat ruler** (`flat_ruler`). The ruler's own δ span is below `BAND_COLLAPSE_LOGITS`, so θ is
  accuracy plus a constant. What an all-zeros fit looks like; checked before `collapsed_band`,
  whose ratio reads ~1 on a globally flat ruler.
- **A cold ruler** (`cold_ruler`). Too few rows from individuals outside the election: θ is
  logit-accuracy, not a shared scale. The lift (on cells both answered) holds, the level does not.
  `calibration_model` `None` = cold; two θ are comparable only when `ruler.id` matches.
- **A collapsed band** (`collapsed_band`). The round's cells span a sliver of the ruler (measured:
  28 cells over 0.63 logits on a ruler spanning 11.4), so θ = `logit(accuracy) + c` and the parent's
  floor tracks the band's centre (one origin read `-0.340` on round 0, `-1.385` on the band).
  **Silent** — warm ruler, matching id, every number renders.
- **A prior-pinned round** (`prior_pinned`). At least `PRIOR_PINNED_RATIO` of the cells share a δ
  with another cell: ties are the PRIOR setting δ where no observation varied. The pin moves with
  the ruler's composition, so an unchanged prompt walks up the scale round on round. Silent too.
  **Read the LIFT, never the level, never a level across rounds** — the pin cancels within a round.
- **Unmeasured difficulty** (`unmeasured_delta`). A cell the judging scale does not carry
  (`DeltaRuler.unlinked`); θ leaves it out, and two arms can skip different cells, so it does not
  cancel inside a lift. A later round reads it once this round's rows fold in.

  **"Is the round-N winner better than C0?" has its own answer, and it is not θ.**
  `RoundResult.overlap` (`domain/results.py::OverlapReading`) reads C0 and each declared pick
  (`::best_line`, one line for every optimizer) on the fixed ORIGIN PANEL (`::origin_panel` — per
  dataset, the cells the origin answered, every member topped up onto them once). Fixed, because a
  set re-chosen from what the line shared contracted as it grew. It is a rate, needing no ruler —
  readable when θ's scale has collapsed. The panel's rows also feed the election (draw and panel
  pooled, less the parent's crowning cells: `::election_rows`; mid-round cuts stay on the draw), so
  it is a REUSED set and the overlap reading is optimistic for every pick: **the unbiased headline
  is the bench.** Round files, `log.md`, `review.md`, the round-close line and the candidates
  chart's `overlap` series all render this one reading.
- **A ruler HOLE is never graded.** δ=0 is a position, not neutral: against a centre near +2.1 it
  scores an unmeasured cell as easiest and pulls θ down ~2 logits, so `fit_theta_given_delta` reads
  only cells the ruler carries.

## What an arm's lift is read against — `lift_reference`

Every arm's `vs_reference` reading (blocked lift, interval, matched floor) is read against ONE
individual per arm (`Election.fit[ArmKey].vs_reference`, its rows banked as
`RoundResult.reference_rows`); a repair re-measures the arms, never the bar.
`OptimizationConfig.lift_reference` picks it, both values under every optimizer:

- **`best_so_far` (default)** — the round's selected best-so-far, re-scored on the whole panel and
  paired on cells both measured (a truncated arm keeps lift and interval, loses the matched floor).
  Potter's parent IS this individual.
- **`parents`** — the arm's own `parent_ids`, each re-measured on exactly the arm's cells, so every
  arm has a matched floor. A crossover child is read against the BETTER parent on those cells — a
  conservative bar. No parent, no reference. A parent is any measured individual, re-measured under
  its round's banked configuration (`PointContext.measured`).

Neither moves what a selector reads (potter elects on θ against best-so-far; CAPO on its own
objective). `overlap.advance` reads neither — it is the pick's lead over C0 on the origin panel
(`OverlapReading.lead`) and drives potter's stall ladder. Under `parents` a masked election is
undecidable rather than guessed (`mask/load.py::_parent`).

## ⚠️ The crowning bar is an OPEN defect — be skeptical of anything resting on it

**A known bug with no fix chosen, not a caveat on a working mechanism.** The bar the election reads θ
against does not currently test anything, so a crowned winner is weaker evidence than "winner"
implies. Call any "this round improved" claim provisional.

- **The bar is the stopping rule, mirrored.** `selection.py::elect_round_winner` admits on a raw θ
  lift above `rasch.py::FIT_SLACK`, more shared cells won than lost on VERDICTS, AND
  `P(θ_arm > θ_parent)` bounded by those discordant cells
  ([`candidate-elimination.md`](candidate-elimination.md) § The θ rule, step 4) above `1 − ε`, PoBB's
  ε. One discordant cell (support 0.75 whatever θ reads) never promotes. A pick's crowning cells are
  replayed by `rescore_parent` and kept out of later races (`selection.py::crowning_cells`), so the
  parent's bar needs no winner's-curse credit. Still no interval and no multiplicity correction;
  `RoundResult.improved` reads any selection past round 0 as a promotion.
- **Separability is read against C0, not the parent.** A round closes `advanced`
  (`domain/results.py::round_advance`) only where its pick leads C0 on the origin panel by an
  interval clear of 0 and tops every earlier pick there. That `RoundAdvance` resets L1 patience and
  is the clock a result quotes (`index.json::final.rounds_to_separable`); adoption stays `improved`.
  A pick whose reading was not taken closes `unread` (neither reset nor stall); a panel with no pair
  lets the promotion stand alone (`advanced_unpaired`).
- **The right toolkit is only PARTLY on this path.** `holm_adjusted` and `p_floor` reach every pair
  `application/scoring/paired.py::read_pair` takes, and **the floor gates the claim**: `p_value` is
  `max(Student-t p, p_floor)` and `side` is `spans` wherever `p_floor` exceeds `alpha`
  (`domain/paired_reading.py::interval_side`). `min_detectable_effect` and `panel_precision` serve
  reads only. `sign_posterior` bounds elimination and the election's ADMISSION (a bar is absolute);
  the election still RANKS on the unbounded posterior — bounding it moved a banked crown across a
  0.0007 gap.
- **At current width it cannot pass.** `exact_p_floor(6) = 0.031`; Holm across three arms takes it to
  0.094, so no corrected exact verdict reaches α=0.05 at any effect size.

**Why still open:** every number a bar could be designed against predates the tenant ruler. **Re-read
separability on a run that used it, then choose a bar** — do not tighten one now.

**One ruler, one clock.** The tenant holds ONE δ ruler (`ruler.jsonl`,
`infrastructure/store/ruler_store.py`), keyed by sample content, versioned by tick: a round's grade-A
rows fold in after its walks and BEFORE its election (`DifficultyView.link`). An election reads that
version less every row of the parent line (`bench/difficulty.py::judging_ruler`) — fit on the
parent's rows, δ would be its hit pattern and θ would favour it every round.

**The clock is the task's memory scope** (`archive_queries.py::MemoryScope`): an optimizing run reads
the head at each round's open (`OpenAt`), and each close re-reads every round on one stamp
(`LevelSeries`), so two levels difference wherever the series' `caveat` is null. A head-to-head arm
reads its head-to-head's frozen version plus its own campaign's lines (`ArmOf`, `RulerReach`); an L4
inner cell reads its outer round's (`CellOf`), so `variant − origin` cancels.

**Every reading names its scale.** `AbilityReading.ruler` and `RoundOutcome.ruler` carry a
`RulerStamp` (version, excluded individuals, purpose, scope), so resume, replay and `verify` re-fit
the scale a reading was taken on. `AbilityReading.cells` counts θ's cells.

## What this is for — separability

Most candidates are dead; the job is to discover that in the fewest measurements. Per-sample
variance splits into **within-candidate** (re-run noise) and **between-candidate** (the signal an
optimizer climbs). Where the second does not exceed the first, optimization fails *silently*: an
unusable instrument reads as a stalled optimizer. Hence screening. The origin-*score* bar is
[`../research/benchmarks.md`](../research/benchmarks.md) § The admission bar's.

*Within* → the `noise-floor` verb, only where a repeat is a real second call (above the sample level
a re-ask replays the content-addressed caches, which is why the L4 panel carries no within-cell term).
*Between* → `aₛ`. **Screen a whole dataset before wiring it, then pick the subset inside one** — the
hard-sample leaderboard, `pick_value` below.

Source: **`p1`, [arXiv:2604.08801](https://arxiv.org/abs/2604.08801)** (its *system* / *user prompt*
= our *candidate* / *sample*) — justification for the open `aₛ`-weighted subset pick. **A small
high-separation subset beats a large one**, and **a later edition of a dated benchmark is a
contamination-resistant held-out set** (one year's AIME → the next; not a substitute for a canonical
split). L4 calls it **informative width** ([`../specs/l4-outer-loop.md`](../specs/l4-outer-loop.md)).

---

## The acquisition score — `pick_value`, and it has two terms

For candidate `c` with 1PL posterior `(μ_c, var_c)`, seed `(μ_s, var_s)`, sample `s` with `(δ_s, se_δ_s)`:

```
pick_value(s) = decision_information_gain(s) + delta_learning_gain(s)
```

**Term 1 — `decision_information_gain`: will this sample move the verdict?** Mutual information
(nats) between the next outcome and `θ_c > θ_s`; in the means-known limit, Bernoulli Chernoff
information (Garivier–Kaufmann 2016, Track-and-Stop).

```
p0      = Φ((μ_c − μ_s) / √(var_c + var_s))                    # current verdict belief
μ⁺, v⁺  = update(μ_c, var_c, δ_s, se_δ_s, hit=True)            # posterior after a hit
μ⁻, v⁻  = update(μ_c, var_c, δ_s, se_δ_s, hit=False)           # posterior after a miss
p⁺      = Φ((μ⁺ − μ_s) / √(v⁺ + var_s))
p⁻      = Φ((μ⁻ − μ_s) / √(v⁻ + var_s))
p̄       = marginal_hit_probability(...)

decision_information_gain(s) = H(p0) − [ p̄ · H(p⁺) + (1 − p̄) · H(p⁻) ]
```

**Term 2 — `delta_learning_gain`: will this sample teach us the ruler?** Expected entropy drop in
`δ_s`: `½·ln(1 + se_δ²·p(1−p)/scale²)`, `scale² = 1 + π·se_δ²/8`.

**Term 2 is the "bias toward unsolved samples" and is not optional:** without it the first pick
degenerates to lowest `se_δ` and starves the unmeasured headroom. It peaks for under-measured coin
flips and decays to zero for well-measured and always-hit/always-miss samples. Together: a
closed-form **Knowledge Gradient** acquisition under Laplace.

## The between-round draw — blind, so the election reads the pool

**No acquisition term chooses a round's cells.** The election's rows must be the rows the held-out
bench would read: a draw ranked on θ or on the origin's headroom buys the parent's misses, and the
lift is a lift on that slice (measured: a θ-elected winner trailed its parent on the bench while the
iid panel's best arm went unchosen). `AdaptiveQueue.draw` (`optimizers/potter/members.py`) takes the
search split less the parent's crowning cells and, once the ruler is warm, shuffles each stratum on
the round's seeded `Replay.rng` (a resume redraws the same cells). `elimination_n_min` ruler
cells lead the shuffle — the cells a warm θ is read on (`rasch.py::theta_cells`), which PoBB's gates
and the coverage floor count. `adaptive_queue_mechanism.py::covered_prefix` fronts one cell per
`source_pin` level. Where the parent misses is the round ORDER's business, never the draw's.

**Warm-gated.** `per_round_resubset` (default `True`): while δ is cold the subset stays the
campaign-start prefix (`bank[:budget]`), which warms the ruler fastest, thawing once it locks. Off →
that prefix all campaign, so accuracies compare directly.

---

## The round order — `build_round_order`, one static order per round

One scoring order per round, shared by every candidate, never re-sorted mid-round, partitioned on
the **parent's** grades:

- **MISS** (grade < 1.0) — **ascending δ**: a live candidate proves itself at once; a dead one's
  misses on the easiest wins are the strongest futility evidence.
- **HIT** (grade ≥ 1.0) — **descending δ**: likeliest regressions first.
- **UNKNOWN** (parent never answered) — **ascending |δ − μ_δ|**, drained after the misses. Unknown is
  not a miss: under resubsetting round 1 shares no cell with the parent, and filing unknowns as
  misses empties the hit stratum and never fires the probe.

Every 4th slot takes the next HIT (the regression probe) **while a miss or unknown is unplaced**.
**Why k=4:** pure miss-first defers all regression evidence; a proportional interleave makes a
futility kill land at the end. k=4 buys a probe inside the first `elimination_n_min` window.

**The first `elimination_n_min` slots take only cells θ is read on** (`theta_cells`: warm, the
ruler's; cold, the parent's). PoBB's entry gate, ε ramp and lock-in and the coverage floor count
cells read, not rows walked; led by the strata alone, a panel of mostly parent hits reaches its last
ruler cell at its final row and no arm can be cut. Cost: the ruler learns off-ruler cells slower.

It is a **pure function** of (parent grades, ruler, sample ids, `elimination_n_min`), so a resume
re-derives it with no sidecar; the hard-samples artifact's `round_order` is the same call.

**Why static beats adaptive:** a per-measurement re-fit front-loads the parent's hit set, where every
early paired comparison ties, `p_best` pins at 0.5 and the gates go blind; the decision lives only in
discordance-potential samples. **At a low base rate the static order hits the same region with the
symptom INVERTED:** the candidate misses the MISS stratum too, near-ties narrow both SEs, and `p_best`
reads DECISIVE (`sealqa/longseal-12`: four arms cut at `elimination_n_min`, identical to six
decimals). Read `n_discordant` on the p_best stream to tell the two apart.

---

## The round-boundary draw, and what it writes

The one end-of-round mutation is the round draw (§ The between-round draw), in memory only; no sample
is removed. Triage reads each failure streak: **zero-signal** is deprioritized by `p(1−p)→0`;
**chronically failing** goes to Critique and L2/L3; **intermittent** is kept — it discriminates.

**The hard-sample view fits nothing.** Each close rewrites
`campaigns/{id}/cycles/{id}/hard_samples.json` (`domain/cells.py::HardSamples`,
`intelligence/hard_sample_sorter.py::build_hard_samples`, the one `pick_value` caller): the cycle's
graded cells on its ONE ruler (`Cycle.difficulty.ruler`) and the abilities read on it
(`Election.fit[ArmKey].theta`, `AbilityScale.frontier`). A sample off the ruler is served in its
`DeltaState` with no δ and no pick score — never 0.0. Campaign scope names the cycle it reads
(`application/scoring/measurement_log.py::campaign_scope_cycle`); every surface ranks via
`::rank_hard_samples`. **Dataset scope is never persisted** (no campaign owns it):
`GET /datasets/{name}/cells?scope=dataset` anchors a ruler per request on archived cells
(`bench/difficulty.py::calibrate_delta_ruler`) and serves δ with no pick score.

---

## Phase 2 sketch — origin-relative weighting (not shipped)

Weight each archive observation by the producing candidate's relevance (similarity + recency). Needs
`Observation` to carry a timestamp + lineage hint; the open choice (archive-wide drift vs per-node
capability curve) fixes that schema.

## Phase 3 sketch — per-STEP difficulty for turn-structured cells (not shipped)

For multi-turn or multi-step cells, fit δ and `a` **per step**: more information per cell, so fewer
cells for the same SE where a cell costs minutes and dollars. **The gain is a theorem:** one outcome's
information `a²·p(1−p)` peaks at `θ ≈ δ`, while a step-structured item contributes at each of its
`k−1` thresholds and covers a wider θ range — which matters because a round's arms are spread.

**A turn is not an item.** Turn 2 is conditioned on turn 1, so scoring turns as items claims `2N`
observations for `N` — **overstated precision**, and elimination cuts on confidence it never earned.
**The cell stays the atom; the fix is a testlet model** (Bradlow/Wainer/Wang, or bi-factor):
per-step `b_j`/`a_j` plus a person×testlet random effect. A graded per-step composite already reaches
θ today (§ The model); per-step fitting adds thresholds that are **fitted rather than imposed**.

Three gates:

- **Identifiability.** Per-step 2PL plus testlet effects against ~20–34 cells a round fits noise.
- **Step-schema stability.** "Step 2" must be the same KIND of thing across cells — a fixed SEMANTIC
  schema decided when the dataset is cut ([`../operations/datasets.md`](../operations/datasets.md)
  § Adding a dataset); the shipped `retrieve → ground → answer` schema and its graders are
  [`../../promptpotter/application/scoring/judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md) § The step schema's.
- **A new parameter space is a new `RulerScope`** — nothing before it compares across the line.

**The enabling condition must not be skipped: bank each step's term separately.** Parts persist as
named keys in `pipeline_data`, and `Scorer.grade` re-grades archived rows under the current formula,
so a testlet fit later is a re-read, not a re-measure. Collapse to a scalar and that door shuts.
