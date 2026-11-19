# webd-2: a stream frame carries the contract; one address parser; no mock a shared graph misses (cloud)

`briefs/common.md` binds you; read `webapp/CLAUDE.md` and `webapp/lib/CLAUDE.md` first. Base:
`origin/main` at your start (it holds webd-1, "a read stops following once its answer is final"; if
not, stop and report). Rows in `docs/specs/code-debt-cleanup.md` § webd as they stand on
`origin/main`; check each at its line first; one that no longer names a defect is reported, not
built. One commit per link, each quoting its row's title; cut each row to what is left.

## Links
1. **The served contract has two hand copies**, its stream half only: SSE frames are cast with `as`
   (`live-stream.ts::openLiveStream`) and the build handshake (`X-Served-Contract`) covers fetches,
   not streams. Action (the row's): each frame carries the stamp (`application/cycle_reads.py::feed_frames`
   and the cycle tail), and the client parses a frame against its generated type and treats a stamp
   mismatch as a fetch's mismatch is treated. Leave the row's Delete half filed (its server side,
   `application/hierarchy.py`, is another chain's tonight). **Re-test:** `git grep -n " as " --
   webapp/lib/live-stream.ts` names no frame cast; a fact: a frame with another stamp reaches the
   same handling a mismatched fetch does.
2. **A bare campaign link says it opens nothing, then opens it**: the masthead banner and
   `openHash` read the address two ways. Action (the row's): one parser serves both. **Re-test:** the
   row's.
3. **A mock misses a module another test file loaded**: `webapp/vitest.config.ts:11` runs
   `isolate: false`. Action: the facts that `vi.mock` a module under `@/lib/hooks/*` or
   `@/lib/state/*` mount real providers instead; a lint rule in the webapp's own lint config (NOT
   `scripts/gate.py`, another chain's tonight) refuses such a `vi.mock`. **Re-test:** the rule's count
   is 0, and the suite's wall time before → after.

## Owns
`webapp/**` except `types.generated.ts` by hand (regenerate it via `scripts/build_ts_types.py` if a
served shape moves); on the server `promptpotter/application/cycle_reads.py` (`feed_frames` and the
cycle tail's frame only) and the generated OpenAPI. NOT `application/hierarchy.py`, NOT
`promptpotter/infrastructure/**`, `application/{jobs,commands,runner,scoring}/*`, `scripts/gate.py`,
`tests/**` except one fact for link 1's server half in a new file (store-2f lands beside you), NOT
`application/optimizers/potter/`.

## Target
Net ≤ 0 lines outside tests, or each added line named. Full `python scripts/gate.py` green. Name every
visual change.

## Report
Per `common.md`, with a Symptoms list.
