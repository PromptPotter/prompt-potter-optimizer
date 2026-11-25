# ml-5: rolling dispatch (cloud · land/measuring-loop)

`briefs/common.md` binds you, with three overrides:
1. **BASE is `origin/land/measuring-loop` at `329a98f21`** (slices 1–4 on main `da08f4717`), not
   on main. Replace the ancestor check with `git merge-base --is-ancestor da08f4717 329a98f21`.
   equals-main runs against `329a98f21`'s offline home. Since W1, a behaviour key hashes the
   optimizer package's code, so any potter or pobb edit moves potter's and capo-pobb's keys:
   regenerate them with `offline_run.py`.
2. **This is roadmap work, not a ledger section.** Your spec is `docs/specs/roadmap.md`, the bullet
   "One measuring loop inside the round". Read it whole; never edit roadmap.md.
3. **THE CODE SHRINKS is no target here**: hunt deletions, and name every added line's reason.

Push to `arc/ml-5` as ONE commit, or several only where the full gate is green at each; a doc
rides the code it describes. Beside you run **potter-w1** on the same base and **l4-key-a2** on
main's L4 files (see Owns). **You share `potter/race.py` and `potter/pobb.py` with potter-w1, by
function:** yours are `PoBBRace.choose_rows`, `_per_round`, `_hand_out`, `_nearness` and the
race's block readers, and `PoBBCheck.check` with `FieldRead`; theirs are `_RaceDecision`,
`EliminationCut`, the record `PoBBRace.close` files, `PoBBStop`, and everything in `election.py`
and the replayers.

## Your base
Read `git log --format='%h %s%n%b' da08f4717..329a98f21`. In short, after ml-4:
- `query_loop.py::BlockRace.choose_rows(taken, ahead)` answers each live walk a
  `domain/sample.py::Stretch(rows, calls)`: its untaken pool in the order it takes them, and how
  many of the block's calls it answers. A block closes once the calls it handed out are back.
- `pobb.shape`: `per_candidate` (today's) hands every survivor `block_size` calls on the same next
  rows; `per_round` hands the block's `block_size × survivors` calls one at a time
  (`race.py::_hand_out`) by I·v²·φ(z)/φ(0), derived in `candidate-elimination.md` § Who answers a
  block's calls. `PoBBCheck.read` is the last field fit (`FieldRead`: θ, variance, P(best), ε(n)).
- PoBB reads the union of rows under `per_round`, each arm's `n_min` and ε(n) on its own depth;
  `EliminationCut` holds `arm_cells` and each rival's grades on the cells read.
- A walk builds a row's state when the row is first taken; `Walk.sendable` bounds what `fill` sends
  and the gauge's `allowed`.

## Owed by ml-4: yours, folded into your commit
- **A pause and resume mid-round under `per_round` re-chooses the same pairs and files no new
  record.** ml-4 built no test of it; build it first, and fix what it finds.
- **`qN/M` reads past its budget.** The offline readout prints `q25/20` (at ml-4's base too): a
  walk's top-ups count against `n_samples`. Root: name the budget a top-up extends. Re-test: no
  `qN/M` with N>M in the offline run's `readout.log`. It is a block reader, so it is yours below.
- **Price the parent's catch-up where it differs.** ml-4 priced it under `random` (51 vs 50 calls of
  ~300: random rows share one order). Price it on the offline potter run under `informative` ×
  `per_round` × `vote: overlap` against `per_candidate`, batch and rolling, and report it.

## Your slice: `dispatch`, today's value unmoved
- **`batch`** is today's: the loop decides once every call a block handed out is back
  (`_ScoringPhase.close_block`).
- **`rolling`**: the loop decides on every returned row. At once it sends as many new calls as the
  information then warrants, from none up to the in-flight cap, and never waits for the rest.
  - **One loop, never a second.** Under `rolling` a decision is a close whose block is one returned
    row: the same `close` → cut → `choose_rows` path, run per return. No second scheduler beside
    `_ScoringPhase`.
  - **"As many as warrants."** Derive from the decision statistic (ml-4's) how many calls a decision
    sends: none when the calls already out can settle it, up to the press when none can. Record the
    derivation in `docs/methods/candidate-elimination.md`. The press stays the cap and the one
    in-flight switch.
  - **The order a decision reads returns.** The roadmap names two: `launch` and `arrival`.
    - `launch` reads each walk's cells in the order they were sent, a returned cell waiting for the
      ones sent before it. The record is then the press's invariant: a press moves the bill, never
      the record.
    - `arrival` reads them as they come. The record then depends on timing, so it files the order
      each decision read, and a resume replays that order, never a new one.
    Build both. `launch` is `rolling`'s value until slice 6 settles the default.
- **The check's price.** `PoBBCheck.check` fits one Rasch model per call (`field_p_best`). Per return,
  that is one fit per row. Measure its wall time on the offline run (`batch` vs `rolling`, press 1
  and N). If it binds, warm-start the fit from the last one inside `rasch.py`; never check every
  k-th return, which is a second cadence.
- **A cut wastes at most the press** (ml-3's bound). Under `rolling` the waste is what was in flight
  when the cut landed: keep the bound and its test.
- **Readers of a block.** Under `rolling` there is no block of rows every survivor closes together.
  Grep the readers of `block`, `block_size` and `RoundPanel.block` (readout's `qN/M`, the dashboard's
  axis, `ReferencePairs`). Derive each from the rows a walk took, and never print a block a
  `rolling` round did not have.

## Owns
- `application/scoring/query_loop.py`, `catch_up.py`, `search_point_scorer.py::open_walk` (never
  `measurement_log.py`: l4-key-a2's);
- `application/optimizers/potter/` race.py and pobb.py by the functions named above, knobs.py,
  members.py, couplings.py; `application/optimizers/nodes.py` (the race protocol);
- `application/intelligence/rasch.py` (a warm start only, if measured to bind);
- `application/runner/round.py` (a block reader);
- `infrastructure/projections/live_dashboard.py`, `application/views/readout.py`;
- the `domain/` knob and coupling rows, and the `pobb` block of
  `assets/optimizers/potter/pipeline.yaml` (potter-w1 holds its L2 block and `l1_critique/1`);
- `docs/methods/candidate-elimination.md`, the `docs/developer/` look-ahead lines;
- their tests, the generated specs, and `assets/behaviour_keys.json` (regenerated by `offline_run.py`).

## Proof
- **equals-main against `329a98f21`**: decisions, requests and resume legs unmoved under today's
  values (`batch`, `per_candidate`). Name each key move, old → new.
- **Tests:**
  - Under `rolling`, a decision runs on a single returned row, and a cut lands before the slowest
    call of its block returns.
  - Under `rolling` + `launch`, press N's record equals press 1's (rows, stops, absorbed).
  - Under `rolling` + `arrival`, a seeded out-of-order completion files its read order, and a resume
    replays it with no new record.
  - A decision whose in-flight calls can settle it sends none.
  - A cut under `rolling` wastes no more than the press.
  - ml-4's owed: a pause and resume mid-round under `per_round` re-chooses the same pairs and
    files no new record; no `qN/M` with N>M.
- **The fit's wall time**, `batch` vs `rolling`, and the parent's catch-up price, in the report.
- **Full gate green.** Report on `out/ml-5-report`.
