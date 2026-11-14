# lw: every webapp module lives by what it is; one storage-key table; two phone fixes (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `lw`, arc `arc/lw`.

- **Base:** `origin/main` at `f589cbe9a` (narr-1, small-1, webb-4, bkeys and faults-w landed). Your `<base sha>` in common.md is this commit.
- **The code shrinks:** over `webapp/` (excluding `lib/api/types.generated.ts`), net ≤ −50. A move pays for itself in deleted re-exports, barrel hops and duplicated imports. Run `git diff --shortstat <base> HEAD -- webapp ':!webapp/lib/api/types.generated.ts'` after each link. `promptpotter/` is untouched.
- **Spec:**
  - `### batch/27 · lw`, every row. webb-4 has landed its `NodeSchemaReading` row, so the barrel row is unblocked.
  - From `### webb-5`: "A ChannelMap deselect snaps back to the seed" (reported: reproduce it first).
  - From `### mob-3`: "Compare at 375 floats the remote bar above the tab bar".

  Every other row stays filed. Re-run each row's Re-test first.
- **Webapp rules:** `webapp/CLAUDE.md` and `webapp/lib/CLAUDE.md` bind you. Its states (I-labels), mobile-first and the phone widths 375/393/412 come first.

## Decisions (orchestrator; build them, do not reopen them)

- **The moves follow `webapp/lib/CLAUDE.md`'s "a module lives by what it IS".**
  - `lineage`, `poll`, `workspace`, `scoring-mask` and `chat/thread` go to `state/`.
  - `cell-means` and `prompt-fields` go to `derivations/`.
  - The chrome that holds a surface (`shell/searchpoint/`, `shell/scoring/`, `shell/node-surface/`, and the root `PairedLift` and `SummaryBlock`) moves to its surface's folder.
  - The surface holding chrome (`dashboard/pipeline/`) moves to its chrome folder.

  Each move re-points every import, `CLAUDE.md` row and doc citation in the same commit. No re-export stub stays behind.
- **The derivations barrel reaches no network layer.** `nodeSchemaReading` takes the read's body, not the `ReadResult`.
- **Every deep import of `derivations/hierarchy` goes through the barrel**, since the cycle that justified bypassing it is gone.
- **One storage-key table, every key prefixed `promptpotter.`.** No reader of the old keys stays, because a viewer's stored conveniences are not survivors.
- **A deselect is a pick of nothing, held apart from no pick.** Clearing a channel shows none, never the seed again. If it does not reproduce, name it and cut the row.
- **Compare's remote bar sits flush with the tab bar** at 375, 393 and 412.

## Links (one commit each, in order)

1. The moves; the barrel; the deep imports.
2. The storage-key table; the deselect; the remote bar.

## Beside you

- **rec** and **rn-conn** (cloud, same base) are engine-only and may regenerate `webapp/lib/api/types.generated.ts`. Never hand-edit it; regenerate it through its script after a rebase, if one is needed.
- Touch no `promptpotter/` file and no `scripts/` file. A row that needs one is cut into "rows to file".

## Proof (beyond common.md)

- `python scripts/gate.py --web` green at each link; the full gate once at the tip.
- Equals-main UNMOVED on every leg.
- `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- Take phone shots at 375 of every surface a link changes, before and after. Commit them under `shots/` on `out/lw-report`, beside `report.md`.
- Each row's re-test, before and after.
