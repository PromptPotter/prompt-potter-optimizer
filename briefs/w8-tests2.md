# w8-tests2: the test world's tail (cloud · Sonnet)

`briefs/common.md` and `briefs/w8-shared.md` bind you. Batch 3 runs beside batch 2's five chains.
Every other chain adds tests too, so add or move tests only for the rows below, and never rewrite
another chain's test.

SPEC: `docs/specs/code-debt-cleanup.md`. Close each row as its Root says, one commit per row. If a
row needs a product change outside `tests/`, `scripts/offline_world.py` and `conftest.py`, cut it
with one line under its owner's row and go on:
- **23 parked facts wait for a world door**: move each fact whose door now exists (w8-doors opened
  `World.opened(connector=)`). The others stay parked and are named.
- **`tests/test_spend.py` holds the walk's tests**: move them to the walk's own test file.
- **test_security's stdio-host test is 78 lines**: bring it under the 60-line budget.
- **One guard is tested once per door**: one parametrized fact across the doors.
- **Child-process facts idle the parent ~8 s** and **A child process has no offline-world door**.
- **An operator's act is atomic in the offline world**.
- **A knob layer's tiers have no world example**.
- **Smells the test-shape split left**: the rows inside it that are tests-only.

OWNS: `tests/` (the rows above), `tests/conftest.py`, `scripts/offline_world.py`.

LEAN: `tests/` shrinks. The pytest wall does not grow: report the cloud seconds, base → tip.
`promptpotter/` is untouched.
