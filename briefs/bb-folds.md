# bb-folds: measure N7's premise, read-only (cloud · Opus)

`briefs/common.md` binds you for ENV FIRST, ECONOMY and the report. This is a **measurement pass: no code
lands.** Push nothing but `out/bb-folds-report` (the report, plus any probe script under `probes/` on that
branch). Read `briefs/bb-shared.md` for BASE. Spec: `briefs/backbone.md` § 10 item 1, and § 4 row **N7**
with § 5 V9.

The claim to test: about eight classes each walk a cycle's ledger and rebuild "the rounds of this
cycle", and they compute the same rounds, so one pure fold (`CycleState`) can serve them all and every
derived file stays byte-identical. The classes are `_StandingFold`, `CycleFactsFold`,
`LiveDashboardProjection`, `ReadoutProjection`, `RoundAudits`, `_RayTail`, `ActivityFeed` and
`RoundFileProjection`. `RoundEnteredRecord` is handled in six places (V9). Nobody has diffed their
outputs.

Measure:
1. Run the offline world (`python scripts/offline_run.py`, `PROMPTPOTTER_HOME=/tmp/h`) at your base.
2. For every cycle it leaves, have a probe drive each fold over the same ledger and extract, per round:
   its index, its members (search points), its election (winner and pair) if any, its status, and its
   cost and cell counts, wherever the fold holds them. A fold that holds no round notion is named as
   such.
3. Diff the folds pairwise per cycle. Report a table: fold × fact, each cell "same as the reference" or
   the first differing cycle and round. Take `_StandingFold` (or whichever the code makes canonical) as
   the reference, and name your choice.
4. Hash the derived files the offline run writes (`index.json`, `dashboard.json`, the round files,
   `readout.log`) and report which of them are pure functions of the ledger. Rerun the offline world in
   a fresh home and compare the hashes. A file that differs between two runs of the same tree is not
   byte-stable, whatever the fold.
5. For each difference, name its root by `file:line`: a fold reading a record the others skip, a
   different notion of a round's end, a timing or clock read.

Verdict: one of
- **N7b can collapse the folds on its own landing**: all agree, and the derived files are stable.
- **These differences ride W**: they would change a derived file's bytes.
- **The premise is false for fold X, because …**

No paid LLM call, no edit to any committed file, no push but `out/bb-folds-report`.
