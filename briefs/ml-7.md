# ml-7: one decision loop (cloud · measuring loop, the reshape)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
**`origin/arc/ml-6` at `fc9a2e7b5`**: main `da08f4717` (W1) plus the measuring loop's six slices (the
defaults flipped: `rolling`, `per_round`, `informative`, `per_decision`, `vote: overlap`) and
potter-w1. Replace the ancestor check with `git merge-base --is-ancestor da08f4717 fc9a2e7b5`;
equals-main runs against `fc9a2e7b5`'s offline home. Read first:
- `git log --format='%h %s%n%b' da08f4717..fc9a2e7b5`, and `git show origin/out/ml-6-report:report.md`
  (its arc commit's subject swallowed its body: that is the lander's to reword, not yours);
- `git show origin/brief/wave-5:briefs/arch-review.md`, an architecture review of the loop at
  `87cf6680a`. Its R1–R4 are your links. Its findings are CONFIRMED or PLAUSIBLE: re-verify each
  citation at your base before you build on it.

Push to `arc/ml-7` as ONE commit, or one per link where the full gate is green at each.

**The point** (the operator, 2026-11-25): the loop, built for one mechanism, touched 15–17 package
files a slice, because three facts have too many holders:
- the race protocol, spelled in three modules;
- the rows a walk reads, in six holders;
- a walk's budget and depth, mirrored into the race.

Reshape it so the next mechanism of this kind is local. **Behaviour does not move**: this is code
only, no run-data reshape, no knob added or dropped.

## Links, in this order
1. **R1 · One decision loop.** A race answers one call, `decide(view) -> Decision(stops, sends)`.
   - `view` holds each walk's taken rows, untaken rows, calls out and budget. The walk owns budget and
     depth; the race reads them from the view and never mirrors them (`PoBBRace._budgets` and
     `_Walking.depth` go).
   - The phase keeps ONE queue of calls sent, read in the order `read_order` names. `dispatch` is
     the trigger: `batch` decides when the queue is empty, `rolling` on each read.
   - A race with no cut (capo's and gepa's pass-through) is one decision that sends every row.
   - Delete:
     - the turn wheel beside the call queue;
     - `Walk`'s `boundary`, `owed`, `waits`, `enter_block`, its copy of the mode, and its positional
       `reorder` (cells key by sample id);
     - `Untaken`, `Stretch`, `BlockRace.close`, `choose_rows`, `block_size`;
     - `Race.blocks`, which returns `self` in every race.
   - Keep: `top_up`, the walk's cell life (`launch`, `take`, `bank`, `end`), `run_walks` as the one
     door, and the per-row `StopRule` door.
   - The protocol lives in ONE module in `application/scoring/`; `nodes.Race` implements it. The
     scheduler types leave `domain/sample.py`.
   - **Decide, and say why:** the press's calls ahead of a decision (batch-only today, `past = 0 if
     self.rolling`). Either the press sends ahead along the race's order under every trigger, or the
     trigger state carries the horizon as a value. Never a branch on the mode in the scheduler. The
     proof below binds the choice.
   - **Re-test:** `git grep -c rolling -- promptpotter/application/scoring/query_loop.py` falls from
     its base count (24 at `87cf6680a`) to the trigger's own lines; `def blocks` gone from every race.
2. **R3 · The row policy leaves PoBB.** `informative`, `_hand_out` and `_settles` become one function
   in `application/scoring/`. It takes the view, an optional `FieldRead` and the knobs, and returns
   `sends`. The knobs stay on the eliminator node's config (`docs/architecture.md` §0), as a group
   any eliminator includes; their paths do not move, so no key moves by config.
3. **R4 · The read order rides the phase.** `run_walks(reads=)` goes; the phase asks the store for
   the order it replays (`ledger_scan.py::StandingRounds.reads`).
4. **R2 · A walk's order is keyed by arm and stated by the phase.**
   - The phase's turn callback hands each walk's order (taken, then untaken). `runner/round.py` stops
     reading `walk.dataset`, and its `moved`/`filed` re-file rule goes.
   - `live_dashboard.py`'s declared order is keyed by arm, not one list per round with the last arm
     winning; the served walk axis reads the arm's own order.
   - Reconcile with ml-6's link 4 (the axis from the cells taken; it deleted `declared_sample_order`)
     on your base: ONE derivation stays.
   - This closes ml-6's filed row **The served walk is the last-listed arm's, not `measuring_arm`'s**
     (`served_dashboard.py::_walk` follows `rows[-1]`; 11 of 18 walk keys served nothing in its
     replay). Re-test: that replay's empty walk keys, 11 → 0.
   - `types.generated.ts` regenerates.

## Owns
- `application/scoring/` (`query_loop.py`, `catch_up.py`, `search_point_scorer.py`, the new protocol
  module), `application/optimizers/nodes.py`, `application/optimizers/potter/` (`race.py`, `pobb.py`,
  `rasch.py`, `knobs.py`'s pobb block).
- `capo/members.py`'s and `gepa/members.py`'s race classes only.
- `runner/round.py`, `domain/sample.py`'s scheduler types (moving out),
  `infrastructure/store/campaign_store/ledger_scan.py`, `infrastructure/projections/live_dashboard.py`,
  `application/served_dashboard.py`'s walk builder.
- The webapp's walk reader, only if its wire moves.
- Their tests, the docs they make stale (`docs/methods/candidate-elimination.md`, `node-standard.md`,
  layer `CLAUDE.md`s), and the generated files (by their scripts).

**l4-key-b** edits `domain/sample.py` too, outside the scheduler types: touch only what you move out.
Its arc lands beside yours, so expect a one-file merge.

## Proof
- **Equals-main against `fc9a2e7b5`'s home: EVERY leg UNMOVED**, decisions and requests, capo, capo-pobb,
  gepa, levi and potter (straight, resumed, wounded, healed, crossed ×3, l4).
- Run it twice, at the press's 1 and its max. Under `launch`, the press moves no record.
- Digests: only package-code keys move; name them.
- Full gate green.
- surface-ledger: this chain should SHRINK `promptpotter/`. Report the net per link and hunt
  deletions until it does.

Report on `out/ml-7-report`:
- per link the commit and +added/−deleted;
- re-tests before and after;
- the press decision and its reason;
- package files a future mechanism of slice 5's kind would touch, then (5's real list) and now (your
  estimate, named);
- Rows closed, Rows to file, Symptoms.
