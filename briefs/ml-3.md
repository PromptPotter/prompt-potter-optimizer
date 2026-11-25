# ml-3: calls in flight past a block's close, on the press (cloud · land/measuring-loop)

`briefs/common.md` binds you, with three overrides:
1. **BASE is `a758e4dee` on `origin/land/measuring-loop`** (slice 1), not on main. Replace the
   ancestor check with `git merge-base --is-ancestor 81b3e6255 a758e4dee`. equals-main runs
   against a758e4dee's offline home.
2. **This is roadmap work, not a ledger section.** Your spec is `docs/specs/roadmap.md`, the bullet
   "One measuring loop inside the round". Read it whole; never edit roadmap.md.
3. **THE CODE SHRINKS is no target here**: hunt deletions, and name every added line's reason.

Push to `arc/ml-3`. `ml-2` runs beside you on the same base (row choice by expected information, in
the race, the sampler and round.py).

## Slice 1 (your base)
- `query_loop.py::BlockRace.choose_rows(taken, ahead)` runs at every block's open
  (`_ScoringPhase.open_block`). It answers each live walk's rows ahead.
- `Walk.reorder` discards a cell sent ahead on a row the choice moved.
- Read a758e4dee's message and its test in `tests/test_spend.py`.

## Your slice
Today a walk launches at most one cell past a pending block close, whatever the press allows
(`_ScoringPhase.fill`: "One cell past a pending close at most"). Under potter's `block_size=1`, the
operator's `LookaheadPress` therefore barely reaches inside a block race.

Make in-flight past a close ride the press: a walk may have up to the press's cells out beyond its
close, on the rows the last open chose. The press stays the one in-flight switch (roadmap: "in
flight: … today the operator's `LookaheadPress`"), so there is no new knob. At press 1, the default
and the offline run's, nothing changes.

- **The invariant stands: a press moves the bill, never the record.** Its tests are
  `test_a_lookahead_window_changes_the_bill_and_never_the_record`,
  `test_a_rounds_walks_each_send_to_their_own_depth_as_many_at_once_as_pressed`, and slice 1's test.
  - A cell out past a close whose row the next open moves is discarded (`Walk.reorder`).
  - A cell out past a close where the race cuts is discarded, as today (`Walk.end`).
- **Re-seat, don't re-buy or replay.** `Walk.reorder` today discards a returned cell whose row the
  walk still owes.
  - Without an archive (no node configs, as in `_round`), that row is paid twice. Slice 1's test
    pins `calls > serial` as its vacuity guard.
  - With an archive, the discarded cell was filed under its claim. When its row is reached it
    REPLAYS (`cached=True`), so a press moves the record's `cached` flag and the round's bought
    count (`fresh_cells`). Reproduce: `_walk(..., blocks=_ReversingRace(), budget=4, cut_at=None)`
    at `armed=4, max_cells=4` against `armed=1`; `rows` differ at index 2 (q7 `cached=True`).
  - Re-seat a returned or running cell, by sample id, at its row's new index wherever the row is
    still ahead. Discard only what the choice dropped.
  - Change the guard to what it should then prove.
- **An index is no longer one row per walk once rows move.** Check each of these against a reorder
  discard, and fix whatever reports wrong:
  - `claiming`;
  - `SampleStartedRecord.sample_idx` and `sample_total`;
  - `Walk.horizon`, which peeks `self.dataset[i]`;
  - `bank()`;
  - the dashboard's discard count (`live_dashboard.py`: started − scored by (arm, position)), where
    a row re-sent after a discard is started twice;
  - the prior catch-ups a cell sent past a close fires (`application/scoring/catch_up.py`,
    `CatchUpPool`) on a row a later open drops: a catch-up is archived only for a cell absorbed
    (`test_a_catch_up_holds_a_cells_slot_and_is_archived_only_for_a_cell_absorbed`).
- **A cut wastes at most the press.** Say so where `launch_horizon`'s docstring now says "a cut
  wastes one cell at most", and make the test check that bound.

## Owns
- `application/scoring/query_loop.py`, `application/scoring/catch_up.py`,
  `application/scoring/search_point_scorer.py::open_walk`;
- `infrastructure/projections/live_dashboard.py` (the discard count only);
- `application/views/readout.py`, if a line it prints goes wrong;
- `tests/test_spend.py`;
- the `docs/developer/` lines on the look-ahead.

**Do not touch** `application/optimizers/`, `application/runner/round.py` or
`application/intelligence/`: `ml-2` owns them.

## Proof
- **equals-main against a758e4dee:** decisions, requests and resume legs unmoved. No key moves
  (`query_loop.py` is in no optimizer package): confirm it.
- **Tests:**
  - Under a block race at press N>1, a walk has cells out past its close.
  - The record (rows, stops, absorbed) equals press 1's, for potter's `block_size=1` and for a block
    race with a reordering hook.
  - No row is paid twice where the choice kept it.
  - The dashboard's discard count equals the ledger's under a reorder.
- **Full gate green.** Report on `out/ml-3-report`.
