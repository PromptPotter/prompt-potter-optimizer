# The mask — how far a change reaches before you have to fork

You changed something after the measurements were paid for: a scoring formula, a PoBB
setting, the engine itself. Two questions follow, and only the second costs money.

1. Would anything have gone differently?
2. **How much of what you already paid for still carries over — and where must you branch
   instead of continuing?**

The mask answers both against the record on disk, with no re-runs and no LLM calls. It is
what lets you change your mind about the criterion without throwing away the campaign.

**Mask and lens are not two words for one thing.** The *mask* is the alternative criterion
projected over the record. The *lens* is the query parameter that picks one — `?lens=…` on
the tree endpoint. One concept, one selector.

## The vocabulary you will see

The **record** is the realized lineage: a campaign is a *tree*, not a line, and what it
actually ran is the record. A mask applies a change and lets it propagate, splitting the
record in two:

- **Invariant** — holds regardless. Every measured `(candidate, sample)` result, each
  node's own value, any subtree off the diverging path. Yours to keep building on.
- **Divergent** — contingent on a choice the change would have made differently: the
  lineage *structure* downstream. A divergent node's own value is still real; only its
  *position* is counterfactual. The tree renders it dimmed.

The **divergence point** is the boundary, and it is *emergent* — it appears wherever the
change first stops carrying over, not at a site anyone declared. Because the lineage is a
tree, this is per-branch: a divergence claims its descendant subtree, while a fork rooted
*before* it stays invariant and is analysed for its own divergence. One mask can therefore
yield several divergence points, and "how big a change can I make" has a per-branch answer.

Nothing is stored. One shared fold, `find_divergences(record, verdict)`, walks the record
asking a **verdict** — "would this have gone differently here?" — and the first flip on a
branch is a divergence. Only the verdict varies; the fold never does. It deliberately does
**not** build a second tree: past the first divergence nothing was measured, so any deeper
tail would be fiction.

## Asking it

Three verdicts ship, and each answers a different "what if".

| Ask | How | Names an alternative? |
|---|---|---|
| **A different scoring formula** | `?lens=score:<formula>` on `GET /campaigns/{c}/cycles/{cy}/tree` — each node also gets a `lens_value` | Yes — the candidate that formula ranks first |
| **A PoBB gate switched off** | `?lens=abort:<variant>`, variant = `<gate>_off` for any `EliminationGate` (`epsilon` \| `lock_in` \| `collapsed`), or `all_off` — the table is DERIVED from that enum, so a new gate is switchable without editing this row | No — the continuation was never measured |
| **A changed engine or scorer** | `python -m promptpotter ab` — replays the active cycle's whole campaign | Where a `round_winner` decision flips, yes |

**Row one re-ranks the RECORD; row three re-runs the ELECTION, and the difference is not a
matter of degree.** The lens orders candidates by `display_rank_key` over the masked aggregate,
so its divergence means *under this formula the crowned candidate is no longer the best-scoring
one*. The election ranks Rasch θ-lift over the parent behind a coverage floor, and θ under
another formula has to be re-fit from per-sample grades against a re-calibrated δ ruler — which
is what `ab` does and what a polled tree read cannot. Ask the lens which rounds are worth
replaying; ask `ab` whether the run would have moved.

`?samples=<id,id,…>` composes with a `score:` lens: re-score over just those samples. No
lens and no samples is the raw read.

### The second consumer: a mask as a compare CHANNEL

The tree answers *where does the record first disagree*. `GET /evidence` answers *and then
what*, by carrying the same two segments on a subject address —
`subject=course:<campaign>/<cycle>;lens=score:<formula>;samples=3,7,11` — so a branch and the
same branch under another criterion pool as two channels of one read instead of two page loads.
The mask is part of the subject KEY, which is what keeps them apart.

Two folds, one round-level ranking. `verdicts.py::masked_election` decides one round against a
stated parent floor; `find_divergences` asks it against the RECORDED parent one round at a time,
while `scenario.py::scenario_spine` walks the chain — each round judged against the winner the
scenario is standing on — and **ends on the round the two part**. Past that the run would have
stood on a parent it never had, so no measurement describes it; that round is also where a fork
applying the criterion is minted, which `resume_and_fork/resume.py` branches at on a scoring
divergence, carrying the rounds before it. Every step is an arm the run measured; the fold picks
among them and invents none.

What the evidence read serves back per masked channel — the round the two part, who each reading
stood on there, invariant rounds, samples scored, and the caveat as a served sentence — is
`ScenarioReading` (`application/evidence.py`). The caveat is the same boundary this section
already draws: θ is not re-fitted and no election is replayed, so the round it names is where the
two readings part rather than a verdict the campaign reached.

### Applying one — the preview IS the fork point

A `score:` mask is the one setting an operator can change mid-campaign and see the cost of first,
and the round it parts at is the round the fork is cut at. `fork-cycle` carries `keep_rounds`,
which swaps the offshoot trigger for `OPERATOR_REWIND` — rounds `0..N-1` lifted, the branch
continuing at N — and `ConfigOverrides.scoring` carries the criterion into the fork's effective
config. Preview and action are one fact rather than two surfaces that have to agree, which is why
the apply affordance sits beside the mask editor rather than in a fork dialog of its own.

**Two settings preview; everything else forks blind, and the difference is the point.** A criterion
and a sample subset are re-projections of rows already measured. A node parameter, a model or a
prompt field is not: nothing ever ran at the edited value, so no measurement on that searchpoint
carries over and the honest render is unknown rather than a recomputed anything. Budget and sample
look-ahead are the only two settings that move a RUNNING cycle in place. A surface offering the
change is obliged to say which of the three it is.

`ab` is the one that answers question 2 out loud. It reports where the change departs the
record, how many rounds that leaves counterfactual, and the decisions that re-derived
differently up to those points — then stops, because replaying past a departure describes a
history that would not have happened. The lens takes the same stopping rule; what separates them
is how exactly each locates the departure, not how far past it either will speak. Zero divergences means the whole forest survives the
change and no fork is needed. It reads through the store's typed round loader, so a round
file the current models cannot parse stops it loudly; a `score:` or `abort:` lens reads the
summary fields instead and still serves such a cycle.

## What it will not claim

- **A record is campaign-scoped; the tree is not.** One record is loaded per campaign the
  tree spans. An inner (L4) run is its own campaign in its own `.inner/` sandbox, and a
  cycle_id is content-addressed on the origin, so it repeats across them. Drill into an
  inner cycle and every bar answering `null` is **a read nobody made**, not the lens
  declining to speak.
- **The computable boundary on abort.** *Suppressing* a contributor that did fire is
  computable from the record. *Adding* one the run lacked is not — lock-in keys off the
  leader's per-step `p_best` trajectory, which the per-candidate snapshot does not carry.
  That question is answered by running a real sibling cycle on the policy-scope fork path.
- **No backfill, structurally.** Row-derivable evaluators (`accuracy`, `error_rate`, …) and the
  per-cell channel means (`latency`, `cost`, `tokens`) are recomputed from persisted per-sample
  rows at read time, so they are never "missing" from an old record; snapshot-only evaluators
  resolve to honest absence. No tool rewrites a stored round file.
- **A node lacking the masked input is *unknown*, never divergent.** Absence of data is not
  evidence of departure.

## Design decisions (the non-derivable rationale)

1. **No persisted mask, and the write side did not create one.** The reuse is the fold plus the
   verdicts; the criterion rides the request and is computed-then-discarded. Fork-from-divergence
   was expected to be its first real reader and turned out not to be: an applied criterion rides
   `CycleSeed.config_overrides`, which the fork already writes, so mask identity would be a second
   home for a value the seed holds.
2. **A mask is a verdict over the record, not a data-kind.** The value face is
   scoring-specific and stays out of the shared fold; a verdict reads whatever features it
   needs and the fold is indifferent. This is why a verdict lives beside the math it asks
   rather than in one registry: scoring and abort in `application/mask/verdicts.py`, replay
   beside the replayers it wraps.
3. **One-step counterfactual.** At a divergence, name the alternative option — it was
   measured, so it is real. Its descendants never existed; claim nothing deeper until a
   real fork runs.
4. **A mask is a testable hypothesis.** Anticipate a fork under the mask → measure the real
   branch → attribute the outcome back. That closure *is* MCTS backpropagation, and it
   landed exactly there: `backprop.py` is a second fold over the same record.
5. **One active mask** at a time; overlays are additive later.
6. **Say "divergence point", never "decision point" or "fork point".** "Fork" is taken by
   the real branched-cycle `ForkSpec`, and the deferred write-side is literally
   fork-from-divergence.
7. **No commitment to "one day every criterion".** Every shipped verdict is a per-round
   predicate, which is what the fold is proven for — not open-ended generality. Extract the
   next one when a real consumer lands.

## Invariants

- **Fed the realizing criterion, the fold reproduces the record.** Each entity is re-scored
  on its *own* stored evaluator namespace, and the recorded eligibility filter is reused
  verbatim — so a higher-scoring but ineligible candidate is still not named leader. This is
  the self-consistency gate, and it holds by construction rather than by test.
- **Non-stationary realized criterion.** A `per_cell` formula changes by FORK, never mid-cycle
  (`persistence-and-state.md` § Changing the composite formula), so a mask spanning forks diffs
  against the formula in effect at each round.
- **A mask is the PROJECTION of the composite, not the composite.** The campaign scores per CELL;
  a mask re-scores a stored per-ROUND evaluator map, because the record it reads may no longer
  have the rows. The two agree exactly where the formula is linear in its terms and diverge by
  Jensen where it is not — a clamp or a ratio is enough. Read a masked value as *what this round
  would have scored on these round-level terms*, never as the number the election used.
- **Record unchanged.** A mask is a projection on top. The realized lineage and its winners
  never move.
- **One scoring home.** No mask math in TypeScript; `score_search_point()` stays the single
  gateway; the fold is a read-time `application/` service, never an infrastructure
  ledger-projection; no mask state is persisted.
- **Selection unchanged.** Divergent nodes stay clickable — dimming is opacity and a label,
  never a disabled interaction.

Code is the source of truth: `promptpotter/application/mask/`. Composite-fitness chain and
scoring authority: [`../architecture.md`](../architecture.md) §0.5. What of the write side is
still open — the SAMPLE-SET half, which needs a replayable measurement order the fork seed does
not carry — is [`../specs/roadmap.md`](../specs/roadmap.md) § Lineage mask.
