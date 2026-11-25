# web-land: a landscape phone keeps room for the pane, and the walk says so every time (cloud · webapp)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is
**`origin/arc/web-tail` at `a1e7d43b1`** (main `da08f4717` plus web-tail: the walk world offline, a
stale bundle refused, panes keyed on their `column` container, one field skin). Replace the ancestor
check with `git merge-base --is-ancestor da08f4717 a1e7d43b1`. Read `git log --format='%h %s%n%b'
da08f4717..a1e7d43b1` first. Push to `arc/web-land` as ONE commit. No `promptpotter/` edit.

## The defect
The full gate's walk went red on `webapp/e2e/walk/responsive.spec.ts:32` (`landscape-844 › every view
fits its viewport`, touch project): "a tap lands off the control drawn there", the run view's rows
("Higher-ranked rows", "Lower-ranked rows", a cell-count row) lying under `button.chat-attach` and
`div.chat-field`. The same test failed once before and passed on a rerun. At 844×390 the run view's
chrome stacks: the cycle header (title, dataset chip, id, Follow), the stats line, the state table,
the chat composer, the play strip and the tab bar, leaving the content pane about 10px tall.

## Links
1. **The chrome spends a landscape phone's height** (root): at a short viewport the content pane
   keeps room to read and tap. Decide where the height goes and say why in one line of the commit:
   the header and state table scroll away with the content instead of standing fixed, or the
   composer and the play strip share one row, or both. Prefer the change that deletes a rule.
   Never a `max-height` hack on one pane, never a rule per view. The operator designs for the phone
   first: the portrait 390×844 layout must not move (screenshot it before and after).
2. **The check is decided by the layout, not by timing**: find why `harness.ts::everyTapLands`
   passed on the same broken layout on a rerun (rows not yet drawn when it ran? scroll position?),
   and make it wait on what it checks, so a broken layout is red every run. **Re-test:** the spec
   red 3 of 3 at base with your link-2 harness change alone, green 3 of 3 at the tip.

## Owns
The webapp shell and chat CSS (`webapp/app/styles/`, `webapp/components/` CSS modules, only the
rules this layout needs), `webapp/e2e/harness.ts`, `webapp/e2e/walk/responsive.spec.ts`, their
README line. Leave `webapp/components/candidates/`, `webapp/lib/`, `domains/candidates.css` alone.

## Proof
Full gate green, the walk twice. Screenshots before/after on `out/web-land-report` under `shots/`:
the run view at 844×390 and at 390×844, `<view>-<viewport>-<before|after>.png`. Report: the commit
and +added/−deleted; the re-test counts; where the height went and why; Rows to file; Symptoms.
