# feed-version-fix: the fix pass on feed-version's review (cloud · main)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/main` at the
sha the dispatch names**. First cherry-pick `1b67cb27a` (`origin/arc/feed-version`, built on
04359745b) onto it, resolving by hunk: in `docs/specs/code-debt-cleanup.md`, keep main's "Form fields
are skinned outside `base.css`" row and delete the live-state row ("The webapp's live state lands on
the wrong subject or the wrong version", which feed-version closes). Then add one commit per fix below.
Push to `arc/feed-version-fix` (wip to `wip/feed-version-fix`); report on `out/feed-version-fix-report`:
a `## Fix pass` section first, then feed-version's own report
(`git show origin/out/feed-version-report:report.md`) carried below it, its "Not pushed" line
dropped. The order is `briefs/feed-version.md`; the review is `briefs/feed-version-fix.review.md`, its
probe `briefs/feed-version-fix.probe.py` (adapt its paths). **The operator's standard: "we always want
to go to the full perfect shape at the root"**: every finding below is BUILT, none filed.

## Decided (the orchestrator), before the fixes
**Scope.** The writer-stamped version (the ledger row "The feed's version spans a whole store": every
writer stamps its subject's version, so no tick walks a store and an ack names what its write moved)
is a chain of its own, after this one. This pass makes feed-version's counter right as a version of a
SUBJECT: one count per subject, shared by every reader of it, from what the subject holds, never from a
reader's view of it. The review's "the ack names every subject" and `reads.ts::draftRead` following
bare `store:` ride with that row: add both, with their re-tests, to that row's text in the ledger.

## Fixes (numbers are the review's)
1. **A version belongs to its subject, never to whoever reads it.** Count each subject once, keyed
   by the subject's owner (the store's base dir; the job registry for `machine`), from
   caller-independent stats: for `machine`, the registry's own state (the job files, the landing, the
   account limit), never the caller's served JSON. A host and a delegate reading one subject share one
   count. Re-test: the review's probe (`feed-version-fix.probe.py`): host and delegate alternating with
   no write stay at one `n`; a write moves both to the same `n+1`. Show it red on the base rule.
2. **A queued draft write computes its value at its turn, from the served draft.** `mutateDraft`
   takes `(served) => patch`, evaluated after the previous write settles (the ack now resolves only
   once the draft read stands on that write, so the served draft holds every earlier write);
   `PoolPicker.toggle`, the pack chips (`NodeKnobsPanel.tsx`) and every sibling that builds a write
   from a read pass a function, never a fixed value. Re-test: two chip presses inside one ack window
   both land (a logic test; red on the base rule). This closes feed-version's own filed row ("A pool
   or pack chip pressed twice inside one write's ack…"): do not file it.
3. **The fix that closes the revert is tested; the guard that cannot fire goes.** Delete
   `read-cache.ts::behind` and its test (a second ordering mechanism beside supersede, unreachable with
   one serving process). Add a logic test: a draft edit acked while a draft read asked before the
   write is in flight; `phase.draft` never returns to the pre-write draft (red on base). Show
   `pools-checkin.spec.ts` under load (`--repeat-each 10`, the gate's workers, beside a full pytest
   run) at base AND at the tip, both counts in the report.
4. **The review's Lesser, each built:**
   - A filed command never answers 500 for the walk after it: the ack's versions are computed so a
     vanished campaign or sandbox reads as gone, never as a raise (re-test: delete a campaign between
     the write and the walk; the command answers its result).
   - The count's lifetime is its subjects': an entry for a subject no walk finds any more is dropped
     (re-test: a deleted campaign's subjects leave the table).
   - The stats are taken and counted under one lock, so one write moves `n` once (re-test: two
     concurrent observers around one write yield one move).
   - The read is the draft's one landing: `useIngestFlow.ts::runCheckin` and `openOrigin` stop
     landing a write's answer into `phase.draft`.
   - Double press: a press is pressable again only when the read it gates on stands past the write
     it filed (a queued launch's Start reads `machine`'s version, not only the run phase); test the
     press made after the ack settles.
   - Names: `SERVING_EPOCH` and `FeedVersion.epoch` hold a process's name, so they say so
     (`_PROCESS` said what it held); no collision with `useIngestFlow`'s `epoch`.
   - One stamping site in `commands.py` (`ingest_upload` and `_Dispatching.__call__` share it;
     `ingest_upload` loses the `job_registry` it took only for the stamp).
   - The ledger's `package_lines` comment says the raise the BASELINE takes, not the diff's raw net;
     `code-debt-cleanup.md`'s stale `reads.py:214-232` citation follows the code.

## Owns
What feed-version owned (`application/cycle_reads.py`, `application/read_params.py`, the feed route in
`presentation/api/routers/reads.py`, `presentation/api/routers/commands.py`'s ack, `webapp/lib/read-cache.ts`
and its hooks, `webapp/lib/api/{client,commands,reads}.ts`, `webapp/lib/hooks/useIngestFlow.ts`,
`webapp/components/pipeline/NodeKnobsPanel.tsx` and its siblings that build a write from a read,
`webapp/lib/CLAUDE.md`'s feed row), plus `application/machine_status.py`'s version input, their tests,
the generated specs and types (by their scripts). Do not touch `runner/`, `domain/l4/`, `jobs/mint.py`,
`application/optimizers/`, `assets/behaviour_keys.json`, the act surfaces (exhibit-acts' fix pass
follows you on these files' versions).

## Proof
Equals-main per `common.md` against the base: decisions, requests, behaviour keys and digests
UNMOVED; `SERVED_CONTRACT` and the generated types move: name old → new. Full gate green.
surface-ledger: hunt deletions (fix 3's `behind`, fix 4's second stamp); name the ceiling.
