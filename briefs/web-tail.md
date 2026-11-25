# web-tail: the walk proves the page it serves, and a pane lays out by its column (cloud · main)

`briefs/common.md` binds you (read BASE FIRST). Your base is **`origin/main` at `da08f4717`**.
Push to `arc/web-tail` as ONE commit, or one per link where the full gate is green at each; a doc
rides the code it describes. Read each row's full text in `docs/specs/code-debt-cleanup.md` at
your base, verify its citations with `git grep` first (rows go stale), and correct a stale one
when you close it.

## Links, in this order
1. **The walk's server reaches the network for model capabilities** (§ wave-5 · webapp): a cloud
   full gate went red on `playwright` once, its WebServer logging "model capability refresh failed
   … 403 Forbidden". Root: the walk world serves a pinned capability answer and makes no outbound
   call. `scripts/offline_world.py`'s router already answers `openrouter.ai/api/v1/models`; find
   which caller fires under `webapp/e2e/serve_app.py` and route every outbound call of the walk
   world through that one router. **Re-test:** the walk server makes no outbound request (a
   refusing proxy changes no walk result). If the caller sits in `initialization/wiring.py` or
   `ingest.py`, the fix still belongs to the walk world's wiring, not a guard in that caller.
2. **The walk serves a stale bundle as the page under test** (§ wave-5 · webapp): against an old
   `webapp/out` the app says "This page is out of date" and a bare `npx playwright test` does not
   refuse it. Root: the walk server refuses a bundle older than its sources, through the check
   `promptpotter/main.py`'s Said already makes, never a second staleness rule. **Re-test:** a walk
   on a stale build fails at start, naming the build.
3. **A pane's phone layout keys on the screen, not its column** (§ wave-5 · webapp):
   `measurements.css`, `scoring.css`, `dashboard-pipeline.css`, `remote.css` and
   `ui/IntervalRow.module.css` switch at `@media (max-width:640px)` though they draw inside a
   `.view` 413px wide on a 1440 screen. Root: the `column` container `ui/Toolbar.module.css`'s fold
   reads (`@container column`). **Re-test:** `git grep -c "@media (max-width: *640px)" --
   webapp/components webapp/app/styles/domains` reaches the screen-only rules alone.
4. **Form fields are skinned outside `base.css`** (§ webd): fields skin in `foundation/base.css`
   alone (`webapp/app/CLAUDE.md`'s one-skin rule; check `chat.css`, `onboarding.css`, `scoring.css`
   at your base). **Re-test:** no `border:` on an `input` / `select` / `textarea` selector outside
   `foundation/base.css`.

Links 3 and 4 change how panes look: the operator designs for the phone first. Capture Playwright
screenshots before and after at 390×844 and of one pane in a 413px column at 1440×900, for every
pane links 3–4 touch, and push them on `out/web-tail-report` beside the report (PNG, under
`shots/`), named `<pane>-<viewport>-<before|after>.png`.

## Owns
`webapp/e2e/` (`serve_app.py`, `serve.mjs`, the walk's harness), `scripts/offline_world.py` (the
walk world's router only), the CSS files the links name and `webapp/app/styles/foundation/base.css`,
`webapp/app/CLAUDE.md` (a rule line only), their tests. Leave every other file alone, above all
`webapp/components/candidates/`, `webapp/lib/` and `webapp/app/styles/domains/candidates.css` (the
operator's UI commit, not yet on main), and everything under `promptpotter/` except what link 1's
walk wiring needs, named in the report.

## Proof
Full gate green (`playwright` included, twice in a row for link 1). surface-ledger: hunt deletions
(link 3 may delete media blocks). Report on `out/web-tail-report` (per link the commit and
+added/−deleted; re-tests before/after; decided in-run; Rows closed; Rows to file; Symptoms), with
the screenshots.
