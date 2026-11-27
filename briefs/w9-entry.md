# w9-entry: the stop names the fork; the stop vocabulary is the domain's (cloud · Opus)

`briefs/common.md` and `briefs/w9-shared.md` bind you. w8-fork left the first row for this chain.

SPEC: `docs/specs/code-debt-cleanup.md`, three rows:
1. **"Rebased to fork" names no fork** (the narrowed row in the wave-6 "v0.8.21 box test" section):
   the rebase is cut before its stop is declared, so the stop's detail names the round it was cut at,
   the child cycle and the reason. Today `entry.py::open_line` passes no detail, and
   `_run_single_cycle` cuts after `close_cycle`.
2. **`runner/termination.py` is a phase module that `jobs/` reads**: the stop vocabulary moves to
   `domain/phases.py` and `termination.py` goes. Its importers (about 19 hits in 10 files:
   `jobs/registry.py`, `nodes.py`, `runner/bench.py`, `loop.py`, `round.py`, `scoring/passes.py`,
   `connectors/harbor.py`, `domain/cycle_result.py`, …) are import re-points only.
3. **The never-closed-origin edge of `fork_edge` has no door**: the row's line numbers are stale. The
   edge is `fork_siblings.py` near :145–157, and `tests/test_archive.py` calls `fork_edge(..., None)`
   directly, so only the `origin is None` raise is unreached. Give it a door, or delete the edge if
   no state reaches it (and say which).

OWNS: `application/runner/entry.py`, `runner/termination.py`, `domain/phases.py`,
`application/bench/resume_and_fork/`, and their tests. Not yours: `runner/round.py`,
`scoring/query_loop.py` (w9-pass's), `runner/bench.py` and `runner/loop.py` (w9-payer's). In those,
the `termination` import re-point only.

Keys: the stop record's place in the ledger moves, so keys that read ledger order may move; name each
old→new. Wipe: none expected; name one if it happens. `runner/` falls from 10 modules to 9, so row
"Wave B — `application/runner/` is 10 modules" stays open; update its count.

LEAN: `promptpotter/` shrinks (expected about +60 / −130).
