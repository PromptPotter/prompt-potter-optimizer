# tw-c: every hand-seat left after the second pass, onto the world (cloud, Sonnet)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
`origin/land/test-world` at `<BASE-C>`. That is `3625a6687` plus tw-r2, tw-s, tw-r3, tw-x and tw-j,
integrated.
Replace the ancestor check with `git merge-base --is-ancestor 9cd30b5a7 <BASE-C>`. Equals-main runs against
`<BASE-C>`'s offline home. Push to `arc/tw-c`, one commit per link, with the full gate green at the tip.

**`tests/CLAUDE.md` binds whole**: the admission rule, the three tiers, the per-test budget, and § The world
as tw-x left it. The world's doors are decided. You re-point onto them, and you build no door.

## The facts
Re-derive every count at `<BASE-C>` with
`git grep -cE "\b(loop_session|seat|close_round|set_cycle|minted_campaign|stored_campaign|mint_cycle|write_resolved_run)\(" -- tests/`.
At `52a173216`, before the second pass, test_security alone held 26 `minted_campaign`, 12 `stored_campaign`,
22 `mint_cycle` and 1 `loop_session` (:3651). Its phase-1 census is `out/tw-2-report:report.md`, so its
facts are judged already.

1. **Every call above, in any test file, moves to the world's door** for the state it seats: a minted
   campaign, a run round, a fork, a rewind or an inner mint. A fact that needs only a minted campaign uses
   the world's mint door and runs no round. A fixture shared by several facts shares its run, and its cost
   per fact is stated. Your link's body names each builder you stopped calling.
2. **`scripts/gate.py`'s three tests leave test_security** (`test_a_webapp_gate_runs_the_engine_checks…`,
   `test_the_walk_world_check_under_no_key…` and `test_a_declared_write_is_what_orders_two_gate_checks`).
   Per § Structural invariants live in production, each one becomes a check in gate.py that refuses the
   wrong wiring when the gate builds its check list. Otherwise it is deleted, with the admission clause it
   fails. No test file for gate.py is created.
3. **Every test over the 60-line budget**, in any file, its helpers counted (`tests/CLAUDE.md` § Budget
   per test), with the list re-derived by an AST count at `<BASE-C>`. tw-s kept several in test_spend, the
   longest at 129 and 135 lines. Each one is cut to budget or split into its independent facts. A step
   whose state feeds the next stays one function, and one line above it names that dependency.
4. **A fact the world cannot seat stops you.** Report it as a row, with the door it lacks, and leave it in
   place. Never build a seam, and never keep a builder for it.

## Production
You may edit `scripts/gate.py` for item 2 and nothing else in production. A row you find goes in your
report.

## Proof
- Before → after, per file you touched and for the suite: lines, test count, lines per test, pytest
  wall, and the pytest process's committed MB at start and end.
- Every test still over 2 s or leaving over 2 MB, named.
- **Coverage diff** (`sys.monitoring` PY_START, base vs tip) over the production modules your files
  reach. Every body the base ran and the tip does not is deleted code, restored, or named with its reason.
- Equals-main against `<BASE-C>`: every optimizer and leg UNMOVED.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-c-report`, plus the builders in `tests/factories.py` you
left with zero callers. I delete those builders at integration.
