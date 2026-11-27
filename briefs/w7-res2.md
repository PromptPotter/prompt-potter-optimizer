# w7-res2: root B's open half, two doors over one fold (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe` at `637a469a3`** (land/w7 7b3e5e293 plus w7-res, which WIPES run data, so it
waits on a deploy). It is not on main, so skip the is-ancestor check and report
`git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **`resolved(subject)` returns one run's
schema, params, config, optimizer selection and scorer, built from records (root B)**, which w7-res
cut to its open half: two doors over one fold, and the projections. Read it whole, plus w7-res's report
(`git show origin/out/w7-res-report:report.md`) and the three w7-res rows in § wave-7 (a cycle with no
`PartitionRecord` raises two errors; three sites spell a draft's verifier grading; `open_cell` resolves
a whole dataset).

DECIDED:
1. One door: `configure_and_apply_pipeline` and `resolved` become one `resolved(subject)` that the read
   and the run both call. Re-test: `git grep -c "def configure_and_apply_pipeline" -- promptpotter` is 0.
2. The projections (`run_observers.py::_bind_observers`, `runner/output.py`) read the resolution and
   never `session.` for a resolved fact. Re-test: `session.` in `output.py` falls from 12 toward 0; name
   each that stays and why.
3. The three w7-res rows close with it: one reader of a cycle's draw raises one error type; a draft's
   rows are a `Bank`, so `all_verifier_graded(` has one site; `open_cell` resolves no dataset.

OWNS: as w7-res did (`pipeline_resolve.py`, `datasets/authored.py`, `initialization/**`,
`run_observers.py::_bind_observers`, `runner/output.py`, `optimizer_manifest.py`, `config_map.py`,
`domain/sample.py`, `startability.py`, `preflight.py`, `draft_patch.py`, `measurement_log.py::open_cell`
only) and their tests. Shared and named: `runner/run_init.py` belongs to w7-open2. Touch only its call
of the fold, and name every line you change there in the report. Not yours: the stores' versions,
`hierarchy.py`, `served_dashboard.py` and the rest of `measurement_log.py` (w7-ver2), `identity/**`, the
bot, `views/readout.py` (w7-rows), `scripts/`.

LEAN: two folds become one, so `promptpotter/` shrinks clearly. Equals-main is unchanged across every
leg, and digests and behaviour keys are unchanged. Run data: w7-res's wipe already covers a
`PartitionRecord` reshape; name any other.
