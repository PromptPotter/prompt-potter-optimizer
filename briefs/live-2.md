# live-2: a surface's subject state is keyed by its address; an anonymous page opens no feed (cloud)

`briefs/common.md` binds you. Base: `origin/main` (96d78d24a or later, with cmd-2). The row is
`docs/specs/code-debt-cleanup.md` § webd, "The webapp's live state lands on the wrong subject or the
wrong version": build the three items below only, quote the row's title in each commit, and cut the
row to what is left. Read `webapp/CLAUDE.md` (§ State reset on prop change, I5) and
`webapp/lib/CLAUDE.md` first. Check each item at its line before building; one that no longer reads
as stated is reported, not built.

## Links
1. **Subject state is the subject's.** `components/searchpoint/VerifyAction.tsx:48` holds `acting`
   and `saturated` across a changed `path` (they reset on `candidate.arm` alone), so its stop and
   saturated note paint on another cycle; `components/candidates/ApplyScenarioPanel.tsx:21`'s `done`
   survives a moved `path`. Grep every surface that holds state set by a command's answer and
   reset it in the render phase on its address (never a `useEffect`), the shape cmd-2 gave
   `useRunControl`'s `pausedAt`. **Re-test:** a `dom` fact per surface: state set on A paints nothing
   after the view moved to B.
2. **The feed opens for a signed-in account only.** `read-cache.ts::subscribe` calls `openFeed()`
   for any subscription, so an `anon` page asks `GET /feed`, is refused, and (via
   `live-stream.ts::openLiveStream`) reopens it on every `RECONNECT_POLL_MS`: an auth-gated request
   on a clock, against I5. Action: the feed opens where the session is known signed in, one place,
   and closes on sign-out. **Re-test:** a `logic` fact: an anonymous subscription opens no
   `EventSource`; signing in opens one.
3. **`dropCachedRead` with subscribers** (`read-cache.ts::dropCachedRead`, caller
   `useIngestFlow.ts:373`): the row says it runs while the read has subscribers; on main it already
   empties the entry and notifies. Report whether the item still names a defect (a subscriber that
   paints an empty frame or loses its answer); build it only if so.

## Owns
`webapp/components/searchpoint/VerifyAction.tsx`, `webapp/components/candidates/ApplyScenarioPanel.tsx`,
any other component link 1's grep finds, `webapp/lib/read-cache.ts` (the feed's open/close and
`dropCachedRead` only), `webapp/lib/state/auth*`, their `__tests__`. NOT `promptpotter/`,
`webapp/lib/hooks/useCommand.ts`, `webapp/lib/live-stream.ts`, `webapp/lib/api/types.generated.ts`,
`webapp/lib/test-fixtures.ts`.

## Target
Net ≤ 0 webapp lines outside tests, or each added line named. `python scripts/gate.py --web` green
(the browser walk included: an anon walk must still render). No visual change: say so, or name it.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
