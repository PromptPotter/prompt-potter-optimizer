# rk-dec-2: the race's three bugs and the noise-walk readout (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rk-dec-2`, arc `arc/rk-dec-2`.

- **Base:** `origin/main` at `61ef07161` (rk-dec links 1–4 landed). Your `<base sha>` in common.md is this commit.
- **Start from `origin/wip/rk-dec` (`3b8b8c376`)**: link 5's attempt, stopped on the planted-arm proof. Read `rk-dec-report.md` (`origin/out/rk-dec-report`) and `briefs/rk-dec.md` whole; their decisions stand except where this brief replaces one.
- **Spec:** rk-dec's report, "Rows to file" and "Symptoms", plus link 6 of `briefs/rk-dec.md`.

## Decisions (orchestrator; build them, do not reopen them)

- **The cut is a family-wise p_best, not a pairwise block race.**
  - At each shared checkpoint, an arm is cut when its posterior probability of being the best of {parent, every standing arm} falls below ε. The probability comes from PoBB's own joint posterior, by Monte Carlo over the arms' θ draws, seeded.
  - The probabilities sum to 1 across the field, so the multiplicity is inside the statement, and slot order enters nowhere.
  - Pairwise "credibly worse than some rival" goes, along with its per-slot sibling set.
  - `pobb.py::PoBBCheck.check` states the new rule in one docstring line, and `candidate-elimination.md` says the same.
- **The top-up is one more stretch of the race,** with the round's eliminator handed in and the cut landed as `Cut(Eliminated)` (as the wip does). Fix the root of the symptom where a cut that never reaches the arm's state is re-bought by `measure_overlap`'s trigger monitor: the cut reaches the state the monitor reads.
- **The origin panel is in walk order,** as the wip does. A resumed round rebuilds the same panel.
- **`block_size=1` appends one `ArmRecord` per arm per cell:** that is the symptom. An arm's record is appended once per checkpoint.
- **`StopReason.CONVERGED` goes** with the tests that hold it. `convergence_patience` went in rk-dec 4, so nothing produces it.
- **The noise-walk readout (link 6),** as `briefs/rk-dec.md` decides it: μ0, τ and hold-up per round, as one projection over the round's own arm-vs-parent pairs, served on the existing round channel to all five doors.

## Links (one commit each)

5. The race: the family-wise p_best cut, the raced top-up, the walk-order panel, one record per checkpoint, CONVERGED gone.
6. The noise-walk readout.

## LEAN is a stop

Links 5 and 6 together keep `promptpotter/` at net ≤ 0 lines (`git diff --shortstat <base> -- promptpotter`). If they can't, stop on `wip/rk-dec-2` and name the deletions that would pay for them. No surplus is explained away.

## Proof (beyond common.md; a miss is a stop, not a tune)

- The planted-arm Monte Carlo over the real `PoBBCheck` (5 arms, 20 cells, n_min 6, 1500 trials, the wip's harness):
  - equal arms' cut share by slot is flat;
  - a planted better arm at a random slot is cut no more often than 7.1% (serial's slot average);
  - report the share at each ε in {0.05, 0.10}.
- Offline:
  - the elected arm moves in at most about 2% of matched rounds;
  - the top-up spends at least 5% fewer cells.
- Equals-main: name each leg's move by link. Link 6 must move no decision.
