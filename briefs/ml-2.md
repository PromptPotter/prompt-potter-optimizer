# ml-2: rows chosen by expected information, θ refit per decision (cloud · land/measuring-loop)

`briefs/common.md` binds you, with three overrides:
1. **BASE is `a758e4dee` on `origin/land/measuring-loop`** (slice 1), not on main. Replace the
   ancestor check with `git merge-base --is-ancestor 81b3e6255 a758e4dee`. equals-main runs
   against a758e4dee's offline home.
2. **This is roadmap work, not a ledger section.** Your spec is `docs/specs/roadmap.md`, the bullet
   "One measuring loop inside the round". Read it whole; never edit roadmap.md.
3. **THE CODE SHRINKS is no target here**: a switch is the slice's substance. Still hunt deletions,
   and name every added line's reason.

Push to `arc/ml-2`. `ml-3` runs beside you on the same base (calls in flight past a block's close,
in `query_loop.py`).

## Slice 1 (your base)
- `query_loop.py::BlockRace.choose_rows(taken, ahead)` runs at every block's open
  (`_ScoringPhase.open_block`). It answers each live walk's rows ahead: as many as its budget left,
  none taken (checked by slot), from any pool the race reads.
- The hook answers the order each live walk takes its untaken pool in; the walk takes the first
  rows its budget leaves.
- **A walk opens on every row it may take**, and takes `budget` of them
  (`search_point_scorer.py::open_walk(budget=)`, `Walk.budget`), so its replay, claim and pricing
  state covers a row the race takes past the opening order. The slice-1 test
  `test_a_row_a_race_takes_past_the_opening_budget_is_priced_and_bought_once_on_resume` proves it.
  Your pool reaches the walk through that parameter: `round.py::_open_arm` opens each walk on the
  sampler's pool, with the draw's size as its budget.
- Today's value, `nodes.Race.choose_rows`, passes the sampler's order through.
- `Walk.reorder` discards a cell sent ahead on a row the choice moved.
- Read a758e4dee's message and
  `tests/test_spend.py::test_a_race_orders_each_blocks_rows_and_a_press_moves_the_bill_never_the_record`.

## Your slice: two switches, today's values unmoved
- **Row choice.** `random` is today's: the sampler's draw (`AdaptiveQueue.draw`, stratified,
  crowning cells dropped) in `build_round_order`'s order. `informative`: at every block's open, each
  walk's next rows are those of most expected information (`rasch.py::expected_information`) at the
  current θ, read off the ruler, never off the parent's own grade on the row, from the sampler's pool.
- **Refit**, the θ that rows are chosen on. `per_round` is the ruler seated at the round's start.
  `per_decision` is PoBB's refit at every block (`PoBBCheck` already refits θ and δ at each check).
- **The coupling.** `informative` reverses the round order's measured decision
  (`docs/methods/verdict-resolution.md`), so it runs under `theta_election.vote: overlap` only. Any
  other pairing is a refused coupling (the members' `MemberCoupling` tables), never a silent
  fallback. Update the section of verdict-resolution.md that records the decision.

## Decided for you
- **The round order stays the sampler's.** capo-pobb runs potter's `pobb` on capo's draw order, so
  an order moved into the race reorders capo-pobb. `build_round_order` on a remainder is not its
  tail.
- **θ per decision sits with the race** (`PoBBRace` / `PoBBCheck`); **the pool sits with the
  sampler**, which hands the race its filtered pool on `RoundSamples` (crowning cells already
  dropped). The race never reads the search split itself.
- **Which member's knobs carry `row_choice` and `refit`** is yours: decide by where each is read and
  say why. `pobb`'s knobs would carry them to capo-pobb too, which keeps the switch generic.
- **Under `per_candidate`** (today's shape, the only one before slice 4), every survivor answers the
  same next row: the hook answers one row list for all walks at an open, on the draw.
- **The top-up never substitutes.** `PoBBRace.top_up` is the same object, and under `overlap`
  (which `informative` requires) the top-up onto the origin panel runs (`round.py::_measure_gaps`).
  Those walks' rows are each member's own panel gaps, one list per member, so "one row list for all
  walks" does not hold there. The hook keeps each top-up walk's own order. Tell a top-up open from a
  draw open by what the race holds, never by a flag the caller passes.
- **What derives each fixed-draw reader once the hook substitutes rows** (verify at a758e4dee, and
  grep for any this list misses):
  - `measuring(sample_order=…)` (`round.py::_walk_population`, the declared order: the readout's
    head, the dashboard's axis) → the walk's own rows, refiled by `on_turn` when the choice moves.
  - `ctx.asked(key, panel.order)` → the rows the walk was asked, at `on_decided`.
  - block `of` → the budget (`Walk.n`).
  - `PoBBCheck(n_samples=len(panel.cells))` → the budget.
  - `coverage_floor` → unaffected.
  - `ReferencePairs.declared` (`panel.cells` keys unless `best_so_far`) → the union of rows the
    walks took.
  - `RoundPanel.block` → unaffected.
- **The parent's catch-up (`CatchUpPool`) on a substituted row the parent never answered**: caught
  up as today, or not. Decide, and say why.

## Owns
- `application/optimizers/potter/` (race.py, pobb.py, members.py, knobs.py),
  `application/optimizers/nodes.py::RoundSamples`;
- `application/intelligence/rasch.py`;
- `application/runner/round.py` (the readers above);
- the `domain/` knob and coupling rows these need;
- `docs/methods/verdict-resolution.md`, `docs/methods/candidate-elimination.md`;
- their tests, the generated specs (by their script), and `assets/behaviour_keys.json`
  (regenerated by `offline_run.py`).

**Do not touch** `application/scoring/` (`query_loop.py`, `catch_up.py`, `search_point_scorer.py`:
call `open_walk(budget=)`, never edit it), `infrastructure/projections/live_dashboard.py` or
`tests/test_spend.py`: `ml-3` owns them. If your slice needs a loop change, stop and report it.

## Proof
- **equals-main against a758e4dee:** decisions, requests and resume legs unmoved under today's
  values. The potter and capo-pobb keys move: name old → new.
- **Tests:**
  - Under `informative`, the hook's row is the most informative at the θ PoBB last refit
    (`per_decision`) or at the round's θ (`per_round`), never by the parent's grade.
  - A pause and resume mid-round under `informative` re-chooses the same rows and files no new
    record. The offline resume legs cover the identity order only.
  - `informative` without `overlap` is refused.
- **Full gate green.** Report on `out/ml-2-report`.
