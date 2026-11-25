# tw-m: what an app and its launcher hold is the instance's, released when it closes (cloud, beside tw-r)

`briefs/common.md` binds you (read BASE FIRST), with ONE override. The base is
**`origin/land/test-world` at `a591e6b87`**. Replace the ancestor check with
`git merge-base --is-ancestor 7dce00a96 a591e6b87`. Equals-main runs against `a591e6b87`'s offline home.

The base is RED on one known check: `offline_run.py` fails `capo-pobb/resumed`, a loop bug chain tw-r
owns. Your full gate is green except that row. Name it, and leave `behaviour_keys.json` to tw-r.

Push to `arc/tw-m`, one commit per link, with the full gate green at the tip.

**The operator** asked whether the test rework also brings performance and memory gains. tw-2's
per-test trace found that the gains sit in production. These are its rows, from `out/tw-2-report`.
Fix each at the root.

## The defects
1. **App lifespan state outlives shutdown.** `promptpotter/main.py:126` `lifespan` leaves about 23 MB
   held in-process after `TestClient(app)` exits. On the box, a process that opens and closes an app
   (a test, an embedded run) keeps it all.
   - Release what lifespan opened (server banner, reaper, notices, pricing refresh, startability,
     landing), or own it per app instance.
   - Measure committed MB before and after one open/close of the app, in a scratch script.
2. **The first in-process `TestClient(app)` costs +24.5 MB** (test_security.py:855). Its suspected
   root is the app import plus route mounting. Find what is imported or built that the request does
   not need, and defer or drop it. Report what you found, even when nothing is cut.
3. **The launcher's detached tasks are a process global.**
   `application/jobs/launcher/launch.py:27` `_DETACHED_TASKS`, which tests cancel by hand
   (test_security.py:4308). Key it to the app or loop that launched it, so closing that owner
   cancels its tasks.
4. **Poll intervals are module constants that tests patch to 0.** `infrastructure/llm/registry.py:53`
   `_QUEUE_POLL_S` and `host_surface.py:553` `_SETTLE_POLL_S`. Wake on the event that ends the wait,
   then delete the constants and the patches.
5. **The MCP door over HTTP costs +7.1 MB** (test_security.py:4871). Its suspected root is the SDK
   session managers. Release them with their session, or report why they must stay.

## Owns
`promptpotter/main.py`, `promptpotter/application/jobs/launcher/**`,
`promptpotter/infrastructure/llm/registry.py`, the module holding `host_surface.py`'s settle poll,
the MCP door module, and `tests/test_security.py`.

**NOT yours:** everything chain tw-r owns:
- the scoring, race, round and replay modules;
- `intelligence/indexes/sample.py`;
- `scripts/offline_run.py`, `offline_world.py` and `gate.py`;
- `tests/world.py` and `tests/test_spend.py`.

A test file other than test_security that patches a constant you delete is yours for that hunk only.
Name it.

## Proof
- Committed MB per open/close of the app, before → after (a scratch script, on Linux, with a stated
  method). The pytest process's committed MB start/end for test_security and for the whole suite.
- test_security wall time, before → after.
- Equals-main against `a591e6b87`: every optimizer UNMOVED.
- Full gate green.

**Report:** follow common.md's FINAL REPORT on `out/tw-m-report`. For each defect give the root, the
fix and its measured effect.
