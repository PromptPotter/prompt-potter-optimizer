# w7-open2: the fork is a request kind; the embedded probe runs before the opening (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-open's one `Opening` per run
among them). It is not on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md`, these rows each read whole: **A fork's cut is opened beside
its admission**, **The embedded fresh door probes its backend after its session opens** (both
§ wave-7), and the fork half of **A fork is not opened under a carried admission** (§ wave-6 · arch
review 2, root A). Read w7-open's report first (`git show origin/out/w7-open-report:report.md`).

DECIDED:
1. A fork is a `LaunchRequest` kind whose `open` cuts it: `commands/launching.py::fork_cycle` cuts
   nothing itself, and the fork enters through `admit` under the admission it carries, as every other
   kind does. Re-test: `fork_cycle` holds no cut, and the carried-admission row closes whole.
2. The embedded fresh door probes its backend before the opening, so a dead backend opens no session.
   Re-test (write it as a test): a fresh embedded launch on a dead backend opens no session.
3. Parity: the five entry points reach the fork through the one request (CLAUDE.md § entry-point parity).

OWNS: `application/commands/launching.py`, `application/jobs/launcher/**` (`opening.py`,
`admission.py`), `application/embedded_run.py`, `runner/run_init.py`, and their tests. Not yours:
`identity/**`, the bot, `views/readout.py` (w7-rows), the stores' versions and `io.py` (w7-ver2),
`scripts/` (w7-gate2). If you need a new command kind beside w7-rows', name the shared file in the
report.

LEAN: `promptpotter/` shrinks. Equals-main is unchanged across every leg (a fork leg included).
Digests and behaviour keys are unchanged. Run data: none expected; name any.
