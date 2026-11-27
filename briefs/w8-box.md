# w8-box: the install's environment and import (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. Batch 3 runs beside batch 2's five chains,
so keep strictly to your Owns. You cannot reach the box: build and prove on the repo alone, and name
each step the operator runs on the box under "Box:" in the commit body.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row:
1. **The box holds the app's environment twice**: one environment source for the services and the
   CLI. Name the box step, which moves the env file.
2. **`promptpotter/__init__.py` acts at import, on the process and on the data root**: import does
   nothing, and each entry point acts at its own start (CLAUDE.md § entry-point-parity: all five).
3. **The box runbook skips the move `site_records/` owes**: the step goes into `deploy-linux/`
   (`update.sh` stages it, or the runbook names it). Make it idempotent.
4. **A resume's wait is unattributed**: re-test it first. If its holder is `jobs/registry.py` (w8-launch2's),
   cut it with one line under that chain's row.

OWNS: `deploy-linux/`, `promptpotter/__init__.py`, `promptpotter/config/settings.py` (the env
source only), the entry points' start (`main.py` lifespan, `cli/main.py`'s start, `embedded_run.py`'s
start as a re-point; it is w8-launch2's file), and their tests.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. A settings
default that changes is a "Box:" line. No SURVIVOR moves.
