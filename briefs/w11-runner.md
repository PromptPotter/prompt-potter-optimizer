# w11-runner: area 03 to its Target, the bench below the optimizers (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 03 and the debt doc's `## 03` section. w11-plugin landed: the contract now lives at
`application/optimizer_contract.py` and the loader registers from above, so the bench's knot to
`optimizers/` can be cut now. Target: the bench walks a manifest's `default` once per round, and walks
`initial_population` in a round entered with no population. It decides nothing an optimizer owns and
imports no optimizer. Init writes a frozen `Session`; the runner writes only `Session.state`. There is one
`RunScope`. A stop is a `StopReason` declared at one door. Resume and fork read standing rounds off the
ledger.

Links, deletion first:
1. **The stop has one door.**
   - `run_observers.py::declare_run_stop` takes a paused stop's words from
     `take_pause() or (interrupted_by, "")`, so a self-pause cannot say why. This was reported twice and
     never filed.
   - It also requires an `interrupted_by` that non-paused stops ignore.
   - A stop's error has two doors, `end_run_on` and `Stores.declare_stop(error=)`; the run uses neither
     of them as the second.
   - Cut this to one door. A stop names its reason and its words.
2. **A cancel during `asyncio.to_thread(_cut_rebase_fork)` mints a fork the parent never names.** The
   fork is named before the thread runs, or nothing is minted.
3. **Upward imports.**
   - `scoring/passes.py` and `evidence/head_to_head.py` import `runner/bench.py`.
   - `jobs/launcher/run_job.py` imports `runner/entry.py`, while `runner/` imports `jobs/` at 11 sites.
   - w11-plugin's 5 runner-imported-from-above lines (its "filed under 03" row).
   - Move each fact to the layer that owns it. Don't add a re-export.
4. **`bench/resume_and_fork/repair.py`** (13 lines, 35 commits, a stub of a retired mechanism): delete it,
   or name what it is for.
5. **`runner/round.py`** (1,402 lines): does it hold one phase per function? Split along the phases that
   `domain/phases.py` names, or give a verdict on why not. Fold the "10 modules, not 8" row into this
   link's shape verdict: the Target names which concept owns each module.
6. **Slow offline facts become pure functions.** The facts that w10-tests and w11-tests2 parked as "a rule
   reached only through a round" are decided where a table can reach them: in the round's pure
   functions, not in a world. Coordinate through the debt row "A rule the suite reaches only through a
   round is decided where no table can reach it". w11-tests2 owns `tests/`. You move the decision. It
   owns the fact, unless it has not landed when you reach this link; then add the table fact yourself
   and name it in the report.
7. **Census:** `initialization/scoring_scope.py`, `embedded_run.py`, `initialization/session.py`,
   `runner/output.py`, `domain/activity.py`, `resume_and_fork/resume.py`, `origin.py`,
   `bench/node_context.py`, `bench/cycle.py`, `runner/bench.py`. Give each a verdict. Re-test "the
   embedded fresh door probes its backend after its session opens".

Held: a max-press resume replays on timing (unreproduced), and a resume's wait is unattributed (it needs
the box timed).

OWNS: `application/runner/**` less `inner/`, `application/bench/**`, `application/initialization/**`,
`run_observers.py`, `run_phase_control.py`, `origin.py`, `embedded_run.py`, `preflight.py`,
`domain/phases.py`, `phase_acts.py`, `activity.py`. You may re-point imports only in `scoring/`,
`evidence/` and `jobs/`. `scoring/` is also w11-scoring2's, which edits no import line.

DONE WHEN:
- `git grep -n "^from promptpotter.application.runner" -- promptpotter/application/scoring
  promptpotter/application/evidence promptpotter/application/jobs` is empty, or each edge is declared.
- No module under `runner/` or `bench/` imports `optimizers/`.
- `git grep -c "take_pause() or" -- promptpotter` is 0.
- Equals-main holds every leg UNMOVED, or names each move.
- The keys are named.
