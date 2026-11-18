# l4-words-fix: the review's fix pass, on `arc/l4-words` (cloud)

`briefs/common.md` binds you. Base: `origin/arc/l4-words` (5360592bb); push `arc/l4-words-fix`,
`wip/l4-words-fix`, `out/l4-words-fix-report`. Read `briefs/l4-words.md` (the chain's brief) and
`git diff 2975f7225 origin/arc/l4-words` first. An independent review found the defects below; verify
each at its line before building, report any that reads false. One commit per finding, or one commit
for the pass if each is a few lines.

## Findings
1. **The outer cell reads its inner run's stop off the wrong cycle after a rebase**:
   `runner/inner/spawn.py::run_inner_cycle` reads `last(minted[-1], RunPhaseRecord)`, the cycle the
   seat opened, while `_run_inner_campaign` reads its rounds off `ended.hop`. A potter inner run
   that rebases and then stops on the fork reads "stopped: Rebased to fork". Root: the run hands back
   the words of the hop it ended on, read where its rounds are read; `minted[-1]` stops being a stop
   reader. **Re-test:** an inner run that rebases then stops on `provider_credit_exhausted` puts that
   stop's words in the outer cell's error.
2. **`StopReason.panel_cut` leaves a SURVIVOR unloadable**: `jobs/*.json` is the `job` row of
   `infrastructure/store/data_root.py` (SURVIVOR), its `ending` is a `StopReason`, and v0.8.20 writes
   `panel_cut`: a job the day launched that ended on a panel cut refuses the landing. Ship the one-shot
   migration (`application/maintenance/migrations/`, registered in `migrate.py::MIGRATIONS`, its
   docstring naming the shape v0.8.20 wrote): each `jobs/*.json` whose `ending` is `panel_cut` ends
   on `panel_hole`. `jobs/ended/` is run data: no migration. Correct the commit's wipe claim.
   **Re-test:** a landing over a job file ending `panel_cut` stages it to `panel_hole` and proves.
3. **`panel_gate_tripped`'s detail prints "None" and has no bound**: `f"{category}: {error}"` reads
   "UNSCOREABLE: None" on an unset error; and an L4 outage joins one clause per holed cell into a
   detail every door serves. A row with no error is worded by its category alone; the detail names
   the first few distinct holes and the count of the rest.
4. **The backend-hole re-test passes by construction** (`tests/test_spend.py`, the two-504 test): its
   stub writes the exact `RunPhaseRecord` the code reads, on the cycle it appends to `minted`. Rewire
   it through the real stop path (`end_run_on` / `declare_run_stop`), so it fails on finding 1.
5. **`PANEL_HOLE`'s comment and `next_acts`** (`domain/phases.py`): "a cell no configuration answers
   for" is wrong (it counts every errored row, charged categories too), and for a HALTED hole a resume
   re-buys the same cut. Correct the words; the act for a hole our bound made says so.
6. `PANEL_HOLE.grades_selection` False beside `BACKEND_HOLE`'s True: decide, say why in the commit.

## Owns
As `l4-words` plus `application/maintenance/migrations/` and `migrate.py`. NOT `spend_book.py`,
`domain/spend.py`, `run_observers.py`, `read_model.py`, `runner/bench.py` / `loop.py` / `entry.py`
(`spend-served-fix`), NOT `presentation/cli/main.py`.

## Target
Net ≤ 0 `promptpotter/` lines beside the migration (name its lines). Run the `--against` legs and
`--digests`; name every leg and digest that moves, old→new.

## Report
Per `common.md`.
