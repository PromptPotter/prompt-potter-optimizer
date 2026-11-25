# tw-r: the three loop bugs tw-0's warm legs exposed, rooted, and every pin it dropped restored (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override. The base is
**`origin/land/test-world` at `a591e6b87`**: the combine `7dce00a96` plus tw-0…tw-4 integrated. Replace
the ancestor check with `git merge-base --is-ancestor 7dce00a96 a591e6b87`. Equals-main runs against
`a591e6b87`'s offline home.

The base is RED on purpose. `offline_run.py` fails `capo-pobb/resumed` (bug 1), so
`behaviour_keys.json` was not regenerated. Regenerate it once, at your green tip.

Checked at integration against `7dce00a96`'s home: `potter/l4` moves 4 of 5 requests, and only because
the outer prompt names the inner run `justlogic.d234__<treatment digest>`. tw-4's re-key moved that
digest. It is not a behaviour move. Prompt hygiene is tw-4's row 4; don't chase it here.

Push to `arc/tw-r`, one commit per link, with the full gate green at the tip. That tip is the first
green the test rework has.

**Why this blocks the release.** tw-0's warm, pressed and resumed legs
(`scripts/offline_run.py::read_warm`) turned three silent loop bugs into failures. They sit inside the
measuring loop being landed. The operator's rule: a loose end inside the mechanism being landed is
built before the landing.

## The bugs (from `out/tw-0-report`, rows 1–3; reproduce each before you fix it)
1. **A resume replays a cut without the catch-ups another arm filed.** No fold feeds
   `RaceCatchUpRecord` back into `raced`. `offline_run.py` is RED on `capo-pobb/resumed`,
   deterministically. The bug was already at `7dce00a96`; the cold resumed leg never reached it.
2. **An arrival-order walk, resumed, elects otherwise in round 2.**
   - `read_order` came in with the combine (`87cf6680a`, `9d6e18f93`).
   - Its pin was `test_a_rolling_arrival_race_reads_as_calls_return_and_a_resume_replays_that_order`
     (base test_spend.py:1780). tw-0 deleted it with the fake races.
3. **A press moves potter's L2 request**: `intelligence/indexes/sample.py::SampleIndex` folds the
   cells a press bought past a close. The invariant is **a press moves the bill, never the record**,
   and the next round's proposer request is the record's input.
   - Today `read_warm` prints a moved request as "Not a fail" (`asked`), so the leg hides the bug.
   - Make a moved request a FAIL first, then fix the root. Decide what the fold reads, and justify it
     in the commit body: cells a press bought past a close are banked spend, never evidence the record
     took.

For each bug: reproduce it on the leg, find the root (the layer that should make it true), fix it
there, and turn its leg green. Patches downstream of the root are refused (CLAUDE.md
`<root-fix>`).

## Pins tw-0 dropped that nothing else holds (restore each as a leg, a rule table, or a test)
- `test_a_rolling_race_decides_on_each_returned_row_and_cuts_before_its_slowest_call`
  (base sp:1714): under `each_read`, one row per decision, and a cut lands before the slowest call
  returns.
- base sp:1780: the arrival order is filed, and a resume replays that order (bug 2's guard).
- `test_a_row_a_race_takes_past_the_opening_budget_is_priced_and_bought_once_on_resume`
  (base sp:1925): a swapped-in row is priced, and bought once across a resume.
- `query_loop.py::_ScoringPhase.arrive` and `::filed_reads` ran at the base but not at the tip. They
  run again, or are deleted as dead code with the proof.

A leg dimension (rolling dispatch, arrival order) is preferred where the fact is "the record does not
move". A rule table goes where the fact is a pure decision.

## Also yours
- **The gate's offline-run verdict.** After a red full run, `gate.py --only offline-run` reran green in
  1.9 s (tw-4's symptom). Find whether a cache serves a verdict the tree no longer earns. If so,
  root-fix it: a red the gate cannot reproduce is a state the system can enter but not report.
- **Caps: leave them.** The cloud prints `-` for the job peak, because the gate meters a Windows job.
  I re-derive pytest's and offline-run's caps locally at your tip.

## Owns
- the race, replay, walk and round modules the three roots live in (`application/scoring/**`,
  `application/optimizers/*/race.py`, `application/runner/round.py`,
  `application/intelligence/indexes/sample.py`, and others your census names);
- `scripts/offline_run.py`, `scripts/offline_world.py`, `tests/world.py`, `tests/test_spend.py`, and
  `scripts/gate.py`'s offline-run entry.

**NOT yours:** `promptpotter/main.py`, `application/jobs/launcher/**`,
`infrastructure/llm/registry.py`, `host_surface.py` and `tests/test_security.py` (chain tw-m runs
beside you). The other test files are yours only where a fix forces a re-point; name those hunks.

## Proof
- `scripts/offline_run.py` run DIRECTLY (not through the gate's row) is green. Every warm, pressed and
  resumed leg is UNMOVED against its warm record, requests included.
- Equals-main against `a591e6b87` for the straight, crossed, healed, wounded, diverged and l4 legs:
  - UNMOVED where no fix reaches them;
  - where a fix moves one, name the leg and the decision, and say why the new one is right.
- The digest moves, the run data each fix reshapes (count it), and any wipe.
- Full gate green at the tip.

**Report:** follow common.md's FINAL REPORT on `out/tw-r-report`. For each bug give the root (file and
function), the fix, the leg that now proves it, and the red → green evidence.
