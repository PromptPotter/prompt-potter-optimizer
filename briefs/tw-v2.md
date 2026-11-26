# tw-v2: the suite's last 3.6 s, at their production roots (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/arc/tw-v` at
`<BASE-V2>` = `77e13a563`, tw-v's tip, gate green in its session. Where this brief writes `<BASE-V2>`,
read `77e13a563`. Replace the ancestor check with `git merge-base --is-ancestor a1720cae0 <BASE-V2>`.
Equals-main runs against `<BASE-V2>`'s offline home. Push to `arc/tw-v2`, one commit per link, with the
full gate green at the tip. `wip/tw-v2`, `arc/tw-v2` and `out/tw-v2-report` are fresh on origin. If
one exists when you push, stop and report: never push to a name this brief does not give you.

Read `out/tw-v-report:report.md` first, whole: its census, its § Rows to file and its § Symptoms are
this brief's input.

## Why
The operator asked that the test rework leave the suite faster and smaller than it found it
(`7dce00a96`). tw-v cut 26 s and met the end RSS, but in its session the suite still ran 65.2 / 67.9 s
at its tip against 62.0 / 63.9 s at `7dce00a96`: ~3.6 s over. Its rows name production roots, each a
cost real runs pay too, so the gap closes where the cost is decided, never in a test:
- `shared/import_walk.py::import_closure` normalizes each module off its own parse
  (`hashing.py::normalized_tree`), beside `hashing.py::normalized_module`'s memo on the text: two
  mechanisms for one digest. A second `scripts/build_ts_types.py::derived_landing_key()` in one process
  re-normalizes 329 modules (2.8 s on one fact, ~3.4 s on every `build_ts_types.py` run, so on every
  gate's ts-types). Its other caller, `application/optimizer_manifest.py` (`"engine": import_closure(…)`),
  pays the same at run time.
- 4,235 fsynced atomic writes a suite (`store/io.py::_synced`, ~2.5 s real time), 1,048 of them
  `projections/live_dashboard.py::_write` rewriting `dashboard.json`. `infrastructure/CLAUDE.md`:
  "`dashboard.json` is a checkout of the fold, and nothing reads it back."
- Three child-process facts idle the parent ~8 s, serially (`conftest.py::dspy_child` 2.9 s, the
  API-start child 2.7 s, the stdio host 2.4 s), ~1.6 s of each importing `promptpotter.main` or the CLI.
  That is also every real CLI verb's start.

## Links
1. **One normalization.** `import_closure` reads the one memo every other digest reads, so a module's
   text is normalized once a process, whichever digest asks first. Re-test: a second
   `derived_landing_key()` in one process under 0.2 s; `build_ts_types.py`'s wall before → after.
   `built_key.py` regenerates; `behaviour_keys.json` must not move.
2. **A checkout nothing reads back.** First find why `dashboard.json` is written 1,048 times a suite
   (the debounce in `live_dashboard.py`, `drain` callers, a write that changes no byte). A write that
   changes nothing is not made. Then price fsync where it is decided: read what re-derives each
   fsynced file after a crash. A file that is the truth (a ledger, a job file, a survivor row of
   `store/data_root.py`) keeps its fsync. A file rebuilt from the truth on its next fold is still
   replaced atomically, so no reader sees a torn file, but needs no fsync. The decision sits in
   `store/io.py`'s doors, by what the file is, never by a flag a caller passes. Name it in the commit
   and in `infrastructure/CLAUDE.md` § Persistence, with a probe if a one-X claim results.
3. **The boot's import graph.** `python -X importtime -c "import promptpotter.main"` and the CLI
   module, ranked by cumulative time. If a heavy module (an extra, a surface) is loaded by a boot that
   never calls it, the cause is a module in the wrong place, and it is fixed by moving that module.
   Never a function-body import: the gate holds lazy imports to a falling baseline (roadmap § landed
   61–64). Re-test: each child fact's wall before → after. Skip this link if the ranking shows no such
   module, and say so with the ranking.
4. **Re-measure.** The three-point census (`7dce00a96`, `<BASE-V2>`, tip) in one session, interleaved,
   two runs each, as tw-v ran it.

A speed fix decides nothing. Equals-main must read every optimizer and leg UNMOVED, the digests must be
identical, and `promptpotter/assets/behaviour_keys.json` must be unchanged. No fact is cut, no run
shrunk, nothing installed. No cache keyed by object identity. `domain/CLAUDE.md` and each touched
layer's `CLAUDE.md` bind.

**Accepted when** the suite's wall is below `7dce00a96`'s in your session, and its end RSS is not above
`<BASE-V2>`'s. If the four links cannot close it, report the gap with the census and what each link
bought, and stop.

## Owns
Production: `shared/import_walk.py`, `shared/hashing.py`, `scripts/build_ts_types.py`,
`application/maintenance/built_key.py` (regenerated), `infrastructure/store/io.py`,
`infrastructure/projections/live_dashboard.py`, `infrastructure/CLAUDE.md`, and only for link 3 the
modules it moves, each named in its commit. Plus `tests/`, `scripts/offline_run.py`,
`scripts/offline_world.py`, and in `docs/specs/code-debt-cleanup.md` tw-v's four rows (file each one
this chain leaves open, restated with its measured cost; delete each one it closes). Never
`docs/specs/roadmap.md`.

## Proof
- Before → after (`7dce00a96`, `<BASE-V2>`, tip), suite and each touched test file: lines, tests,
  wall, the pytest process's RSS at start and end; `offline_run.py`'s wall and child max RSS; one
  session.
- Per link: its re-test before → after.
- Coverage diff (`sys.monitoring` PY_START, `<BASE-V2>` vs tip): every body the base ran and the tip
  does not is deleted code, restored, or named with its reason.
- Equals-main against `<BASE-V2>`: every optimizer and leg UNMOVED, digests identical.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-v2-report`.
