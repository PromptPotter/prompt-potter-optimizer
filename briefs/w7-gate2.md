# w7-gate2: the gate's remaining quarter hour (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7` at `3c778826b`** (main `2c7dff34e` plus six wave-7 landings, w7-gate's schedule
among them). It is not on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/roadmap.md` § Then: what makes a gate and a CI run take a quarter hour (its second half),
and these four rows in `docs/specs/code-debt-cleanup.md`, each read whole: **The gate meters no check's
CPU**, **Vitest's start meets load from outside the gate lock**, **`walk-world` runs in CI's `check`
job, where nothing reads it**, **Three tests over the 2 s budget**. The brief decides each door; build
it as the row states.

LINKS, in order, one commit each:
1. CPU metering. `scripts/processes.py::close_job` returns CPU-s beside the peak: the Windows job's
   `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION` (TotalUserTime + TotalKernelTime) and POSIX `os.wait4`'s
   `ru_utime + ru_stime`. The gate's table prints each check's CPU-s ÷ wall. `Check.slots` stays the
   hand-measured value; the printed ratio is what re-measures it.
2. `walk-world` runs only where playwright does. Whether a check re-execs is whether it runs the
   engine, not its kind (`scripts/gate.py::_PINNED_KINDS`). Re-test: `gate.py --py` lists no
   `walk-world`, `gate.py --web` still re-execs and walks it, and `ci.yml`'s `check` job runs no walk.
3. `npm test` queues behind the clone's gate lock, as the gate does: the lock `gate.py` takes is
   reachable from the `test` script in `webapp/package.json` (a small node or python shim the script
   names, no new dependency). The gate's own vitest run then drops `started=lambda: False` and is
   scheduled by its slots like any check. You cannot run the Windows re-test: name it for the operator.
4. The three tests over budget (`tests/test_security.py`): each fact rides a fixture or a check that
   already pays its cost, or the test goes with its fact restated where it is already proven. Read
   `tests/CLAUDE.md` whole first. Re-test: `pytest --durations=10` lists none of the three over 2 s.

OWNS: `scripts/gate.py`, `scripts/processes.py`, `.github/workflows/ci.yml`, `webapp/package.json`
(its `test` script only), `webapp/vitest.config.ts`, `tests/test_security.py` (those three tests and
their fixtures), and the rows you close. Not yours: `promptpotter/` beyond what a test fixture needs
(w7-ver, w7-web and w7-res run beside you), `webapp/lib/**`, `webapp/e2e/**`.

LEAN: `promptpotter/` unchanged. Measure `CI=1 python scripts/gate.py` wall at base and tip on the
same box, warm, and report both. Digests, behaviour keys and equals-main are unchanged; run data: none.
