# The mask — how far a change reaches before you have to fork

You changed something after the measurements were paid for — a scoring formula, a PoBB setting, the engine. The mask answers, against the record on disk with no LLM call, (1) would anything have gone differently, and (2) **how much of what you paid for still carries over, and where must you branch instead?**

**Mask and lens are not two words for one thing.** The *mask* is the alternative criterion projected over the record; the *lens* is the query parameter that picks one (`?lens=…`).

## The vocabulary

The **record** is the realized lineage — a tree, not a line. A mask splits it:

- **Invariant** — holds regardless: every measured `(candidate, sample)` result, each node's own value, any subtree off the diverging path.
- **Divergent** — the lineage *structure* downstream of a choice the change would have made differently. The node's value is real; only its *position* is counterfactual, rendered dimmed.

**Say "divergence point", never "decision point" or "fork point"** ("fork" is `ForkSpec`'s). It is emergent and per-branch: one mask can yield several. Nothing is stored: one fold, `find_divergences(record, verdict)`, asks a verdict at each step and the first flip on a branch is a divergence. It builds no second tree — past the first divergence nothing was measured.

## Asking it

| Ask | How | Names an alternative? |
|---|---|---|
| **A different scoring formula** | `?lens=score:<formula>` on `GET /hierarchy/{address}` (a `per_cell` composite; each candidate gets a `lens_value`). `?lens=dials:<term=weight,…>` realizes weights on the campaign's own anchors and serves the formula as `lens_criterion` | Yes — the candidate that formula ranks first |
| **A PoBB gate switched off** | `?lens=abort:<gate>_off` for any `EliminationGate` member, or `all_off` — derived from the enum | No — the continuation was never measured |
| **A changed engine or scorer** | `python -m promptpotter ab [--campaign <id>]` | Where a `round_winner` decision flips, yes |

**A lens is scored the way the run is.** `score:F` is the cycle's own scoring block with `per_cell = F` (`mask/load.py::lens_overrides`, the `ConfigOverrides` a fork applying it carries), run over each arm's rows (`Scorer.sheet`, `fold_cells`) — so a `lens_value` is what a fresh run under `F` reports, never `F` over a round's means.

**The lens re-ranks the RECORD; `ab` re-runs the ELECTION.** The lens orders by `scoreboard_rank_key`; the election ranks θ-lift over the parent behind a coverage floor, which under another formula needs a re-fit against a re-calibrated ruler — `ab`'s job. Ask the lens which rounds are worth replaying; ask `ab` whether the run would have moved. Both stop at the first departure.

`?samples=<id,…>` composes with a `score:` lens and re-scores **both sides** — the arms and the parent (`RoundResult.reference_rows`) — or the bar stays put while every challenger moves. A round with no banked parent panel is undecidable on a subset.

**The second consumer: a compare channel.** `GET /evidence` carries the same segments on a subject (`subject=course:<c>/<cy>;lens=score:<F>;samples=3,7`), the mask part of the subject KEY. `application/mask/divergence.py::masked_election` decides one round; `scenario_spine` walks the chain and **ends on the round the two part** — the round a fork applying the criterion is minted at. It serves `application/evidence/subjects.py::ScenarioReading`; θ is not re-fitted and no election is replayed, so the parting round is a boundary, not a verdict.

### Applying one — the preview IS the fork point

The round a `score:` mask parts at is the round the fork is cut at: `fork-cycle` with `keep_rounds` mints an `OPERATOR_REWIND` (rounds `0..N-1` lifted) and `ConfigOverrides.scoring` carries `{per_cell: F}` over the parent's block. Preview and action are one fact, so the apply affordance sits beside the mask editor.

**Two settings preview; everything else forks blind.** A criterion and a sample subset re-project measured rows; a node parameter, a model or a prompt field has no measurement at the edited value, so the honest render is unknown. Budget and look-ahead alone move a RUNNING cycle in place. A surface offering a change says which of the three it is. **A knob's `Scope` (`application/campaign_config.py`)** decides resume: `POLICY` resumes in place; `DATA` and `IDENTITY` run divergence detection.

`ab` reads through the typed round loader, so an unparseable round file stops it loudly; a `score:` or `abort:` lens reads summary fields and still serves such a cycle.

## What it will not claim

- **A record is campaign-scoped; the tree is not.** An L4 inner run is its own campaign in its own `.inner/` sandbox; a `null` bar there is a read nobody made, not the lens declining.
- **Abort is computable only one way.** Suppressing a contributor that fired is computable; adding one the run lacked is not (lock-in keys off a `p_best` trajectory the snapshot does not carry) — that needs a real sibling cycle.
- **No backfill.** Masked readings are graded from persisted rows at read time; a term a row lacks leaves that row unscored. No tool rewrites a stored round file.
- **A node lacking the masked input is *unknown*, never divergent.**

## Design decisions

1. **No persisted mask.** The criterion rides the request; an applied one rides `CycleSeed.config_overrides`, which the fork already writes, so mask identity would be a second home.
2. **A mask is a verdict over the record, not a data-kind** — each verdict lives beside the math it asks (scoring and abort in `application/mask/divergence.py`, replay beside its replayers), not in a registry.
3. **One-step counterfactual** — name the measured alternative at a divergence; claim nothing deeper until a real fork runs. `backprop.py` is a second fold over the same record.
4. **One active mask** at a time.
5. **No commitment to "every criterion"** — shipped verdicts are per-round predicates; extract the next when a real consumer lands.

## Invariants

- **Fed the realizing criterion, the fold reproduces the record** — each entity re-graded from its own rows under its cycle's own scorer, the recorded eligibility filter reused verbatim.
- **A formula changes by fork, never mid-cycle** ([`persistence-and-state.md`](persistence-and-state.md) § Changing the composite formula), so a lens replaces the composite in each cycle's OWN block; a parent carried across a fork edge is re-graded under the child's scorer.
- **A mask IS the composite read per cell** — a formula over means agrees only where linear, and the shipped length charge is not.
- **Record unchanged; one scoring home** — no mask math in TypeScript, the fold a read-time `application/` service, no mask state persisted; formula evaluations only, no LLM call.
- **Selection unchanged** — divergent nodes stay clickable; dimming is opacity and a label.

Code: `promptpotter/application/mask/`. Composite-fitness chain: [`../architecture.md`](../architecture.md) §0.5.
