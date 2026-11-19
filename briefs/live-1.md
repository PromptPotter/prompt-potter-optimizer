# live-1: a command's answer lands on the subject it was sent for; a closed tail reopens (cloud)

`briefs/common.md` binds you. Base: `origin/main` (61fff035c or later). The row is
`docs/specs/code-debt-cleanup.md` § webd, "The webapp's live state lands on the wrong subject or the
wrong version": build its two ✓ items only, quote the row's title in each commit, and cut the row
to what is left (never delete what you did not build). Read `webapp/CLAUDE.md` and
`webapp/lib/CLAUDE.md` first.

## Links
1. **`useCommand` lands its result on whatever subject is in view** (the row's first ✓):
   `webapp/lib/hooks/useCommand.ts` clears `failure` on a scope change at render (`:44-48`), but a
   send in flight across that change lands `then`, `setFailure` and `setPending` on the new subject
   (`:61-83`). Action: a send's answer is the scope's it was sent from; a moved scope drops it (an
   abort's shape, `failure: null`) and frees the slot. **Re-test:** a vitest `logic`/`dom` fact: a
   send started on scope A that settles after the scope moved to B leaves B's `failure`, `pending`
   and `then` untouched; one on an unmoved scope lands as today.
2. **The cycle-events `EventSource` never reopens after a refused reconnect** (the row's second ✓):
   `useCycleEvents.ts:43-44`'s `onerror` only sets `connected=false`, while `read-cache.ts:400-404`
   reopens a CLOSED feed on the reconnect beat: the rule is spelled twice. Action: one helper both
   live channels open through (a dropped stream reconnects itself; a CLOSED one reopens on
   `RECONNECT_POLL_MS`), the probe `new EventSource` ≤ 2 kept. **Re-test:** a fact: a tail whose
   stream reads CLOSED reopens after the beat and re-reads its snapshot.

## Owns
`webapp/lib/hooks/useCommand.ts`, `webapp/lib/hooks/useCycleEvents.ts`, `webapp/lib/read-cache.ts`
(its feed's open/reopen only), a new helper module under `webapp/lib/` if the root needs one, their
`__tests__`. NOT `promptpotter/`, `webapp/lib/api/types.generated.ts`, `webapp/lib/test-fixtures.ts`,
components (a component whose call the change breaks is reported).

## Target
Net ≤ 0 webapp lines outside tests. `python scripts/gate.py --web` green (typecheck, vitest, the
browser walk). No visual change: say so, or name the surface that renders differently.

## Report
Per `common.md`.
