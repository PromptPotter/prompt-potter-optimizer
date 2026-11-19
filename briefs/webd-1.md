# webd-1: four small webd rows, webapp-side (cloud)

`briefs/common.md` binds you; read `webapp/CLAUDE.md` and `webapp/lib/CLAUDE.md` first. Base:
`origin/main` at your start (it holds live-3's landing, "a round reads one source"; if `git log
origin/main --oneline -5` shows no such commit, stop and report). The rows are in
`docs/specs/code-debt-cleanup.md` § webd as they stand on `origin/main`: read them there, check each
at its line; one that no longer names a defect is reported, not built. One commit per link, each
quoting its row's title; cut each row to what is left.

## Links
1. **A steer editor resets on its base's identity**: `NodeConfigEditor.tsx:71` resets its rows when
   `base` is a new object. Action (the row's): the reset keys on the base's content. Re-test: the row's.
2. **A read picks its freshness off its own last answer**: `useRound.ts` peeks `readResult` to choose
   `roundRead`'s `once` vs `byEtag`. Action (the row's): a `final(body)` on `ReadDescriptor`, owned
   by `read-cache.ts`, stops following a read once its answer says it is final; `roundRead` declares
   `final: (view) => !view.unfiled` and `useRound` reads one descriptor. Grep for any other hook
   choosing a descriptor off a read's answer, and move it too. Re-test: the row's, plus the existing
   `useRound` fact (a filed round under a ticking store makes 0 requests).
3. **An L4 panel's Leader card is empty**: `panes.tsx::LeaderPane`'s `SubjectBox` draws nothing for an
   origin-only outer campaign, and no line says why (I1). Re-test: the row's. Name the visual change.
4. **The served contract has three hand copies** (✓ = verify each part at its line first):
   - `commands.ts:103-108` hand-writes `Partial` `ConfigOverrides` / `ForkStart` beside the
     generated `CommandSent`.
   - SSE frames are cast with `as` (`live-stream.ts::openLiveStream`), and the build handshake covers
     fetches but not streams.
   - The client decides what Delete files (`CampaignMenu.tsx:44-54` vs `workspace_edits.py:62-77`).
   Action: the generated types are the one copy. A stream frame is parsed against its generated type,
   or carries the build stamp a fetch does. Delete's file list is the server's, served on the read
   the menu already makes. If a part is bigger than a link, build what fits and file the rest.

## Owns
`webapp/**` except `webapp/lib/api/types.generated.ts` by hand (regenerate it with
`scripts/build_ts_types.py` if a served shape moves). On the server, only the module serving
Delete's file list (`workspace_edits.py`, and its read's router) and `types.generated` / OpenAPI
regeneration. NOT `promptpotter/infrastructure/store/**`, `application/{jobs,commands,runner}/*`,
`application/pipeline_resolve.py`, `tests/factories.py` (store-2f runs beside you), NOT
`application/optimizers/potter/`.

## Target
Net ≤ 0 lines outside tests, or each added line named. Full `python scripts/gate.py` green. Name every
visual change.

## Report
Per `common.md`, with a Symptoms list.
