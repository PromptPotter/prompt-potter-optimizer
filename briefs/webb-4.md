# webb-4: the webapp's one-owner doors, one viewed searchpoint, the phone's hit areas (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `webb-4`, arc `arc/webb-4`.

- **Base:** `origin/main` at `1e0db24787dbfffc326fc65585d407faa79b7c3a` (layers landed). Your `<base sha>` in common.md is this commit.
- **The code shrinks, in the webapp too:** `promptpotter/` stays net ≤ 0 (you should touch at most `presentation/api/routers/reads.py`). Over `webapp/`, excluding `lib/api/types.generated.ts`, the target is ≤ −250. Run `git diff --shortstat <base> HEAD -- webapp ':!webapp/lib/api/types.generated.ts'` after each link. common.md's shrink rule applies to this number as well.
- **Spec:** the ledger section `### webb-4` of `docs/specs/code-debt-cleanup.md`, every row except two. The first row of `### mob-3` and one row of `### batch/32 · tests-w` are added. The lists follow.
  - **Not yours:**
    - "The output contract is flattened in the browser" (`domain/pipeline_schema.py` is in bkeys' Owns);
    - "A lift's name is composed twice" (readings serves `Estimate.text`).
  - **Also yours:**
    - mob-3's "A one-row toolbar's hit area overhangs its row vertically";
    - tests-w's "A connection note can take a touch walk's click".
- **Webapp rules:** `webapp/CLAUDE.md` binds you, and its states (I-labels), mobile-first and phone widths 375/393/412 come first. Read `design` notes only where a row changes what a person sees.

## Decisions (orchestrator; build them, do not reopen them)

- **U5, one viewed searchpoint.** No new read: a new read breaks R7. First survey which existing read already serves a picked arm's spec, and build on it. If none serves it, stop that row and file it with what each read lacks. The Leader always shows the leader. The pick renders once, where `ScoringInspector` stands. Hosts give identity only. Re-test: `searchpointCopyChoices(` appears once.
- **The sample set is one intent with one door.** "Open the picker seeded with X" is the intent. The hash holds the set, and `workspace.tsx::setViewKeys` is the only writer. The picker, `stepOverlap` and the trajectory seed call that door, and `setSelectionForSampleSet` goes. `FileTree::onSelect` stops being drilled: the tree reads the door where it stands.
- **The channel seed:** `picked ?? seed`, read and never written in render. The `seeded` flag goes.
- **`NodeConfigEditor` reads `useConnector` at an identity prop.** `NodeSchemaReading` stops being threaded through its 8 files.
- **The read state:** one owner per clause. A `/me` failure is reported once, by the cache. `state/auth.tsx` reacts to the cache's state and never re-reports, a 401 included. `couponRead` gets its validator. "Has measurements" reads the served `round_axis.unfiled`. Evidence is hashed in the build that serves it (`routers/reads.py::_hashed`), not after it. Re-test `spentTo` mid-round first; if it reads null, its one owner is the feed.
- **The evidence pulse goes.** The evidence read names its subject, and a settled course is never re-asked.
- **The title lint reads a template title's words**, and each of the 8 hits says its prose through `ui/Term` or `ui/HoverCard`.
- **The hit area:** the primitive reserves both overhangs, block and inline, so no one-row toolbar overflows its row. Re-test: `scrollHeight − clientHeight` is 0 for the Candidates header and the setup line at 375, 393 and 412.
- **The touch flake:** reproduce `walk/records.spec.ts`'s first test on `touch` under load first (`--repeat-each`, CPU-throttled). The fix depends on what you find:
  - if the note's box takes the click, make the note pointer-transparent over the content it marks stale;
  - if the walk clicks before the poll settles, the walk waits on the state the page reports.
  - Never add a retry or a timeout.

## Links (one commit each, in order)

1. The doors: the sample set's one door, the channel seed, the evidence pulse, the read-state clauses.
2. One viewed searchpoint (U5), and `NodeConfigEditor` at an identity prop.
3. The title lint and its 8 hits; the hit area; the touch flake.

## Beside you

**bkeys runs beside you** (`arc/bkeys`, on the same base). It owns the engine's identity code, `scripts/offline_run.py`, `scripts/gate.py`, `domain/pipeline_schema.py` and the L4 modules. The files you may share with it:
- `webapp/lib/api/types.generated.ts`: bkeys renames `Treatment.source`. Regenerate the file through its script, never hand-edit it, and touch no other line of it.
- `webapp/components/compare/ChannelDetail.tsx`: bkeys may edit its treatment row. Keep your hunks off that row.
- Touch no `scripts/` file and no `promptpotter/` file beyond `routers/reads.py`. If a row needs one, cut it into "rows to file".

## Proof (beyond common.md)

- `python scripts/gate.py --web` green at each link; the full gate once at the tip.
- Equals-main is UNMOVED on every leg. You change no engine decision; the evidence-hash clause must not move a request.
- Take phone shots at 375 of every surface a link changes, before and after. Commit them under `shots/` on the `out/webb-4-report` branch beside `report.md`, never on the arc, and name each in the report's appendix.
- Each row's re-test, before and after.
