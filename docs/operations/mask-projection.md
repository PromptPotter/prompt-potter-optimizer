# The mask — how far a change reaches before you have to fork

You changed a scoring formula, a PoBB setting or the engine after the measurements were paid for. The mask answers, against the record on disk with no LLM call: would anything have gone differently, and how much of what you paid for carries over? Code: `promptpotter/application/mask/`; the composite-fitness chain is [`../architecture.md`](../architecture.md) §0.5. The *mask* is the alternative criterion projected over the record; the *lens* is the query parameter that picks one (`?lens=…`).

## The vocabulary

The **record** is the realized lineage — a tree. A mask splits it: **invariant** — every measured `(candidate, sample)` result, each node's own value, any subtree off the diverging path; **divergent** — the lineage *structure* downstream of a choice the change would have made differently; the node's value is real, only its *position* is counterfactual, rendered dimmed. Say "divergence point", never "decision point" or "fork point" ("fork" is `ForkSpec`'s). One fold, `find_divergences(record, verdict)`, asks a verdict at each step; the first flip on a branch is a divergence, past which nothing was measured. Nothing is persisted: the criterion rides the request, an applied one `CycleSeed.config_overrides`.

## Asking it

| Ask | How | Names an alternative? |
|---|---|---|
| **A different scoring formula** | `?lens=score:<formula>` on `GET /hierarchy/{address}` (a `per_cell` composite; each candidate gets a `lens_value`). `?lens=dials:<term=weight,…>` realizes weights on the campaign's own anchors and serves the formula as `lens_criterion` | Yes — the candidate that formula ranks first |
| **A PoBB gate switched off** | `?lens=abort:<gate>_off` for any `EliminationGate` member, or `all_off` | No — the continuation was never measured |
| **A changed engine or scorer** | `python -m promptpotter ab [--campaign <id>]` | Where a `round_winner` flips, yes |

**A lens is scored the way the run is.** `score:F` is the cycle's own scoring block with `per_cell = F` (`mask/load.py::lens_overrides`, the `ConfigOverrides` a fork applying it carries), run over each arm's rows (`Scorer.sheet`, `fold_cells`) — a `lens_value` is what a fresh run under `F` reports, never `F` over a round's means ([`persistence-and-state.md`](persistence-and-state.md) § Changing the composite formula); a parent carried across a fork edge is re-graded under the child's scorer.

**The lens re-ranks the RECORD; `ab` re-runs the ELECTION.** The lens orders by `scoreboard_rank_key`; the election ranks θ-lift over the parent, which under another formula needs a re-fit against a re-calibrated ruler — `ab`'s job. Both stop at the first departure. `?samples=<id,…>` composes with a `score:` lens and re-scores the arms and the parent (`RoundResult.reference_rows`) alike.

**The compare channel.** `GET /evidence` carries the same segments on a subject (`subject=course:<c>/<cy>;lens=score:<F>;samples=3,7`), the mask part of the subject KEY. `application/mask/divergence.py::masked_election` decides one round; `scenario_spine` walks the chain and ends on the round the two part (`application/evidence/subjects.py::ScenarioReading`); θ is not re-fitted, so the parting round is a boundary, not a verdict.

### Applying one — the preview IS the fork point

The round a `score:` mask parts at is the round the fork is cut at: `fork-cycle` with `keep_rounds` mints an `OPERATOR_REWIND` (rounds `0..N-1` lifted) and `ConfigOverrides.scoring` carries `{per_cell: F}` over the parent's block. **Two settings preview; everything else forks blind**: a criterion and a sample subset re-project measured rows; a node parameter, a model or a prompt field has no measurement at the edited value, so the honest render is unknown. Budget and look-ahead alone move a RUNNING cycle in place.

## What it will not claim

- **A record is campaign-scoped; the tree is not.** An L4 inner run is its own campaign in its own `.inner/` sandbox; a `null` bar there is a read nobody made.
- **Abort is computable only one way.** Suppressing a contributor that fired is computable; adding one the run lacked is not (lock-in keys off a `p_best` trajectory the snapshot does not carry) — that needs a real sibling cycle.
- **No backfill.** Masked readings are graded from persisted rows at read time; a term a row lacks leaves that row unscored; a node lacking the masked input is *unknown*, never divergent.
- **One-step counterfactual, one active mask** — name the measured alternative at a divergence; claim nothing deeper until a real fork runs. `backprop.py` is a second fold over the same record.

## Invariants

- **Fed the realizing criterion, the fold reproduces the record** — each entity re-graded from its own rows under its cycle's own scorer, the recorded eligibility filter reused verbatim.
- **A mask IS the composite read per cell** — a formula over means agrees only where linear, and the shipped length charge is not.
- **One scoring home** — no mask math in TypeScript, the fold a read-time `application/` service; each verdict lives beside the math it asks (`application/mask/divergence.py`). Divergent nodes stay clickable; dimming is opacity and a label.
