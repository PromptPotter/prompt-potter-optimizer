# tw-1…tw-4: rebuild one test file group in the three tiers (cloud, in parallel with tw-0)

`briefs/common.md` binds you (read BASE FIRST), with ONE override. The base is
**`origin/land/combine-loop` at `7dce00a96`** (main `5d150ac13` plus the measuring loop, L4, spend-tomb
and web). Replace the ancestor check with `git merge-base --is-ancestor 5d150ac13 7dce00a96`.
Equals-main runs against `7dce00a96`'s offline home.

Your dispatch line names your id. Push to `arc/<id>`, one commit per link, with the full gate green at
the tip.

**The operator** (2026-11-25): "can completely rework everything. old was dumb." This is not a trim.
Each fact is rebuilt in the tier that owns it, or deleted.

## Running beside tw-0
tw-0 builds the backbone at the same time, from the same base. Read **`briefs/tw-0.md`** in full:
- its § What is wrong now;
- its § A reviewer's inventory;
- its § The methodology to build, which is the three tiers you apply.

tw-0 owns, and you never edit: `tests/conftest.py`, `tests/factories.py`, its new world module,
`tests/CLAUDE.md`, `tests/test_spend.py`, `scripts/offline_run.py`, `scripts/offline_world.py` and
`scripts/gate.py`. It is rewriting the charter now, so the base's `tests/CLAUDE.md` binds you only for
its admission rule (behaviour-coupled, silent, unrecoverable) and its probes. tw-0.md's three tiers
govern the rest. I integrate tw-0 and the four of you afterwards.

## The method, per test in your files
1. **Census.** Classify each test: rule / journey / edge / delete. For a delete, name the reason:
   - it fails the admission rule;
   - it duplicates a pin elsewhere (name it);
   - it asserts a string or a name.
2. **Rules** become table tests over the pure function: one function per rule, its rows a `for`. No
   stores, no session, no event loop. A rule reachable only through a cycle is exposed in production
   as its own function, in its own link, and named in the report.
3. **Journeys** (a loop invariant over real optimizers) belong in offline legs, but `offline_run.py`
   is tw-0's.
   - An existing leg already pins the invariant (name the leg and the trace field that shows it):
     delete the test.
   - Otherwise, move the test unchanged into one block at the end of your file, headed
     `# Journeys awaiting a leg`. Write each one's leg spec in your report: optimizer, dimension,
     trace assertion. The integration pass makes them legs.
4. **Edges** go through the production doors: the real opening mints, the real entry runs offline on
   the `offline` fixture, and a door is the REST/CLI/MCP adapter itself.
   - Every `seat(`, `_peer_cycle(`, `close_round(`, hand-seated cycle, frozen file and
     `SimpleNamespace` in your files goes.
   - tw-0's shared world does not exist yet. Write the least setup your edges need as ONE module
     fixture in your file, built only from production doors and existing `factories.py` builders.
     Integration re-points it to tw-0's world, so build no second framework.
5. **A wrongness the old test compensated for** (two computations of one bound, a structural invariant
   pinned by a test): root-fix it in production, in its own link, if no other chain owns the module.
   Otherwise put a `code-debt-cleanup.md` row in your report (you do not edit the ledger).

**Memory and time are part of the method.** An extra (dspy, …) is never imported into the suite's
process; it is tested where it runs, in a child process. Setup you build is released at teardown (read
models, caches). A child-process test runs with a small parent.

## Ownership (disjoint from tw-0 and from each other)
| id | owns |
|---|---|
| tw-1 | `tests/test_scoring.py` |
| tw-2 | `tests/test_security.py` |
| tw-3 | `tests/test_identity.py` |
| tw-4 | `tests/test_archive.py`, `tests/test_ruler.py`, `tests/test_connectors.py`, `tests/test_world_edges.py`, `tests/test_stream_stamp.py` |

- Need a builder? Write it as a `_helper` in your module and list it as a candidate to promote. Stop
  using a factories builder? List it as a candidate to delete.
- **Production:** a rule you expose, or a root you fix, is yours only where no other chain's file reads
  it and tw-0's brief does not name it. tw-0 has `query_loop.py`'s bound and the schema-load check
  replacing `test_identity.py:5008`. Grep first and name the hunks. If two chains could touch one
  module, report it and do not edit it.

## Per-chain notes (measured at `7dce00a96`, pytest per-test trace)
- **tw-1, test_scoring** (6,307 lines, 116 tests, 52 s). Sections: scorer formulas, composite fitness,
  election, authored round, elimination, escalation/stop, paired readings, bare-answer backend.
  - §1, §2 and §7 are mostly rules, so they become tables.
  - §3, §5 and §6 replay rounds by hand (`seat`, `close_round`). They become journeys or edges.
  - Slowest tests:
    `test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed` (8.7 s) and
    `test_capo_races_block_after_block_while_more_than_mu_arms_stand_and_stops_at_mu` (4.5 s).
- **tw-2, test_security** (5,759 lines, 161 tests, 66 s, +27 MB). Sections: leaks/perimeter,
  delegation, unattended deletes, the command state machine, the host surface, API tokens.
  - Edge-heavy.
  - `test_the_api_starts_under_the_loop_guard_with_its_held_producer_lock` adds +15 MB.
  - `test_a_host_over_stdio_acts_in_the_terminals_account_as_the_in_process_door_does` takes 12.5 s,
    `test_one_refused_start_run_asked_in_process_is_the_value_every_door_renders` 8.0 s, and
    `test_an_edit_to_code_a_load_runs_lands_the_root_again` 7.7 s.
- **tw-3, test_identity** (5,152 lines, 128 tests, 36 s, +35 MB). Sections: hashes, and the config a
  campaign runs.
  - Hash facts are rules, so they become tables.
  - `test_an_l4_override_moves_the_prompt_and_the_hash_of_every_preset` takes 13.6 s and +14 MB.
    Find what it builds per preset.
  - The reviewer found literal shipped-config values asserted at :3023 (a rule-1 breach), and :3753
    duplicating :3705.
  - Leave :5008 to tw-0.
- **tw-4, archive + ruler + connectors + edges** (4,829 lines).
  - test_archive's seated races (`_informative_race`, `_decided`, `_on_ten`, a patched
    `DifficultyView.ruler`) become `row_policy.send_rows`/`FieldRead` tables; `send_rows` is pure.
  - ar:1408, the resumed informative race, is a journey: tw-0 is making the resumed leg warm.
  - test_ruler is mostly rules.
  - test_connectors' `test_a_resent_dspy_call_spends_one_of_its_programs_max_calls` imports dspy
    in-process (+27 MB). Move it to a child process.

## Proof
Every number before → after, for your files and for the whole suite:
- lines, test count, and lines per test;
- pytest wall time per file and total;
- the pytest process's committed MB start/end (a per-test hook in your scratch), and the gate's pytest
  job peak.

**Coverage diff** over your files' production modules (`sys.monitoring` in your scratch, base vs tip):
list every body the base executed and the tip does not. Each one is deleted code, or restored, or
named with its reason.

**Equals-main against `7dce00a96`:** every optimizer UNMOVED.

**Report:** follow common.md's FINAL REPORT on `out/<id>-report`, plus:
- the census table (test → tier/delete, reason);
- the journeys awaiting a leg, with their specs;
- the factories candidates to promote and to delete.
