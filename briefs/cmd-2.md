# cmd-2: every command names the subject it acts on (cloud)

`briefs/common.md` binds you. Base: `origin/main` (a4dd6eaed or later, with live-1). The rows are
`docs/specs/code-debt-cleanup.md` § webd: "The webapp's live state lands on the wrong subject or the
wrong version" (its first clause only: the `useCommand` slots that pass no `scope`) and "A caller's
throwing `then` reads as the server's refusal". Read `webapp/CLAUDE.md` and `webapp/lib/CLAUDE.md`
first. Quote each row's title in its commit; cut the first row to what is left, delete the second.

## Links
1. **A slot's scope is required.** live-1 made an answer land only on the scope it was sent from,
   but `scope` is optional, and about 13 of 30 slots on main pass none (`git grep -n "useCommand<"
   -- webapp`): `ApplyScenarioPanel`, `LiveSegment`, `DatasetPickList`, `SteerForkPanel`,
   `VerifyAction` (both), `useRemoteModel`, `CampaignMenu`, `useCampaignForest`'s cleanup,
   `useRunControl`, and the account surfaces. Action: the type requires it. A subject-bound slot passes
   its subject's identity, read where the surface already reads its subject (the viewed address via
   `useWorkspace()`, a campaign or draft id it holds; never a second derivation). An account-level slot
   passes ONE exported account constant. The commit lists each slot → its scope. **Re-test:** the
   typecheck refuses a slot with no scope, and a `dom` fact: `useRunControl`'s pause sent on cycle A
   that settles after the view moved to B paints nothing on B.
2. **A caller's `then` is not the send.** `useCommand.ts::run` calls `then` inside the send's `try`,
   so a throw there lands as a `CommandFailure` with a server kind. Action: `then` runs after the
   send settled; its throw is reported as an incident (`reportIncident`) and never becomes a
   `failure`; `run` still never throws. **Re-test:** a `logic` fact: a throwing `then` leaves
   `failure` null and reports one incident.

## Owns
`webapp/lib/hooks/useCommand.ts`, every file holding a `useCommand<` call (listed above; components
and `webapp/lib/hooks/*`), their `__tests__`. NOT `promptpotter/`, `webapp/lib/api/types.generated.ts`,
`webapp/lib/test-fixtures.ts`, `webapp/lib/live-stream.ts`, `webapp/lib/read-cache.ts`.

## Target
Net ≤ 0 webapp lines outside tests, or each added line named. `python scripts/gate.py --web` green.
No visual change: say so, or name the surface that renders differently.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
