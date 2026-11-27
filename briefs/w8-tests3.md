# w8-tests3: the offline world's doors, then the parked facts (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-tests2 (Sonnet) stopped without a link,
because every row's door lives in `scripts/offline_run.py`, which its Owns left out. Its census is
on `origin/out/w8-tests2-report`: read it first. You own the offline world now.

SPEC: `docs/specs/code-debt-cleanup.md`. Build each door, then move its facts. One commit per door
together with the facts it frees:
1. `run_one` knobs in `scripts/offline_run.py` for the doors the **23 parked facts** row lists:
   budget-binding leg, pause during a bench pass, `origin_replicates`, `every_adoption`,
   `arm_gate=author`, multi-arm GEPA, `stand_on`. A knob changes no existing leg: equals-main stays
   53 unmoved. Move each fact a door frees, and name the ones left parked.
2. **`tests/test_spend.py` holds the walk's tests**: an offline leg with a skip knob plus a resumed
   twin, so `_walk` (test_spend.py:871) stops hand-driving `query_loop.run_walks`.
3. **A child process has no offline-world door** and **Child-process facts idle the parent ~8 s**:
   one child entry in `scripts/offline_world.py`, so the two `_OFFLINE_CHILD` sites go.
4. **One guard is tested once per door**: one parametrized fact across the doors.
5. **A knob layer's tiers have no world example**, **An operator's act is atomic in the offline
   world**, and **test_security's stdio-host test is 78 lines**, as far as each root allows.

OWNS: `scripts/offline_run.py`, `scripts/offline_world.py`, `tests/conftest.py`, the test files the
rows name, and `tests/factories.py` (hub: your helpers only). Other chains add their own tests
beside yours, so never rewrite another chain's test.

LEAN: `tests/` shrinks. The pytest wall does not grow: report the cloud seconds, base → tip.
`promptpotter/` is untouched (if a door needs product code, name the row and cut it). Equals-main
53 unmoved.
