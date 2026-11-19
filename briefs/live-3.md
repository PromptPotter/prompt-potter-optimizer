# live-3: the live-state row's last webapp clauses (cloud)

`briefs/common.md` binds you. Base: `origin/main` (ee0433a3b or later). The row is
`docs/specs/code-debt-cleanup.md` § webd, "The webapp's live state lands on the wrong subject or the
wrong version": build the three clauses below, quote the row's title in each commit, cut the row to
what is left. Read `webapp/CLAUDE.md` (I10, § State reset on prop change) and `webapp/lib/CLAUDE.md`
(§ Display-data sources, "No stitch") first. Check each clause at its line before building; one
that no longer names a defect is reported, not built.

## Links
1. **The lineage cleanup's ack is its campaign's.** `lib/hooks/useCampaignForest.ts:121`'s
   `cleanupAcked` is set by the cleanup's answer and never keyed by `campaignId`, so another
   campaign reads the cleanup as done. Action: store it with the campaign it was pressed on and
   compare at read time (live-2's `VerifyAction` shape). **Re-test:** a fact: an ack on A reads false
   on B.
2. **A round reads one source.** `lib/hooks/useRound.ts:40-46` takes an unfiled round's arms from
   `dashboard.json` (`liveCandidates(dash)`), a filed one's from the round read, and its rows from the
   dashboard in both cases: a client stitch of in-flight and history ("No stitch": where two are one
   series the SERVER merges). Action: say which source each field should ride per
   `lib/CLAUDE.md`'s table; if the root is a served merge, build it in the round read's server
   module and the hook reads it alone. **Re-test:** the hook reads one descriptor per field class,
   and a fact on a round that files mid-view keeps its arms.
3. **Time-ray pages meet the head.** Earlier pages ride a fixed cursor (`lib/api/reads.ts:603-626`),
   so they are not contiguous with the head once it moves. Action: a page's cursor is the served
   one it continues from. **Re-test:** a `logic` fact: head + one earlier page, the head moves, the
   next page leaves no gap and no repeat.

## Owns
`webapp/lib/hooks/{useCampaignForest,useRound}.ts`, `webapp/lib/api/reads.ts` (the time-ray
descriptors only), the time-ray and round components that read them, their `__tests__`, and on
the server only the round read's and the time-ray read's modules if a link's root is there (name
them in the commit). NOT `promptpotter/infrastructure/store/**`, `application/jobs/*`,
`application/commands/*`, `application/runner/*`, `application/pipeline_resolve.py` (store-2 runs
beside you), NOT `application/optimizers/potter/`, NOT `webapp/lib/api/types.generated.ts` by hand
(regenerate it if a served shape moves).

## Target
Net ≤ 0 lines outside tests, or each added line named. `python scripts/gate.py` green (web, and py
if a server module moved). No visual change: say so, or name it.

## Report
Per `common.md`, with a Symptoms list for anything off-row you saw.
