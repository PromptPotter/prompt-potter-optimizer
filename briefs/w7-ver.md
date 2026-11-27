# w7-ver: a writer stamps one version per subject (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7` at `0fd79cba1`** (main `2c7dff34e` plus w7-small, w7-text, w7-host, w7-line; it is
not on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`).

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **A writer stamps one version per
subject (root D)**, with the rows it names under **Closes** (**The feed's version spans a whole store**,
**The account feed never re-asks an exhibit's reads when the host's store moves**) and its adjacent
rows: **The live reads re-serve history per poll** (§ batch/26 · narr: a value is its aggregate plus
the live round, never a copy of its history; the polled dashboard re-serves every closed round per
frame), **The lineage subtree sorts every candidate per candidate**, and the ETag half of **A follower
reads with the host's whole `Stores`**. Read each whole.

DECIDED:
1. A writer stamps the version of the subject it writes, once, when it writes; a reader compares
   stamps and never re-derives a change from the files it reads (`file_sig(` falls from 34).
2. The closed-round series is one read keyed by its version: the live frame carries the live round
   and the closed series' version, never the closed rounds again; the client keeps the closed series
   until the version moves. No browser stitch of `…/rounds/{n}`. `ServedDashboard.line` (w7-line) is
   the series: keep its shape, change only what a frame re-sends.
3. The account feed re-asks an exhibit's reads when a version it read moves.

OWNS: the 11 `version` functions and their stores (`campaign_store/store.py`, `draft_store.py`,
`measurement_archive.py`, `ruler_store.py`, `tenant_dataset_store.py`, `dataset_access.py`,
`machine_status.py`, `hierarchy.py`, `hierarchy_subtree.py` (its version and sort only),
`measurement_log.py`), every `file_sig(` caller, the feed and its ETag path, `served_dashboard.py` and
`live_dashboard.py` for the frame's closed series, and the webapp's read cache for it
(`webapp/lib/hooks/` except `useRead.ts`, which is w7-gate's).

LEAN: a consolidation, so `promptpotter/` shrinks. Re-tests: `git grep -c "file_sig(" -- promptpotter`
falls from 34; one `version` per subject; a polled frame at 16 closed rounds re-sends no closed round.
Run data: none expected; name any wipe.
