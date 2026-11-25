# ml-4: each candidate its own rows, PoBB on the union (cloud · land/measuring-loop)

`briefs/common.md` binds you, with three overrides:
1. **BASE is `origin/land/measuring-loop` at `f2afd7e3d`**: slices 1–3 squashed into one commit
   on main `da08f4717` (W1). Replace the ancestor check with
   `git merge-base --is-ancestor da08f4717 f2afd7e3d`. equals-main runs against `f2afd7e3d`'s
   offline home. Since W1, a behaviour key hashes the optimizer package's code, so any potter or
   pobb edit moves potter's and capo-pobb's keys: regenerate them with `offline_run.py`.
2. **This is roadmap work, not a ledger section.** Your spec is `docs/specs/roadmap.md`, the bullet
   "One measuring loop inside the round". Read it whole; never edit roadmap.md.
3. **THE CODE SHRINKS is no target here**: hunt deletions, and name every added line's reason.

Push to `arc/ml-4` as ONE commit, or several only where the full gate is green at each; a doc
rides the code it describes. Beside you runs **l4-key-a** on main (`runner/inner/`,
`domain/l4/cells.py`, `initialization/wiring.py`, `jobs/launcher/admission.py`, `dataset_access.py`,
`infrastructure/store/stores.py`, `infrastructure/run_scope.py`): leave those alone. **You share
`jobs/mint.py`**: yours is only the coupling refusal's move out of `refuse_unrunnable`; theirs is
`MintPlan.origin_hash` and the inner seat.

## Your base
Read `git show -s f2afd7e3d`: its message names each slice's seams. In short:
- `query_loop.py::BlockRace.choose_rows(taken, ahead)` runs at every block's open and answers each
  live walk's untaken pool in the order it takes them; `Walk.budget` is how many it takes.
- ml-2: `pobb`'s knobs `row_choice` (random | informative) and `refit` (per_round | per_decision),
  so capo-pobb inherits them. `PoBBRace.choose_rows` sorts each draw walk's untaken pool by
  `rasch.py::most_informative` at one set of θ. Under `per_decision` that is `PoBBCheck.refit`, the
  last field fit's θ moved onto the ruler (`theta_on_ruler`); under `per_round` it is the parent's θ
  on the seated ruler. A top-up walk (keyed in `_check.priors`) keeps its own order. The pool rides
  `RoundSamples.pool`, a walk opens on `RoundSamples.walked`, and `read_on` names the reference's
  rows. `informative` without `vote: overlap` is a `refused` coupling, enforced at the mint
  (`preflight.py::refuse_couplings`).
- ml-3: a walk has up to the press's cells out past its pending close
  (`Walk.unsent`), and `Walk.reorder` re-seats every cell out or back by sample id, discarding none.
  `Walk.submitted` and `_Acquired.idx` are gone, and `claiming` is keyed by sample id.

## Filed rows from ml-2 and ml-3: yours, folded into your one commit
- **A walk opens on the whole pool under `random` too** (ml-2's symptom). `round.py::_open_arm`
  opens every walk on `panel.walked`, so `open_walk` resolves the archive (`_resolve_prior_cache`)
  for every pool row of every arm, every round, even though `random` never takes past the draw.
  Today's default pays that I/O. Root: a walk's replay, claim and pricing state for a row is built
  when the row is first taken or sent, never eagerly at open. Measure the open's wall time on the
  offline run before and after. The slice-1 test
  `test_a_row_a_race_takes_past_the_opening_budget_is_priced_and_bought_once_on_resume` must stay
  green.
- **A refused coupling reaches only the mint** (ml-2's row). `refuse_couplings` runs in
  `jobs/mint.py::refuse_unrunnable`, so an overlay a resume or steer lays later
  (`informative` with `vote: draw`) runs. Root: couplings are asked where every selection passes
  (`optimizer_manifest.py::_select`), once.
- **`informative` on a cold ruler is the sampler's order, unreported.** `most_informative`'s ties
  keep the sampler's order, and a cold ruler ties every row. Report the row choice that ran on the
  round's record and readout, and say whether a round-1 ruler is ever cold.
- **`SampleStartedRecord.sample_idx` goes stale on a re-seat, and nothing reads it.**
  `Walk._started` stamps the row's index at launch; a later open re-seats the cell elsewhere. Delete
  the field and `LaunchEvent`'s `idx`. This is a ledger reshape: it rides your one run-data wipe, so
  count it with the record reshape below.
- **`Walk.flight`'s `allowed` reads the rule horizon alone.** Past a block close the press bounds
  what may be out, and `Flight` never learns the press, so the dashboard's gauge overstates under a
  block race. Derive `allowed` from the rows the walk may send now (`Walk.unsent` at the press the
  round runs at), so the gauge and `fill` read one bound.

## Your slice: `shape`, today's value unmoved
- **`per_candidate`** is today's: every survivor answers the same next row, one row list for all
  walks at a draw open (ml-2's rule).
- **`per_round`**: new calls go to whichever (candidate, row) pairs tell the decision most. Each walk
  answers its own rows, and an open may hand a walk none, one or several of the block's calls.
  - **The decision** is the cut (`PoBBCheck.check`: P(best) below ε(n)) and, past it, the election.
    Derive which pairs tell it most from that statistic and the ruler (θ, δ). Expected Fisher
    information at each candidate's own θ (`rasch.py::expected_information`) is the floor. Weigh it
    by how near each candidate's decision is, if the derivation says so. Record the derivation in
    `docs/methods/candidate-elimination.md`.
  - **The loop's change**: today every live walk's reach rises by `block_size` at each open. Under
    `per_round`, the hook also says how many of the block's calls each walk gets. A block closes when
    the calls it handed out are back. Keep one hook: reshape what `choose_rows` answers, never add a
    second one beside it. The passthrough answers `block_size` each and reproduces today's walk.
- **PoBB reads the union of rows.** `PoBBCheck.check` today reads `shared`, the rows every racer
  answered. Under `per_round`, that set empties. Read the field on the union:
  - `field_p_best` already fits one Rasch model on observations, so a missing cell is no hole.
  - Name what `n_min` and the ε ramp count under the union (today: `len(cells)` after `theta_cells`).
  - Name what `queries_scored` and the readout's `qN/M` print.
  - Under `per_candidate` the union equals `shared` on a draw open. On a top-up walk (each member's
    own panel gaps, `round.py::_measure_gaps`) it does not. Keep today's read there unless equals-main
    stays green, and say which you kept.
- **The replay record is reshaped.** `EliminationCut.candidate_cells` is one list of cells the field
  was read on. `_RaceDecision._read` refuses when the cut arm misses one: under the union it always
  would. The record holds what a replay re-reads: each arm's own cells and each rival's grades as
  read (`prior_histories` already does the second). Reshape it once. This is a run-data reshape: count
  the run data first (`ls .promptpotter/projects/*/campaigns`), say the count in the commit body, and
  write no reader of two shapes (the landing wipes).
- **The parent's catch-up under `per_round`**: the parent catches up on every row a candidate takes
  that it never answered (`CatchUpPool`), so the parent's bill grows with the union. Price it: the
  parent's calls per round under `per_round` vs `per_candidate` on the offline run. If a cheaper root
  exists (the parent read on the ruler alone), propose it in the report; don't build it.
- **The election under `per_round`**: `vote: draw` pairs each candidate with the parent on the cells
  both scored (`selection.py::discordant`), which still holds. `vote: overlap` is unaffected. Name
  any reader of "the round's rows" that assumes one row list (grep `panel.cells`, `sample_order`,
  `ReferencePairs`, `RoundPanel`, the dashboard's axis). Derive each from the walk's own rows, as ml-2
  did for the per-walk readers.
- **Couplings.** Say which `row_choice` × `shape` × `vote` pairs run and which are refused, with the
  reason for each refusal, in the members' `MemberCoupling` tables. Never a silent fallback.

## Owns
- `application/scoring/query_loop.py`, `search_point_scorer.py::open_walk`, `catch_up.py`;
- `application/optimizers/potter/` (race.py, pobb.py, members.py, knobs.py),
  `application/optimizers/nodes.py` (the race protocol and `RoundSamples`);
- `application/intelligence/rasch.py`;
- `application/runner/round.py`, `application/scoring/selection.py` (only a reader that assumes one
  row list);
- `infrastructure/projections/live_dashboard.py` and `application/views/readout.py`, if a line goes
  wrong under `per_round`;
- the `domain/` knob and coupling rows, and `domain/records/run_records.py::SampleStartedRecord`;
- `application/optimizer_manifest.py`, `application/preflight.py`, `application/jobs/mint.py`
  (the coupling's one ask);
- `docs/methods/candidate-elimination.md`, `docs/methods/verdict-resolution.md`;
- their tests, the generated specs (by their script), and `assets/behaviour_keys.json`
  (regenerated by `offline_run.py`).

## Proof
- **equals-main against `f2afd7e3d`**: decisions, requests and resume legs unmoved under today's values.
  The record reshape moves digests: name old → new and attribute each move. A moved decision under
  today's values is a red.
- **Tests:**
  - Under `per_round`, two walks at one open answer different rows. A walk handed no call that block
    waits, and the block closes on the calls handed out.
  - PoBB cuts on the union: a candidate is cut on rows its rivals never answered.
  - An `EliminationCut` filed under `per_round` replays True on resume.
  - A pause and resume mid-round under `per_round` re-chooses the same pairs and files no new record.
  - Under press N>1 and `per_round`, the record equals press 1's (rows, stops, absorbed).
  - A refused coupling is refused at check-in, by name, and on a resume whose overlay lays it.
  - The filed rows: a walk under `random` resolves no archive row past its draw; the gauge's
    `allowed` equals what `fill` may send under a block race; the round's record names the row
    choice that ran.
- **Full gate green.** Report on `out/ml-4-report`, with the parent's catch-up price, the walk
  open's wall time before and after, and the run-data count.
