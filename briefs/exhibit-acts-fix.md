# exhibit-acts-fix: the fix pass on exhibit-acts' review (cloud · main)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/main` at the
sha the dispatch names** (it holds `feed-version`, which reshaped the read versions). First
cherry-pick `af3ba3378` (`origin/arc/exhibit-acts`, built on 04359745b) onto it, resolving by hunk
(the batch/29 · tv-2 row in the ledger stays until fix 1 closes it; a version hunk takes main's
`feed-version` shape). Then add one commit per fix below. Push to `arc/exhibit-acts-fix`; report on
`out/exhibit-acts-fix-report`: a `## Fix pass` section first, then exhibit-acts' own report
(`git show origin/out/exhibit-acts-report:report.md`) carried below it. The order is
`briefs/exhibit-acts.md`; the review is `briefs/exhibit-acts-fix.review.md`, its probes
`briefs/exhibit-acts-fix.probes.md` (adapt their paths). **The operator's standard: "we always want
to go to the full perfect shape at the root"**: every finding below is BUILT, none filed.

## Decided (the orchestrator), before the fixes
**One mechanism (the review's L1).** Every offer the server computes is filtered by
`filable(stores.identity)` WHERE IT IS BUILT, so a REST or MCP reader is never served an act it
cannot file (entry-point parity): `skip_refusal`, `facts.verify`, `bench.status.offered`,
`deletable_stubs`' offer, `ActivityDecision.actions`, beside `RunAdmission` and `CommandReading`.
The reader's kinds are served ONCE per read (not on every entry, the review's L2) and read by the
webapp only for an act no read offers (limits, lookahead, apply-scenario, steer-fork, bank-snippet),
through ONE hook keyed by the subject the act files on (L3). Count the client joins before/after.

## Fixes (numbers are the review's)
1. **The compare pane's acts read the subject they file on**: Verify and Steer-fork (and every act
   the one hook serves) read the kinds of the read their `path` came from, never the viewed cycle's.
   Closes the tv-2 row (delete it then). Re-test: a follower with an exhibit channel and an own
   channel in compare, either one viewed: the exhibit channel offers neither act, the own one both.
2. **A read's version covers its reader's capabilities**: a grant or scope change moves the version
   the ETag AND the feed name (one version function in `application/`, on `feed-version`'s shape),
   so no 304 serves another reader's acts. Re-test: the review's `exa_probe2.py` gives the follower
   and the host different versions; a `/grant` change moves the version with no store moving.
3. **The backend test can fail**: stand the exhibit's cycle where a verb is offered (RUNNING →
   `pause`, DETACHED with a readable definition → `start`); the host is offered it, the follower is
   `offers is None` with `read_only`; one `read_command` case with a scoped reader. Show each test red
   on the base rule.
4. **The scenario readout stays**: `ApplyScenarioPanel` gates its button only.
5. **`read_only` never swallows a remedy the reader may file**: the review's probed rule
   (`proposed_rule.py`): `read_only` stands only where the run asks something and the reader files
   none of it; otherwise the run's own refusal stands, its `next_acts` through `offered`.
6. **The stop's acts are served**: `RunAdmission` serves its offered stop acts (the `acts` it computes);
   `run-phase.ts::stopNote` / `runNextStep` read them; the client `STOP_REASON_INFO[...].next_acts`
   filter and `launch_refusal` go.
7. **`RunLimitsControl` reads the served fact itself**; its `files` prop and the two test edits that
   only add it go.
8. **L4 + L5, the delegate surfaces**: `CampaignMenu`, `AccountUsageTab`, `ArchiveCompactionControl`
   and the account tabs read the reader's kinds (the one hook, or `/auth/me`'s capabilities: fewest
   spellings, say which); MCP `list_tools` (`presentation/mcp/server.py`) lists only what
   `filable(session identity)` holds. Re-test: a `/grant` delegate without `lifecycle` sees no
   rename/archive/delete; a scoped bearer's `list_tools` names only its kinds.
9. **L6 + the review's Symptoms**: `filable`'s docstring says what it is (the map; `_rung`'s cancel
   rung and `pressed_at` outside it); `offered` and the now-public `holding` get names no other
   module holds and that say what they build; `OWNER_FILABLE` is generated (the kind list into
   `types.generated.ts`), not hand-copied; `hierarchy.py`'s `info.action in ("resume", "start")`
   reads `RUN_VERB_KIND`; the dead payload filter and the loop-invariant `SUBMIT_ARMS in kinds` go;
   reader-voiced copy for a reader who cannot answer (`activity.py`, `run_held`, `_READ_ONLY`'s
   "its owner's", false for a delegate); `PromptFieldsEditor`'s bank waits on no second read.

## Owns
What exhibit-acts owned, plus `application/hierarchy.py`'s version function and its feed reader in
`application/cycle_reads.py`, `served_dashboard.py`, `hierarchy_subtree.py`, `domain/activity.py`,
`presentation/mcp/server.py`, `host_surface.py`, `webapp/lib/derivations/run-phase.ts`, the compare
pane (`components/compare/`), `components/candidates/`, `CampaignMenu.tsx`, the account tabs,
`ArchiveCompactionControl.tsx`, their tests, the generated specs and types (by their scripts).
Beside you, the orchestrator combines W1 on `land/w1` (keys, mint, address files): none of yours.

## Proof
Equals-main per `common.md` against the base: decisions, requests, behaviour keys and digests
UNMOVED; `SERVED_CONTRACT` and the generated types move: name old → new. Full gate green.
surface-ledger: hunt deletions (fixes 6, 7 and the L1 count delete); name the ceiling.
