# Paired-Sample PoBB

> **Audience:** Developer reference. Operators see [`../manual/`](../manual/) for usage docs.

PoBB (Posterior-of-Being-Best, Russo 2016) compares the round's current
candidate against the pool of completed priors and abandons it when its
posterior probability of being the best falls below ε. The original
formulation assumes every arm is observed on an i.i.d. sample of the same
underlying distribution.

PromptPotter's shared round order intentionally violates that: it front-loads
the decision-relevant samples (the seed's misses — the only place a candidate
can win) so a dead candidate is abandoned within a handful of queries instead
of burning the full sample budget. That non-iid ordering breaks PoBB's
premise — below is the failure mode it creates, the paired-sample fix that
keeps the speedup, and the on-disk shape that lets resume replay paired
decisions without re-running them.

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

Re-run the pathology above with pairing on: as round 2's candidate
measures its hard-first order, backfill forces the locked leader onto
those same samples — its history gains real hard-sample coverage
(mostly misses; the same prompt family scores 50% on the full set),
and its recorded mean deflates from the false 100% toward its true
rate. The candidate may still lose, but the comparison is honest.
No separate "lower the lock-in threshold" code path, no display patch
for "100% on n=8": the inflation was a mechanical consequence of
unpaired comparison, and paired comparison mechanically removes it.

## Cost

Backfill runs per candidate over its sample order (~6–10 hard samples).
The round's first candidate pays a few fresh leader measurements; later
candidates' orders overlap heavily (cache hits), and by round 3+ the
leader has near-full coverage. Net: roughly one extra
candidate-equivalent of LLM spend per round — the cost of statistical
validity.

## On-disk shape and replay

Each `ELIMINATION_CUT` / `LEADER_LOCK_IN` decision record (in
`campaigns/{campaign_id}/cycles/{cycle_id}/rounds/round_NNNN.json`) now carries the paired
snapshot under `data`:

```json
{
  "kind": "elimination_cut",
  "outcome": true,
  "data": {
    "p_best": 0.0,
    "leader_id": "R1_winner",
    "candidate_sample_ids": ["9", "12", "13", "6", "14", "8"],
    "prior_histories": {
      "R1_winner": { "9": false, "12": false, "13": true,
                     "6": true, "14": false, "8": false }
    }
  }
}
```

(`inputs_ref` additionally records the gate parameters in force at
decision time — the live knob values are `optimization.pobb_epsilon` /
`elimination_n_min` in `CampaignConfig`, not this doc.)

`candidate_sample_ids` is the ordered list of samples the candidate had
measured at decision time. `prior_histories[cid]` is each prior's **hits**
restricted to exactly those samples (after backfill).

This makes the divergence replayer self-contained
(`resume_and_fork/replayers.py::_pobb_replay_snapshot`): it rebuilds the
candidate vector from the rescored results keyed by
`candidate_sample_ids`, pairs each prior via `prior_histories`, and
calls the same `elimination_p_best` on the cycle's fixed δ ruler.

**Graded, never binarized.** The inputs are the per-sample **graded** fitness ∈ [0,1]
(`intelligence/exploration.py::graded_response`), not a `hit` boolean. The logistic MAP
maximizes cross-entropy, which is valid for any `y ∈ [0,1]`, so a binary dataset is
bit-identical to the old hit path — while a graded backend (TermNorm's reciprocal rank, the
L4 outer proxy) keeps its gradient instead of collapsing to an all-miss θ where `p_best`
pins at 0.5 and PoBB never discriminates. Recorded booleans from pre-graded decisions coerce
to 0.0/1.0, the identical values the live path fed.

No cross-round "find R1_winner in prior rounds" logic, no backfill
during replay. The decision record is the entire input, and the θ rule is
closed-form + deterministic (the θ fit is pure, no MC seed) so replay is
bit-for-bit when no scorer change moved the candidate's hits. When the active
scorer differs, the candidate side gets rescored (by `resume.py::_rescore`); the
prior side stays at the recorded hits (a scorer change that materially shifts
priors surfaces as divergence via the candidate side).

Entry points: `optimization/pobb/elimination/checks.py::PoBBCheck` (the
mechanism), `scoring/metrics.py::elimination_p_best` (the one θ rule
shared by live `check()` and replay); the closures that wire them are
named inline above.

## Sample-selection: the shared round order

Backfill makes the paired comparison statistically valid; the shared
**iteration order** is what makes it cheap — win-opportunity samples first,
so decision evidence arrives before the budget is spent. Split out to its
own page: [`adaptive-queue-mechanism.md`](adaptive-queue-mechanism.md).

## Elimination ladder: margin before posterior

`PoBBCheck.check()` runs two gates in order. The first is the
**paired-margin gate** — integer pairing arithmetic on the shared universe:

```
wins   = candidate HIT where the seed missed
losses = candidate MISS where the seed hit
need   = ⌈improvement_threshold · budget⌉ − (wins − losses)
kill when binom_sf(unattempted win opportunities, need, p_w) < ε
```

`p_w` is the Laplace-smoothed win rate on the measured seed-MISS stratum
alone — ties carry nothing, so an easy prefix can't inflate the estimate,
and the statistic is order-agnostic (a pure function of the outcome
multiset + the seed's map). When `need` exceeds the remaining win
opportunities, `binom_sf` is exactly 0 — the deterministic can't-catch-up
corner (the old separate dominance gate) rides the same formula. Seed is
the origin (R1) or the prior round's winner (R2+); coverage of the
candidate's measured samples is guaranteed by the backfill above, and
still-unclassified universe samples count as win opportunities (the gate
under-kills, never over-kills, while backfill catches up).

The second gate is the θ-ability posterior — `p_best < ε`, where `p_best =
min over priors of P(θ_cand > θ_prior)` on the cycle's fixed δ ruler. The two
gates are complementary: the margin gate asks "can it still be ADOPTED"
(exact pairing arithmetic, ε-futility, deterministic at the corner); the θ
gate asks "is it credibly the BEST" (difficulty-adjusted evidence
accumulation). Margin fires first because it is integer-exact and needs no
θ fit.

## Related concepts

* `docs/concepts/the-loop.md` — where PoBB fits in the round lifecycle.
* `docs/concepts/scoring-and-memory.md` — the MeasurementArchive that
  catches every backfilled `(leader_sp, sample)` measurement.
* `docs/operations/rewind-and-fork.md` — how decision replay drives
  divergence + fork behavior.
* `git log` — the artifact contract carrying
  the heatmap's `sample_order` (δ_s desc) and the descriptive
  `pick_score.per_sample` contestedness snapshot.
* [`adaptive-queue-mechanism.md`](adaptive-queue-mechanism.md) — the shared
  round order that sets every candidate's iteration order.
