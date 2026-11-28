# w11-tests3: area 14, round 3 — the gate is green on the desk it runs on; the world holds what tests seat (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 14 and the debt doc's `## 14` section (24 rows). Target:
- A gate verdict is the code's, never the desk's load.
- A test asks the world through a door and never seats what a door should make.
- The suite gets cheaper.
- A green rerun is a defect, never a pass.

Links, central first:
1. **The gate under load: one root, three rows.** "The bundle check times out under the gate's load"
   (now with three desk cases and a counter measurement), "Two wounded legs move under load", and
   "Vitest's start meets load from outside the gate lock".
   - The measurement: disk idle; available memory down to 685 MB, with paging bursts; Defender's
     `MsMpEng` at 1.15 cores on average, 2.6 at peak; CPU at 40%.
   - Root: `gate.py`'s schedule budgets `free_mb` once, at the start, and the e2e waits run on clocks.
   - Fix both halves:
     - The schedule reads available memory as each check starts, and holds a heavy check until it
       fits.
     - `webapp/e2e/harness.ts::drawn` and `fake_issuer.ts::waitForHealth` wait on what the server
       says it is doing, never on a bare timeout.
   - The bundle check's scratch index starts from the checkout's own (the row's own root).
   - Re-test: two full gates on a 4-core Linux box, each beside a stdlib Python process holding half
     its memory, both green. Install nothing.
2. **No door holds a world cycle mid-walk, a refused inner rebase or a raised mid-run ceiling.**
   - Add the world door that holds a cycle mid-walk; `RunPhaseRecord(Running())` hand seats go from
     7 to 0.
   - The inner stop rule is w11-runner's: if it has landed as a pure function, test it as a table;
     else leave the `execute_round` patch and name it.
3. **19 parked facts wait for a world door**, with "A knob layer's tiers have no world example", "No
   door yields a dataset with a moved panel" and "No offline leg draws a mixed-benchmark round". Add
   the doors in `scripts/offline_world.py` and `offline_run.py`'s leg table, cheapest first. Each fact
   moves off its seat; name every one still parked.
4. **The suite's slow facts:**
   - "A torn-bill fact takes 3.08 s".
   - "The suite runs ~3.6 s over `7dce00a96`".
   - "`tests/test_spend.py` holds the walk's tests".
   - "A shipped-data scan lives in test_identity".
   - Each fact sits in its subject's file at under 2 s.
5. **The gate's own rows:**
   - "`gate.py --changed` proves nothing for a committed link".
   - "The offline-run check is landing tier, so `--changed` misses a script break".
   - "The surface ledger measures against origin/main, not the chain's base".
   - "The trace's canonical labels move on a resume into the end pass".
   - "The offline world reports no cached_tokens".
6. **capo's skip twin moved once on the Windows desk** and **The capo offline bench turns on one
   seed**: pin what the leg does not pin.

Not now:
- "The paper harness tracks no named tree" is the paper's.
- "An operator's act is atomic in the offline world", "The offline world runs no launch a browser
  starts" and "A rule the suite reaches only through a round is decided where no table can reach it"
  ride with w11-runner and w11-jobs2. Leave each a one-line status.

OWNS: `scripts/gate.py`, `scripts/offline_world.py`, `scripts/offline_run.py`,
`scripts/complexity_ledger.py` (the measurement, not BASELINE values a landing sets), `webapp/e2e/**`,
`tests/**`. In flight beside you:
- w11-runner adds table facts in `tests/` only for its own pure functions.
- w11-spend, w11-scoring2, w11-jobs2 and w11-connectors2 each edit their own facts.
- You edit or add only facts no other chain names. On a shared test file, keep your hunks apart from
  theirs.

DONE WHEN:
- Link 1's re-test passes, and a full desk gate on the Windows box is green beside nothing else.
- `git grep -c "RunPhaseRecord(Running())" -- tests` is 0.
- The parked-fact count falls, by name.
- pytest wall is lower than base on the same box.
- Every leg is UNMOVED.
- The keys are named.
