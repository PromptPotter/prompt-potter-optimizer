# Paired-Sample PoBB

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PoBB (Posterior-of-Being-Best, Russo 2016) compares the round's current
candidate against the pool of completed priors and abandons it when its
posterior probability of being the best falls below ε. The original
formulation assumes every arm is observed on an i.i.d. sample of the same
underlying distribution.

PromptPotter's adaptive queue mechanism intentionally violates that: it
reorders each candidate's evaluation so the most diagnostic samples land
first, abandoning a clearly inferior candidate within a handful of queries
instead of burning the full sample budget. That asymmetric ordering breaks
PoBB's iid premise — below is the failure mode it creates, the paired-sample
fix that keeps the sorter's speedup, and the on-disk shape that lets resume
replay paired decisions without re-running them.

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
priors_by_sample: dict[str, dict[str, bool]]    # cid → sample_id → hit
prior_sps:        dict[str, JobSearchPoint]      # cid → the SP that produced those outcomes
```

`register_completed(results, candidate_id, sp)` ingests full
`QueryMeasurement`s (which carry `sample_id` and `hit`), builds the
sample-keyed **hit** map (the θ fit is over binary outcomes; error/deprecated
samples are excluded), and remembers the prior's `JobSearchPoint` so its
measurements can be extended later.

### 2. Reactive per-sample backfill

`PoBBCheck` accepts a `backfill_fn` at construction. The candidate loop
(`score_one_candidate`) builds an async closure and hands it to the
query loop via the `on_sample_pre_check` hook; the query loop fires it
after each sample lands, before degradation checks read prior coverage:

```python
async def _catch_priors_up(sample: Sample) -> None:
    fresh = await elim_check.backfill_for_sample(sample)
    if fresh:
        callbacks.on_pobb_backfill(round_num, idx, n_total, sample.id, fresh)
```

`backfill_for_sample` is idempotent: priors already covering `sample.id`
are skipped, and the method returns the list of priors that actually
gained a measurement (so the telemetry event suppresses itself when
every prior was cached for this sample). Priors get caught up
sample-by-sample as the candidate measures them — paired comparison
always sees up-to-date priors without paying for a full-dataset upfront
wall. Candidates that abort early (PoBB-eliminated mid-run) never pay
for prior coverage on samples they won't reach. The backfill function
itself is a thin closure over `score_search_point` so the new
measurements:

* Hit the per-sample archive cache when those `(prior_sp, sample)`
  pairs already exist (cross-cycle, cross-fork — the MeasurementArchive
  is the DB core).
* Run fresh on the leader's prompt for genuinely new pairs, land in
  `archive/measurements/`, and become reusable for every future round.

### 3. θ comparison in `check()`

`PoBBCheck.check()` reads the candidate's sample IDs straight off
`results`, then for each prior builds a paired **hit** vector by mapping each
candidate sample ID to that prior's stored hit:

```python
paired_priors[cid] = [prior_map[sid] for sid in candidate_samples]
```

Priors that don't cover every sample the candidate has measured are
excluded (this only happens when backfill was skipped or failed —
otherwise every prior is guaranteed to cover the candidate's IDs).
Then `metrics.py::elimination_p_best` runs **one joint 1PL Rasch fit** over the
candidate + every paired prior and returns `p_best = min over priors of
P(θ_cand > θ_prior)` — the closed-form `Φ(Δθ / √(se_c²+se_p²))`, no Monte Carlo.
This is the **same difficulty-adjusted ability θ the round-winner election ranks
by** (`elect_round_winner`): mid-round elimination and end-round election now
judge "better" by one metric, so they can't disagree. The pairing still earns
its keep — backfill guarantees the priors have outcomes on the candidate's
*contested* (hard) samples, which is exactly where the θ comparison gets its
discriminating information.

### 4. Lucky-prefix is self-correcting

Walk through the AIME example with paired-PoBB enabled:

* Round 1: candidate `3b6553…` leader-locks at 8/8 on `{0..7}`. Its
  sample-keyed history is `{"0": 1, "1": 1, ..., "7": 1}`.
* Round 2 starts. Candidate C2.0's sample order is `{9, 12, 13, 6,
  14, 8}` (hard-first).
* As C2.0 measures each of `{9, 12, 13, 6, 14, 8}`, the query loop
  fires `backfill_for_sample(sample)` for that sample. The hook walks
  every prior, finds `3b6553…` missing this sample id, and calls
  `backfill_fn(3b6553…_sp, [sample])`. Sample 6 is cached; the other
  five run fresh (or hit cache from a sibling cycle) and almost
  certainly include several misses — the same prompt family that
  produces 50% on the full set will miss most of these.
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
        "9": false, "12": false, "13": true,
        "6":  true, "14": false, "8":  false
      }
    }
  }
}
```

`candidate_sample_ids` is the ordered list of samples the candidate had
measured at decision time. `prior_histories[cid]` is each prior's **hits**
restricted to exactly those samples (after backfill).

This makes the divergence replayer self-contained:

```python
# resume_and_fork/replayers.py::_pobb_replay_snapshot
candidate_sample_ids = data["candidate_sample_ids"]
prior_histories      = data["prior_histories"]
cur_by_sample        = {r["sample_id"]: r["hit"] for r in rescored_results}
candidate_hits = [cur_by_sample[sid] for sid in candidate_sample_ids]
paired_prior_hits = {
    cid: [hist[sid] for sid in candidate_sample_ids]
    for cid, hist in prior_histories.items()
}
p_best, _ = elimination_p_best(candidate_hits, paired_prior_hits)  # same closed-form θ rule
```

No cross-round "find R1_winner in prior rounds" logic, no backfill
during replay. The decision record is the entire input, and the θ rule is
closed-form + deterministic (`fit_rasch` is pure, no MC seed) so replay is
bit-for-bit when no scorer change moved the candidate's hits. When the active
scorer differs, the candidate side gets rescored (by `resume.py::_rescore`); the
prior side stays at the recorded hits (a scorer change that materially shifts
priors surfaces as divergence via the candidate side).

## Code map

| File | Role |
|---|---|
| `promptpotter/application/optimization/pobb/elimination/checks.py::PoBBCheck` | Sample-keyed priors, `backfill_for_sample`, paired `check()`, `snapshot_priors`, `set_sample_universe` (budget for the dominance gate) |
| `promptpotter/application/intelligence/adaptive_queue_mechanism.py` | Online adaptive queue mechanism: `update_theta_posterior`, `decision_information_gain`, `pick_value`, `next_sample`, `expected_order` |
| `promptpotter/application/optimization/l1/score/loop.py::score_population` | Builds the `backfill_fn` closure + the `_next_sample(scored_outcomes)` closure; injects both into PoBB / the query loop |
| `promptpotter/application/optimization/l1/score/candidate.py::score_one_candidate` | Builds `_backfill_for_sample(sample_id)` closure and passes it as `on_sample_pre_check` — reactive per-sample backfill, no upfront wall |
| `promptpotter/application/scoring/query_loop.py::run_query_loop` | Per-step `next_sample(scored_outcomes)` + fires `on_sample_pre_check(sample.id)` after each sample lands, before degradation checks read prior coverage |
| `promptpotter/application/optimization/l1/population.py::pobb_decision_data` | Embeds `candidate_sample_ids` + `prior_histories` into the decision record |
| `promptpotter/application/scoring/metrics.py::elimination_p_best` | Joint Rasch fit over candidate + paired priors → `p_best = min P(θ_cand > θ_prior)`; the one rule shared by live `check()` and replay |
| `promptpotter/application/optimization/resume_and_fork/replayers.py::_pobb_replay_snapshot` | Re-fits θ from recorded hits via `elimination_p_best`; no cross-round resolver, no MC |

## Sample-selection: online adaptive queue mechanism

Backfill makes the paired comparison statistically valid; the per-candidate
**iteration order** is what makes it cheap. The adaptive queue mechanism
(`promptpotter/application/intelligence/adaptive_queue_mechanism.py`) is a 1PL
Item Response Theory online sequential selector — at each step it folds
the candidate's measured `(δ_s, se_δ_s, hit)` outcomes into a Gaussian
Laplace-approximation posterior on `θ_c`, then picks the next sample by
maximizing a one-step-greedy objective — the **pick-value**,
in nats:

- **decision information gain:** `I(Y_s ; verdict)`,
  the mutual information between the next outcome and the keep/abort
  verdict `θ_c > θ_s` against the seed (`μ_s` the seed prior's fitted
  ability). Picks the sample whose outcome maximally separates candidate
  from seed — directly minimizes expected queries to a confident verdict.
  The means-known limit recovers Bernoulli Chernoff information
  (Garivier-Kaufmann 2016).
`pick_value = decision_information_gain` — a single objective (the earlier
blended `+ explore_weight · model_information_gain` explore term was dropped
2026-05; see [`../specs/verdict-resolution.md`](../specs/verdict-resolution.md)).
PoBB *is* a keep/abort decision and that's what the evaluation budget should
buy down.

The heatmap's hardest-first `sample_order` (the spec's `|δ_s|` sort)
lives on the per-cycle artifact for display; the artifact's
`pick_score.per_sample` is the blended pick-value for a fresh mutation of
the seed — ability prior centred on the seed's ability `θ_seed`, not the
population-mean anchor 0 — a descriptive snapshot of "how informative is
this sample on a brand-new candidate." The **live** adaptive queue
mechanism uses its own per-candidate posterior, so the artifact's order
and the candidate's actual measurement order can diverge — the artifact
is what the operator sees in the webapp, the live order is what the
candidate ran against on disk.

Why this beats a static "hardest first" iteration: hardest-first treats
the queue mechanism as a property of the *dataset* (Rasch δ alone). The
adaptive queue mechanism treats it as a property of the
*candidate-vs-seed comparison*, which is what PoBB actually tests. The
decision term gives zero score to samples where the seed and candidate
are predicted to agree (both unanimous-HIT or both unanimous-MISS)
regardless of how hard they look on the δ axis; samples in the gap
between `μ̂_c` and `μ_s` carry maximum info — and the queue mechanism
re-evaluates that gap after every measurement, so the order is
*responsive* to the candidate's running evidence rather than frozen at
candidate-start.

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

The second gate is the θ-ability posterior — `p_best < ε`, where `p_best =
min over priors of P(θ_cand > θ_prior)` from the joint Rasch fit. The two gates
are complementary: dominance is SPRT's deterministic corner (probability of
catching up = 0); the θ gate is difficulty-adjusted evidence accumulation against
an ε threshold. Dominance fires first because "mathematically impossible" beats
"probably won't."

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
* `git log` — the artifact contract carrying
  the heatmap's `sample_order` (δ_s desc) and the descriptive
  `pick_score.per_sample` blended-pick-value snapshot.
* `promptpotter/application/intelligence/adaptive_queue_mechanism.py` —
  the live online adaptive queue mechanism (one blended decision-led
  objective).
