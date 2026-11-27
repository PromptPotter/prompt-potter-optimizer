# w8-doors: the test world's doors, root F's rest (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. Root F (one test world) is nearly closed
(`seat(` 10/3). Finish it, so that tests stop filing seat rows.

SPEC: `docs/specs/code-debt-cleanup.md`. Read each row whole, then close it as its Root says:
1. **One test world (root F)**: its remaining counts. Close the row when its re-test passes.
2. **`_peer_cycle` seats…**, **A test seat binds…**, **Hand-seats with no world door**, **The world
   has no in-process connector door**, **No door yields a recorded capability menu…**
3. **A test stands outside the harness** is already closed (`SimpleNamespace` 0, no `HeldRun` in
   `test_archive.py`). Delete it.

A test door opens through the product's own door (run init, the dispatcher), never a hand-built
record. A fact that needs a product door that does not exist yet is cut with one line under the
owning root's row (w8-shared), not hand-seated.

OWNS: `tests/factories.py`, the world (`tests/` fixtures and helpers), `scripts/offline_world.py`,
and the tests the rows name. Other chains add their own tests beside yours, so merge, never rewrite.

LEAN: `tests/` shrinks or holds. The test wall does not grow (report the cloud `pytest` seconds, base
→ tip). `promptpotter/` is untouched, apart from re-points.
