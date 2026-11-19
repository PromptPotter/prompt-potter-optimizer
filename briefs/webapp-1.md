# webapp-1: the responsive walk at landscape-844 (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/land/w5i`
(the sha the dispatch names)**, the landing stack main will take, not origin/main. Push to
`arc/webapp-1`. Your row: `docs/specs/code-debt-cleanup.md` § `wave-5 · webapp (what the responsive
walk showed at the w5i landing)`, **A control sits under the remote-control bar at landscape-844**
(read its full text). Runs beside `cfg-5` (owns `promptpotter/domain/campaign_config.py`); touch only
`webapp/` and the ledger row.

## The row
Your Linux box reproduces what Windows does not: run `cd webapp && npx playwright test
e2e/walk/responsive.spec.ts --project walk --project touch --repeat-each 5` at the base FIRST and
record every failure (title, message, the screenshot's and `error-context.md`'s reading). Then read
where the failing fact is DECIDED: the "Error" control, `.remote-control`'s height, how
`useShellHeight("--remote-dock-h")` and `.content::after` reserve it, `.shell`'s `min-height:700px`
against a 390px-tall viewport. Fix the app where the layout is wrong; change the harness
(`e2e/harness.ts`) only if it misreads what a user sees, and say why in the commit. If the desktop-1440
sideways read (`div.content +51px`, `div.card.cand-card +70px`) shows, root it too; if it never shows in
your repeats, say so. Never retry, skip or loosen a check to pass. **Re-test:** the same command,
green on every repeat, before and after named.

## Proof
Full gate green (playwright included). Report on `out/webapp-1-report` (Links, Re-tests with the
before/after repeat counts, Proof, Decided in-run, Rows closed, Rows to file, Symptoms).
