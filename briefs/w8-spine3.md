# w8-spine3: root C2, now with its key moves allowed (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-spine2 landed links 1 and 2 (the walk
fill, `CellSheet.filled_from`). Its link 3 stopped only because it moved keys. Its report is on
`origin/out/w8-spine2-report`.

OVERRIDE of w8-shared (operator rule: a re-key never defers a fix). This chain may move behaviour
keys and digests. Land each move, explain it by its mechanism, and name it old→new. A leg whose
decisions move for a reason you cannot name stops the link.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per link:
1. Rebuild `origin/wip/w8-spine2`'s `a1e85af` on today's tree (it does not cherry-pick cleanly:
   regenerate `built_key.py` and `behaviour_keys.json`). It deletes `Invalid` (`Cut.reason` None)
   and `RoundOutcome.diagnostics` (potter's facade derives it). Its known moves are capo
   `a06e2f…`, capo-pobb `811eca…`, potter `23d796…`: an invalid arm's `rounds[N]` reason
   `{kind: invalid}` becomes null. Closes **The neutral round envelope…** and **An invalid arm is
   recorded twice**.
2. **`QueryLoopState.running` re-folds what the recorder already holds** (spine2 filed this): its
   readers read the recorder.
3. **A record holds no value another record derives (root C2)**: the rows it names by title that
   are left, largest first.
4. Symptom: `potter/pobb.py:70` `race_sheet` builds sheets with `scorer_id=''`, so each reader
   re-grades them (`scoring/catch_up.py:75`). Root: a race's priors are filed under the session
   scorer.

Leave **Overlap and repair keep their own pass families** alone: it is held on
`domain/run_standing.py`, which is w8-web2's.

OWNS: `promptpotter/domain/records/`, `domain/results.py`, `domain/arms.py`, `domain/scoring.py`,
`domain/pass_kinds.py`, `application/bench/` (`cycle.py`, `round_analysis.py`, `node_context.py`),
`application/scoring/` (`query_loop.py`, `catch_up.py`, `passes.py`, `paired.py`, `arm_states.py`),
`application/runner/round.py`, `application/views/readout.py`, `optimizers/potter/dispatch/facade.py`,
`optimizers/potter/pobb.py`, and their tests. Not yours: `run_standing.py` and `served_dashboard.py`
(w8-web2's), `runner/run_init.py` (w8-launch3's), `pipeline_resolve.py` and `scoring/formula/`,
`evaluators.py`, `measurement_log.py` (w8-resolve3's). Each is a re-point only.

LEAN: `promptpotter/` shrinks. Run data: name each wipe.
