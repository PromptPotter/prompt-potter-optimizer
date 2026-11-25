# tw-0: the new test world: three tiers, one world, a charter, and test_spend's walk as the pattern (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override. The base is
**`origin/land/combine-loop` at `7dce00a96`**: main `5d150ac13` plus the measuring loop, L4, spend-tomb
and the web chain. Replace the ancestor check with `git merge-base --is-ancestor 5d150ac13 7dce00a96`.
Equals-main runs against `7dce00a96`'s offline home.

Push to `arc/tw-0`, one commit per link, with the full gate green at the tip.

**The operator** (2026-11-25), on the combine's +1,500 test lines: "that doesnt seem necessary, review and
if necessary roll over the old test-methodology … great lets ship extensive changes there too yes before
release now can completely rework everything. old was dumb."

This is the rework's backbone. Per-file chains (tw-1…) migrate the rest onto what you build, so the
world and the charter must carry them without you.

## What is wrong now (measured at `7dce00a96`)
- **Size.** `tests/` is 30,478 lines in 12 files for 638 tests (~48 lines a test). It includes
  test_spend 6,742, test_scoring 6,307, test_security 5,759, test_identity 5,152, test_archive 3,296 and
  factories.py 1,571 (57 builders).
- **Production is replayed by hand.** `seat(` 36/5, `_peer_cycle(` 16/1, `close_round(` 17/4,
  `loop_session(` 25/7 and `minted_campaign(` 82/6 over `tests/`, plus test_spend.py's `_round`/`_walk`
  harnesses with ~10 kwargs each.
- **Hand-written stand-ins for the engine.** The combine's race tests write five fake races
  (`_ReversingRace`, `_PerRoundRace`, `_ChoosingRace`, `_RollingRace`, `_CuttingRace`,
  test_spend.py:1384–1947). Through them they assert one invariant, "a press moves the bill, never the
  record", four times, once per shape. The real races (potter's `PoBBRace`, capo's `PairedTRace`,
  gepa's gate) are never what runs.
- **Memory** (a per-test trace of the pytest process: committed MB before and after each test):
  - The process starts at 133 MB after its imports and ends at 280 MB. State piles up file by file:
    identity +35, connectors +33, security +27, archive +25, spend +18, scoring +13.
  - `test_connectors.py::test_a_resent_dspy_call_spends_one_of_its_programs_max_calls` imports dspy
    in-process (+27 MB).
  - `test_security.py::test_the_api_starts_under_the_loop_guard…` adds +15 MB, and
    `test_identity.py::test_an_l4_override_moves_the_prompt_and_the_hash_of_every_preset` adds +14 MB
    in 13.6 s.
  - The gate's pytest job peaks at 374 MB (cap 408): this grown process plus a child process a late
    test spawns.
- **Time.** pytest takes 232–289 s in the gate (test_spend 73 s, test_security 66 s, test_scoring 52 s,
  test_identity 36 s).

## A reviewer's inventory (read-only, at `ba9016dca`; verify before you build on it)
**Classes over the whole tree:**

| class | tests | lines |
|---|---|---|
| walk | 59 | 2,404 |
| round replay | 53 | 2,410 |
| campaign | 78 | 3,101 |
| stores | 96 | 3,199 |
| pure | 355 | 9,021 |

Module helpers add 4,274 lines more. Keeping today's shapes it estimated −4,000 lines (13%). **The
operator asked for a complete rework, so that is the floor, not the target.**

**Why the press tests exist.** The offline world never presses, and its backend answers with no delay.
So the four press tests and the two cut-waste tests stand in for a leg the world lacks. Build that leg:
- `pressed`, one per optimizer: it dispatches `MAX_SPEED` (or the deterministic press you pick) as
  `pausing_in_walk` dispatches its pause;
- the backend yields a draw-keyed number of `sleep(0)`, so returns reorder.

Then fold the press tests into one host table or delete them.

**The resumed leg runs at `hardness=0`** (`offline_world.py:277`), so its ruler is cold and it never
exercises informative rows. Run it warm (`REBASE_HARDNESS`) with the trace asserting
`rows_ran == "informative"`. That retires the per_round pause/resume test (sp:1608) and
`test_a_resumed_informative_race_rechooses_its_rows_and_files_nothing` (ar:1408).

**Production roots the tests compensate for. Fix them at the root, in their own link:**
- `query_loop.py` computes one bound twice (`:568` vs `:1069`). `fill` must send `flight().allowed`,
  and the gauge test (sp:1844) goes.
- `test_identity.py:5008` pins a structural invariant. It becomes a check at schema load (charter §
  Structural invariants).

**Fake deciders.** `_PerRoundRace`, `_ChoosingRace`, `_RollingRace` and `_CuttingRace` each re-implement
a `row_policy.py::send_rows` shape. Where a loop test still needs a decider, use one `_Policy(knobs)`
over the real `send_rows`. The test_archive tests that seat a real race via `seat(` plus a patched
`DifficultyView.ruler` shrink to `send_rows`/`FieldRead` unit tables (`send_rows` is pure).

## The methodology to build (decide the details, keep the shape)
Three tiers, each owning one kind of fact:
1. **Rules.** A pure function's facts are table tests: rows of input → expected in one function per
   rule, no stores, no session, no event loop. Examples: `scoring/row_policy.py`,
   `scoring/race_protocol.py`, `domain/ruler.py`, `intelligence/rasch.py`, `shared/statistics.py`,
   schema builders, wording tables. A rule a test can only reach through a walk is a rule left
   unexposed: expose it in production (its own function), never a test seam.
2. **Journeys.** The loop's invariants over the REAL optimizers are offline-world legs in
   `scripts/offline_run.py`, which compares records and runs in the gate. These include press
   invariance, pause/resume replay, interrupts, wounds and heals, a cut wasting at most the press, and
   fork/rebase.
   - Add the press as a leg dimension (every optimizer at press 1 vs press N), so no pytest fake race
     stands in for one.
   - Its known noise: **A max-press resume replays on timing** (ledger) is unreproduced on 4 cores.
     Pick a press that is deterministic, or prove the root.
3. **Edges.** Store, archive, landing, reclaim, security and tenancy, the REST/CLI/MCP doors, and served
   shapes are tested through the production doors on ONE fixture world:
   - it mints through the real opening;
   - it runs a campaign through the real entry offline;
   - it releases what it built at teardown.

   `seat`, `_peer_cycle`, `close_round` and the hand-seated cycles go. `factories.py` keeps only
   builders of values a door cannot produce.

**Charter.** Rewrite `tests/CLAUDE.md` for the three tiers. Keep its admission rule (behaviour-coupled,
silent, unrecoverable) and its probes that still hold. State where each kind of fact lives, the
world's API, and the budget per test (lines, seconds, MB).

**Memory and time are part of the method.** An extra (dspy, …) is never imported into the suite's
process: it is tested where it runs, in an offline leg or a child process. The world's teardown leaves
the process as it found it (read models, caches). A child-process test runs early, or the parent is
small when it does.

## Links (suggested; your census may reorder)
1. The world and the charter: `tests/world.py` (or a name you justify), `tests/CLAUDE.md`, and
   `conftest.py`.
2. The offline press dimension, plus any journey legs the migration needs.
3. Migrate test_spend.py's walk/race/press section onto the tiers: rules → tables, journeys → legs,
   edges → the world. Delete the five fake races and the `_round`/`_walk` harnesses wherever no
   remaining test needs them.
4. Migrate test_spend.py's remainder, or stop at a clean seam and report it. test_spend.py is yours
   whole.

## Owns
`tests/conftest.py`, `tests/factories.py`, the new world module, `tests/CLAUDE.md`, `tests/test_spend.py`,
`scripts/offline_run.py`, `scripts/offline_world.py`, and the gate's pytest and offline-run check
entries in `scripts/gate.py` (their caps re-derived from your measured peaks, reason in the commit body).

A production function you expose for a rule table is yours where only tests and its own module read
it. Also yours: the two roots above, `application/scoring/query_loop.py`'s bound and the schema-load
check. The other test files are NOT yours. Re-point them only where a builder you delete forces it, and
name the hunks.

## Proof
Every number before → after:
- `tests/` lines, test count, and lines per test;
- pytest wall time;
- the pytest process's committed MB start/end, and the gate's pytest job peak;
- the offline-run check's time and peak.

**Coverage.** No pin is lost silently. For the function bodies of `promptpotter/` that pytest plus the
offline run execute at the base and at the tip (`offline_run.py --coverage`, plus a `sys.monitoring`
pass over pytest in your scratch), list every body the base executed and the tip does not. Each one is
deleted code, or restored with a test, or named with its reason.

**Equals-main against `7dce00a96`:** every optimizer UNMOVED (tests-only, plus any production rule you
exposed).

**Report:** follow common.md's FINAL REPORT on `out/tw-0-report`, plus the world's API in ≤20 lines for
the tw-1… briefs.
