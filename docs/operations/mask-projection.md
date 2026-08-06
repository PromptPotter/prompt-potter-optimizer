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
| **A different scoring formula** | `?lens=score:<formula>` on `GET /campaigns/{c}/cycles/{cy}/tree` — each node also gets a `lens_value` | Yes — the candidate that formula would have elected |
| **A PoBB contributor switched off** | `?lens=abort:<variant>`, variant ∈ `epsilon_off` \| `lock_in_off` \| `all_off` | No — the continuation was never measured |
| **A changed engine or scorer** | `python -m promptpotter ab` — replays the active cycle's whole campaign | Where a `round_winner` decision flips, yes |

`?samples=<id,id,…>` composes with a `score:` lens: re-score over just those samples. No
lens and no samples is the raw read.

`ab` is the one that answers question 2 out loud. It reports where the change departs the
record, how many rounds that leaves counterfactual, and the decisions that re-derived
differently up to those points — then stops, because replaying past a departure describes a
history that would not have happened. Zero divergences means the whole forest survives the
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
- **No backfill, structurally.** Row-derivable evaluators (`accuracy`, `latency_norm`, …)
  are recomputed from persisted per-sample rows at read time, so they are never "missing"
  from an old record; snapshot-only evaluators resolve to honest absence. No tool rewrites
  a stored round file.
- **A node lacking the masked input is *unknown*, never divergent.** Absence of data is not
  evidence of departure.

## Design decisions (the non-derivable rationale)

1. **No persisted mask until a reader exists.** The reuse is the fold plus the verdicts; the
   criterion rides the request and is computed-then-discarded. Persisted, addressable mask
   identity lands with the fork-from-divergence write-side — its first real reader.
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
- **Non-stationary realized criterion.** `per_round` formulas hot-swap mid-campaign; diff
  against the formula in effect at each round.
- **Record unchanged.** A mask is a projection on top. The realized lineage and its winners
  never move.
- **One scoring home.** No mask math in TypeScript; `score_search_point()` stays the single
  gateway; the fold is a read-time `application/` service, never an infrastructure
  ledger-projection; no mask state is persisted.
- **Selection unchanged.** Divergent nodes stay clickable — dimming is opacity and a label,
  never a disabled interaction.

Code is the source of truth: `promptpotter/application/mask/`. Composite-fitness chain and
scoring authority: [`../architecture.md`](../architecture.md) §0.5. The deferred write-side —
minting a fork *at* a divergence point, carrying the mask as its new criterion-of-record — is
[`../specs/roadmap.md`](../specs/roadmap.md) § Lineage mask.
