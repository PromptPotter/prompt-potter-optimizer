# Paired-Sample PoBB

PoBB (Posterior-of-Being-Best, Russo 2016) compares the round's current
candidate against the pool of completed priors and abandons it when its
posterior probability of being the best falls below ε. The original
formulation assumes every arm is observed on an i.i.d. sample of the same
underlying distribution.

PromptPotter's adaptive picker intentionally violates that assumption.
The picker reorders each candidate's evaluation so the most diagnostic
samples land first — that lets a clearly inferior candidate be abandoned
within a handful of queries instead of burning the full sample budget.
Online adaptive ordering is the whole point of cheap loser elimination.

This document explains the failure mode the asymmetric ordering creates,
the paired-sample mechanism that fixes it without giving up the sorter's
benefits, and the on-disk shape that lets resume replay paired decisions
without re-running them.

## The pathology

A real example from the AIME 2025 cycle that motivated this fix:

```
Round 1, 5 candidates evaluated:
  3b6553… : sample_ids {0,1,2,3,4,5,6,7}, 8/8 hits = 100%  ← leader-locked
  e589d4b7: sample_ids {0..19},          10/20 hits =  50%
  68c5d7fa: sample_ids {0..6},            4/7  hits =  57%  (eliminated)
  0067efd2: sample_ids {0..10},           6/11 hits =  55%  (eliminated)
  795183e0: sample_ids {0..19},           8/20 hits =  40%

Round 2, 3 candidates: all PoBB-eliminated at q6, p_best=0.0
Round 3, 6 candidates: all PoBB-eliminated at q6, p_best=0.0
```

The round-1 "winner" `3b6553…` was leader-locked at exactly the floor
(`lock_in_n_min=8`, `lock_in≥0.95`) by getting 8/8 on the easy prefix —
samples 0–7, the first eight the sorter hands out. The two candidates
that completed the full 20 scored 40% and 50% on the same prompt family.
The lock-in fired on a lucky-prefix streak, not on a genuinely dominant
candidate.

Rounds 2 and 3 then compared every new candidate against that "100%"
leader using **unpaired** PoBB:

* Leader vector: `[1, 1, 1, 1, 1, 1, 1, 1]` on samples `{0..7}`.
* Candidate vector: `[0, 0, 0, 0, 0, 0]` on samples `{9, 12, 13, 6, 14, 8}`
  (sorter's hard-first order, hardest for this round).

The two arms share exactly one sample (#6). The MC posterior on those
vectors says the candidate has ~0% chance of being best, so it gets
eliminated at q6 — but the comparison is statistically meaningless: the
candidate was tested on samples the leader was never tested on. The
leader's 100% is unbeatable in principle because it never had to face
the hard samples.

Origin missed all five hard samples (`#8, #9, #12, #13, #14`); the
leader's prompt is a small variant of origin and almost certainly misses
most of them too. The optimizer keeps reporting "100% — no improvement"
while every candidate looks dead-on-arrival.

## The mechanism

The fix is one design choice with several call-site consequences: **PoBB
priors are sample-keyed, and the leader is backfilled on the candidate's
upcoming sample order before each candidate is evaluated.**

### 1. Priors stored sample-keyed, not as flat vectors

`PoBBCheck` no longer holds `priors: dict[cid → list[float]]`. It holds:

```python
priors_by_sample: dict[str, dict[str, float]]   # cid → sample_id → fitness
prior_sps:        dict[str, JobSearchPoint]      # cid → the SP that produced those scores
```

`register_completed(results, candidate_id, sp)` ingests full
`QueryMeasurement`s (which already carry `sample_id` and `fitness`),
builds the sample-keyed map, and remembers the prior's `JobSearchPoint`
so its measurements can be extended later.

### 2. Backfill before evaluation

`PoBBCheck` accepts a `backfill_fn` at construction. Inside the per-round
loop in `score_population`, right before each candidate's `score_search_point`
call:

```python
samples_by_id = {str(s.id): s for s in dataset}
await elim_check.backfill_priors([s.id for s in dataset], samples_by_id)
```

The candidate's budget under the adaptive picker is the full dataset
(the picker chooses *which* sample to measure next, but the candidate
runs until PoBB eliminates or the dataset is exhausted), so backfill
covers the whole dataset rather than a static prefix. `backfill_priors`
walks every prior in the pool, finds the sample IDs the prior hasn't
been measured on yet, and calls `backfill_fn(prior_sp, missing_samples)`.
The backfill function is a thin closure over `score_search_point` so
the new measurements:

* Hit the per-sample archive cache when those `(prior_sp, sample)`
  pairs already exist (cross-cycle, cross-fork — the MeasurementArchive
  is the DB core).
* Run fresh on the leader's prompt for genuinely new pairs, land in
  `archive/measurements/`, and become reusable for every future round.

### 3. Paired comparison in `check()`

`PoBBCheck.check()` reads the candidate's sample IDs straight off
`results`, then for each prior builds a paired vector by mapping each
candidate sample ID to that prior's stored fitness:

```python
paired_priors[cid] = [prior_map[sid] for sid in candidate_samples]
```

Priors that don't cover every sample the candidate has measured are
excluded (this only happens when backfill was skipped or failed —
otherwise every prior is guaranteed to cover the candidate's IDs).
The seeded Monte Carlo runs on these paired vectors. Same sample set
on both arms, statistical validity restored.

### 4. Lucky-prefix is self-correcting

Walk through the AIME example with paired-PoBB enabled:

* Round 1: candidate `3b6553…` leader-locks at 8/8 on `{0..7}`. Its
  sample-keyed history is `{"0": 1, "1": 1, ..., "7": 1}`.
* Round 2 starts. Candidate C2.0's sample order is `{9, 12, 13, 6,
  14, 8}` (hard-first).
* `backfill_priors` looks at `3b6553…`'s coverage, finds it missing
  `{8, 9, 12, 13, 14}`. Calls `backfill_fn(3b6553…_sp, [s8, s9, s12,
  s13, s14])`. These measurements run fresh (or hit cache from a
  sibling cycle) and almost certainly include several misses — the
  same prompt family that produces 50% on the full set will miss
  most of these.
* `3b6553…`'s history is now `{"0": 1, ..., "7": 1, "8": 0, "9": 0,
  "12": 0, "13": 1, "14": 0}` — actual coverage of the round's
  hard samples.
* PoBB comparison: leader vector on `{9, 12, 13, 6, 14, 8}` is now
  `[0, 0, 1, 1, 0, 0]` (mean 0.33), candidate vector is `[0, 0, 0,
  0, 0, 0]`. Candidate still loses, but the comparison is honest —
  and **the leader's recorded mean dropped from 1.0 to 0.5-ish on
  its full coverage**, automatically deflating the false-100% floor
  the sorter had been smashing every candidate against.

No separate "lower the lucky-prefix lock-in threshold" code path. No
display patch for "100% on n=8". The lucky-prefix inflation is a
mechanical consequence of unpaired comparison, and paired comparison
mechanically removes it.

## Cost

The backfill runs once per candidate, on the candidate's sample order
(typically ~6–10 hard samples). Within a round:

* First candidate triggers backfill on every prior over its sample
  order. Round 2 starting with R1_winner needs ~5 fresh leader
  measurements.
* Subsequent candidates' sample orders heavily overlap (same hard
  samples for the round). Their backfill is mostly cache hits.
* Across rounds, the leader accumulates measurements — by round 3+
  the leader has near-full coverage and backfill is free.

Net: roughly one extra "candidate-equivalent" of LLM spend per round,
amortized across the round. That's the cost of statistical validity.

## On-disk shape and replay

Each `ELIMINATION_CUT` / `LEADER_LOCK_IN` decision record (in
`campaigns/{cycle}/rounds/round_NNNN.json`) now carries the paired
snapshot under `data`:

```json
{
  "kind": "elimination_cut",
  "outcome": true,
  "inputs_ref": {
    "candidate_id": "abc…",
    "prior_candidate_ids": ["R1_winner"],
    "queries_scored": 6,
    "epsilon": 0.05,
    "n_min": 6,
    "round_num": 2,
    "recorded_p_best": 0.0
  },
  "data": {
    "p_best": 0.0,
    "leader_id": "R1_winner",
    "p_best_snapshot": { "R1_winner": 1.0, "abc…": 0.0 },
    "candidate_sample_ids": ["9", "12", "13", "6", "14", "8"],
    "prior_histories": {
      "R1_winner": {
        "9": 0.0, "12": 0.0, "13": 1.0,
        "6":  1.0, "14": 0.0, "8":  0.0
      }
    }
  }
}
```

`candidate_sample_ids` is the ordered list of samples the candidate had
measured at decision time. `prior_histories[cid]` is each prior's fitness
restricted to exactly those samples (after backfill).

This makes the divergence replayer self-contained:

```python
# resume_and_fork/replayers.py::_pobb_replay_snapshot
candidate_sample_ids = data["candidate_sample_ids"]
prior_histories      = data["prior_histories"]
cur_by_sample        = {r["sample_id"]: r["fitness"] for r in rescored_results}
current = [cur_by_sample[sid] for sid in candidate_sample_ids]
paired_priors = {
    cid: [hist[sid] for sid in candidate_sample_ids]
    for cid, hist in prior_histories.items()
}
# … same seeded MC as the live check …
```

No cross-round "find R1_winner in prior rounds" logic, no backfill
during replay. The decision record is the entire input. When the active
scorer differs from the recorded one, the candidate side gets rescored
(by `resume.py::_rescore`); the prior side stays at the recorded fitness
(approximate but correct enough — a scorer change that materially shifts
priors will surface as divergence via the candidate side).

## Code map

| File | Role |
|---|---|
| `promptpotter/application/optimization/pobb/elimination/checks.py::PoBBCheck` | Sample-keyed priors, `backfill_priors`, paired `check()`, `snapshot_priors`, `set_sample_universe` (budget for the dominance gate) |
| `promptpotter/application/intelligence/adaptive_picker.py` | Online picker: `update_theta_posterior`, `decision_information_gain`, `model_information_gain`, `pick_value`, `next_sample`, `expected_order` |
| `promptpotter/application/optimization/l1/score.py::score_population` | Builds the `backfill_fn` closure + the `_next_sample(scored_outcomes)` closure; injects both into PoBB / the query loop |
| `promptpotter/application/optimization/l1/score.py::score_one_candidate` | Calls `await elim_check.backfill_priors([s.id for s in dataset], samples_by_id)` over the full dataset budget before each candidate evaluates |
| `promptpotter/application/scoring/search_point_scorer.py::_run_query_loop` | Per-step call to `next_sample(scored_outcomes)` — no static iteration order |
| `promptpotter/application/optimization/l1/population.py::pobb_decision_data` | Embeds `candidate_sample_ids` + `prior_histories` into the decision record |
| `promptpotter/application/optimization/resume_and_fork/replayers.py::_pobb_replay_snapshot` | Reads paired snapshot from `data`; no cross-round resolver |

## Sample-selection: online adaptive picker

Backfill makes the paired comparison statistically valid; the per-candidate
**iteration order** is what makes it cheap. The picker
(`promptpotter/application/intelligence/adaptive_picker.py`) is a 1PL
Item Response Theory online sequential selector — at each step it folds
the candidate's measured `(δ_s, se_δ_s, hit)` outcomes into a Gaussian
Laplace-approximation posterior on `θ_c`, then picks the next sample by
maximizing one blended, one-step-greedy objective — the **pick-value**,
two terms in nats:

- **decision information gain (exploit, dominant):** `I(Y_s ; verdict)`,
  the mutual information between the next outcome and the keep/abort
  verdict `θ_c > θ_s` against the seed (`μ_s` the seed prior's fitted
  ability). Picks the sample whose outcome maximally separates candidate
  from seed — directly minimizes expected queries to a confident verdict.
  The means-known limit recovers Bernoulli Chernoff information
  (Garivier-Kaufmann 2016).
- **model information gain (explore, small weight):** `½·log(1 + w̄·se_δ²)`,
  the expected reduction in a sample's difficulty uncertainty. A small
  `explore_weight` pull toward poorly-characterized / never-solved
  samples so the global Rasch model keeps improving; exactly zero when
  `se_δ → 0`, so it never fights the decision term where the model is
  already sharp.

`pick_value = decision_information_gain + explore_weight ·
model_information_gain`. Both terms are in nats, so `explore_weight`
(`ExplorationConfig`, default 0.15) is dimensionless; kept well below 1
the picker stays decision-dominated — PoBB *is* a keep/abort decision and
that's what the evaluation budget should buy down.

The heatmap's hardest-first `sample_order` (the spec's `|δ_s|` sort)
lives on the per-cycle artifact for display; the artifact's
`pick_score.per_sample` is the blended pick-value for a fresh mutation of
the seed — ability prior centred on the seed's ability `θ_seed`, not the
population-mean anchor 0 — a descriptive snapshot of "how informative is
this sample on a brand-new candidate." The **live** picker uses its own
per-candidate posterior, so the artifact's order and the candidate's
actual measurement order can diverge — the artifact is what the operator
sees in the webapp, the live order is what the candidate ran against on
disk.

Why this beats a static "hardest first" iteration: hardest-first treats
the picker as a property of the *dataset* (Rasch δ alone). The adaptive
picker treats it as a property of the *candidate-vs-seed comparison*,
which is what PoBB actually tests. The decision term gives zero score to
samples where the seed and candidate are predicted to agree (both
unanimous-HIT or both unanimous-MISS) regardless of how hard they look on
the δ axis; samples in the gap between `μ̂_c` and `μ_s` carry maximum
info — and the picker re-evaluates that gap after every measurement, so
the order is *responsive* to the candidate's running evidence rather than
frozen at candidate-start.

## Elimination ladder: dominance before posterior

`PoBBCheck.check()` runs two gates in order. The first is pure arithmetic:

```
cand_max_final_hits = cand_hits + (budget − queries_scored)
if cand_max_final_hits < seed_total_hits → ELIMINATE
```

If even hitting every remaining sample can't tie the seed prior's
already-known total on the candidate's intended sample budget, no further
scoring can flip the comparison. Fires regardless of the latest sample's
hit/miss outcome — the dominance is structural, not evidential. Seed is
the origin (R1) or the prior round's winner (R2+); the seed's coverage
across the candidate's budget is guaranteed by the backfill above.

The second gate is the existing Bayesian posterior — `p_best < ε` on the
paired vectors. The two gates are complementary: dominance is SPRT's
deterministic corner (probability of catching up = 0); the posterior gate
is sequential evidence accumulation against an ε threshold. Dominance
fires first because "mathematically impossible" beats "probably won't."

The `predictable_tail_*` δ-aware ε scaling that lived here previously
was a heuristic version of this — loosen ε when the remaining samples are
high-|δ| because no information is coming. Replaced because the dominance
check states the same intuition exactly (`p=1` corner) instead of
approximating it via a tunable multiplier.

## Related concepts

* `docs/concepts/the-loop.md` — where PoBB fits in the round lifecycle.
* `docs/concepts/scoring-and-memory.md` — the MeasurementArchive that
  catches every backfilled `(leader_sp, sample)` measurement.
* `docs/operations/rewind-and-fork.md` — how decision replay drives
  divergence + fork behavior.
* `docs/specs/hard-sample-sorter.md` — the artifact contract carrying
  the heatmap's `sample_order` (δ_s desc) and the descriptive
  `pick_score.per_sample` blended-pick-value snapshot.
* `promptpotter/application/intelligence/adaptive_picker.py` — the
  live online picker (one blended decision-led objective).
