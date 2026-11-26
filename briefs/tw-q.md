# tw-q: a world run costs what a campaign costs, so the suite's wall is production's (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/tw-xjp` at
`<BASE-Q>` = `90986d76e`. That is `land/test-world`'s `af224d3bd` plus tw-x, tw-j and tw-p, integrated,
with two fixes of mine: `pricing.py::load_rates` holds a home as its rows apart from the floor, and the
offline run plans every leg's landing in one process (`offline_run.py::proven_homes`). Read
`out/tw-p-report:report.md` first. Replace the ancestor check with `git merge-base --is-ancestor 9cd30b5a7 <BASE-Q>`. Equals-main runs
against `<BASE-Q>`'s offline home. Push to `arc/tw-q`, one commit per link, with the full gate green at
the tip.

## Why
The operator asked whether the test rework would also improve the suite's run time and memory. tw-p met
the memory and missed the wall. In its session the end RSS went 344 (`7dce00a96`) → 364 → 331 MB, but
the wall went 82.3 → 116.4 → 109.0 s. tw-p puts the excess on two things:
- **The cold start, ~4 s.** `application/maintenance/landing.py::landing_key` (`functools.cache` per
  process) hashes every row's schema and the code of every module a load runs, at every entry that
  lands. `docs/specs/code-debt-cleanup.md` files it: +20.5 MB committed, 2.6 s per entry, an API's
  lifespan among them.
- **The shared world runs, each a real campaign** (tw-p § Appendix B, each paid by its first reader):
  `line` 8.9 s with the cold start, `head_to_head` 7.6 s, `steered_line` 5.1 s, `origin_forks` 3.8 s,
  `displaced` 2.7 s, `family_tree` 2.6 s, `gated` 2.2 s, plus two facts running their own campaigns.

**Your base fails the operator's desk gate on memory alone**, as tw-p's did: all 591 tests pass, but the
pytest job (the parent and every child it spawns, metered together) peaks at 404 MB against its 393 MB
cap. At `7dce00a96` it peaked at 373. I traced it on the desk. The parent peaks at 266 MB committed,
and the job's peak is one child: `test_security.py::test_the_api_starts_under_the_loop_guard_with_its_held_producer_lock`
starts the API in a process of about 167 MB while the parent holds about 237. An API's entry pays
`landing_key`'s +20.5 MB, so link 1 is the root here too. The cap is not raised and no test is
reordered to dodge it. tw-p already did that once: `tests/conftest.py::pytest_collection_modifyitems`
runs the dspy child's facts first, while the parent is small. At your tip, measure with and without it
the child's peak RSS and the parent's RSS while the child runs, and delete the sort unless the child's
own footprint is the floor (dspy's import), which you then state. Your cloud gate prints `-` for job peaks, so measure that API child's peak RSS
and the parent's at `<BASE-Q>` and your tip; I check the cap on the desk.

A world run is production code running a small campaign in process. Its seconds are what every real run
init, resume and API entry pays, so the root is in production, never in the suite's shape: a smaller run
that hides a slow production path is not this chain.

## Links
1. **The landing key is settled at build.** Per the code-debt row: a generated module that a gate check
   regenerates, as `presentation/api/served_contract.py` is, so no process hashes at entry. State the
   seconds and the committed MB it removes at an API's first open, at a CLI entry, and in the suite.
   Delete the row.
2. **Profile each shared world run on its first reader**, cProfile or a sampler, with production frames
   only. Per run, its top three production frames by cumulative time. Fix each one at its owner: a fold
   re-read per call, a revalidating `model_copy`, a file re-parsed, a registry re-walked. One commit per
   owner, and each body names the seconds it removes on the suite and on `scripts/offline_run.py`'s wall,
   same machine. A frame you leave gets one line saying why.
3. **No shape change buys time.** Rows, rounds and seeds per run stay as tw-p left them (`tests/CLAUDE.md`
   § The world). A run you find oversized is a row, with the fact that sized it.

**Accepted when** the suite's wall is below `7dce00a96`'s and its end RSS below `7dce00a96`'s, both
measured in YOUR session across the three points (`7dce00a96`, `<BASE-Q>`, tip). If links 1 and 2 cannot
reach it, report the gap with every frame you measured, ranked, and stop. Never cut a fact to close it.

## Owns
Production (`promptpotter/**`), plus `tests/conftest.py` and `tests/CLAUDE.md` § The world for the
costs you state.

**NOT yours:** chain tw-w runs beside you (`briefs/tw-w.md`). It owns `scripts/offline_world.py`,
`scripts/offline_run.py`, `tests/world.py`, `promptpotter/assets/behaviour_keys.json`, and every test
hunk that clears `route_order` or hand-prices a usage. A behaviour key or digest your change moves is a red flag: a speed
fix decides nothing, so equals-main must read every leg UNMOVED and the digests identical.

## Proof
- The three-point before → after, for the suite and per test file: wall, and the pytest process's RSS at
  start and end. `scripts/offline_run.py`'s wall and child max RSS at `<BASE-Q>` and the tip.
- Per link 2 fix, the profile line before and after.
- Equals-main against `<BASE-Q>`: every optimizer and leg UNMOVED, digests identical.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-q-report`.
