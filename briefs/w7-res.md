# w7-res: `resolved(subject)` returns one run's resolution, built from records (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with TWO overrides:
- BASE FIRST: your base is **`origin/land/w7` at `c45354f8d`** (main `2c7dff34e` plus w7-small,
  w7-text, w7-host, w7-line and w7-open, one Opening per run). It is not on main yet, so skip the
  is-ancestor check and report `git log -1 origin/land/w7`.
- Digests and behaviour keys MAY move here: the compiler is a hashed unit. Name every move old → new
  per optimizer and why; the orchestrator lands this with the next planned re-key, never alone.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **`resolved(subject)` returns one run's
schema, params, config, optimizer selection and scorer, built from records (root B)**, every row it
names under **Closes** and its adjacent rows, plus **The `--set` path grammar lives in the CLI** (its
grammar moves to `application/`; the CLI, the API and `/potter-run` call it). Read each whole.

DECIDED (the root's own words):
1. One `resolved(subject)` builds schema, params, config, optimizer selection and scorer from the
   records, for the read and the run alike; `pipeline_resolve.py`'s two folds (`_campaign_merge`,
   `configure_and_apply_pipeline`) become that one, with no `if session.cycle_id:` branch.
2. A `Session` binds the resolved value to a live client and builds none of it:
   `run_observers.py::_bind_observers` and `runner/output.py` read the resolution, not `session.`.
3. The scorer is compiled once per resolution: `scorer_of(` leaves its 10 call sites, and
   `verifier_graded` is the bank's recorded answer everywhere (`verifier_graded=False` 9 → 0). If it is
   not yet a recorded fact of the mint, the mint records it (name the run-data reshape and its wipe).

OWNS: `promptpotter/application/pipeline_resolve.py`, `promptpotter/application/datasets/authored.py`,
`promptpotter/application/initialization/**`, every `scorer_of(` caller (scorer lines only in
`scoring/closed_rounds.py`, whose read and version are w7-ver's), `run_observers.py::_bind_observers`,
`runner/output.py`, `optimizer_manifest.py`, `config_map.py`, `presentation/cli/commands/launch.py`'s
`--set` grammar and its new `application/` home, and the tests of each. Not yours: the stores' versions
and the dashboard frame (w7-ver), `webapp/lib/derivations/**` (w7-web), `scripts/` (w7-gate).

LEAN: a consolidation of two folds and ten compiles, so `promptpotter/` shrinks clearly. Re-tests:
`git grep -c "verifier_graded=False" -- promptpotter` 0; `scorer_of(` at one site;
`git grep -n "def _set_body" -- promptpotter/presentation` empty.
