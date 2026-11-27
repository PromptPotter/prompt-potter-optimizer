# w7-rows3: four small rows, each as its Door states (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-web2 the last). It is not on
main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7. Close these rows, one commit each, each built as its
**Door** or **Root** line states. Read each whole first. If a link needs a design the row does not
decide, stop that link, report it, and go on to the next.
1. **The browser re-derives liveness beside the served one**: the dashboard serves one liveness that
   covers a moment replay; `poll.tsx`'s `isLive` and `HardSamplesPreview.tsx`'s gate read it.
2. **Read params are not generated**: each `ReadParams` model's query goes into `types.generated.ts`
   through the generator `ts-types` runs, and `ActivityResponse.group_by`'s echo goes if nothing
   else reads it.
3. **One test over the 2 s budget**: `promptpotter mcp` (stdio) starts without importing the API's
   closure (`promptpotter.main`). Re-test: the stdio host test runs under 2 s; report
   `python -X importtime -m promptpotter mcp --help`'s top lines at base and tip.
4. The `--changed` half of **`walk-world`'s cache touch raises on a first run**: `gate.py --changed`
   on a clean tree runs no check, so it proves nothing for a committed link. Door: on a clean tree,
   `--changed` reads the diff against the upstream base (`origin/main`, or the branch's merge-base),
   and says which base it took. Re-test: on a clean branch one commit ahead, `--changed` lists that
   commit's checks.

OWNS: `webapp/lib/state/poll.tsx`, `HardSamplesPreview.tsx`, the dashboard's liveness field in
`served_dashboard.py` / `live_dashboard.py`, `scripts/build_ts_types.py` and the generated types, the
`mcp` CLI entry and its import path, `scripts/gate.py` (`--changed` only), and their tests. Not yours:
`tests/**` beyond the tests of these links (w7-tests rewrites the test world on land/w7-wipe; name
every test file you touch).

LEAN: `promptpotter/` shrinks or holds. Digests, behaviour keys and equals-main (every leg) are
unchanged. Run data: none.
