# w11-tests4: area 14, round 4: the world launches what a browser starts (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 14 and the debt doc's `## 14` section. Both product walks stopped at the same wall: the offline world
runs no launch a browser starts, so nobody can watch a round live, pause, resume or fork on a phone
before a paid run. This chain removes that wall.

Target: every act an operator presses in the browser runs in the offline world, through its door, and
the trace names each decision truly.

Links, deletion first. Work each row along its root:
1. **The offline world runs no launch a browser starts**. Do this first; it unblocks both walks. A
   check-in Start in `webapp/e2e/serve_app.py`'s world mints, launches and walks the round offline.
   Re-test it with the walk's own route: Start, watch the live indicator, pause, resume, fork.
2. **The offline world answers no check-in resolver turn** (w11-walk2). It rides on the same world door.
3. **No door holds a world cycle mid-walk, a refused inner rebase or a raised mid-run ceiling**. Once
   link 1 exists, a world cycle holds mid-walk, and the 9 `state=Running()` seats go.
4. **The walk still counts fetches beside the pane's `aria-busy`**. `drawn` keeps only its `aria-busy`
   check.
5. The trace rows: **The offline trace reads a dead path and orders inner campaigns by their JSON**
   and **The trace's canonical labels move on a resume into the end pass**. Every trace path names a
   live field, and a set is keyed by identity.
6. **The build stamp hashes from an empty index**. The stamp seeds from the checkout's index.

Leave every other area 14 row open, and give each a one-line status in the report: still open, closed at
base, or moot.

OWNS: `scripts/offline_world.py`, `scripts/offline_run.py`, `webapp/e2e/**`, `webapp/vite.config.ts`,
`tests/**` for the seats link 3 retires, and the world doors. You own the inner-campaign sort at
`offline_run.py:1148`: key it by the inner cell's identity. w11-l4 comes after you and keys the inner
campaign's id by content. Other chains in flight edit their own tests, so keep your `tests/` edits to
the seats you retire.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- One walk at 390×844 starts, watches, pauses, resumes and forks a run in the offline world: list its
  screenshots under `out/w11-tests4-report`.
- Equals-main names each move, and the keys are named.
- `python scripts/gate.py` is green.
