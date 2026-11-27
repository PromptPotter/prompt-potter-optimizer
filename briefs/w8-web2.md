# w8-web2: root E, the next derivations (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-web landed four links. What is left of
root E is 1,828 non-test lines in 24 modules (`webapp/lib/derivations`). Its report is on
`origin/out/w8-web-report`.

SPEC: `docs/specs/code-debt-cleanup.md`, **The served contract is generated and the browser derives
nothing (root E)** and its **Closes:** list, and **The browser recomputes engine facts** (still
open). Take the largest derivation first, one link per served fact: the server serves it, the
browser reads it, and the derivation and its tests go. Aim for about 6 links. Skip a derivation that
needs a record w8-spine2 is reshaping (`domain/records/`, the round fold), and name it.

Keep the `cycle_reads.py` copy as it is: its door is filed on its row.

OWNS: `webapp/` (except `webapp/e2e/walk_world.py`, a re-point only), `application/served_dashboard.py`,
`application/cycle_reads.py`, `domain/run_standing.py`, the served projections the derivations
replace, `webapp/lib/test-fixtures.ts` (hub), and their tests.

LEAN: `webapp/` and `promptpotter/` together shrink. `p_served` must not rise. Raise `package_lines`
in the BASELINE with its reason (the check passes a row at or under a ceiling the branch raised).
Equals-main, digests and behaviour keys unchanged. Mobile first: check a changed panel at phone
width. Report what is left of E, in lines and modules.
