# w7-rows5: two small rows, each as its Root states (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-open3 the last). It is not
on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7. Close these rows, one commit each, each built as its
**Root** line states. Read each whole first. If a link needs a design the row does not decide, stop
that link, report it, and go on to the next.
1. **A fork's withdraw swallows its own failure**: `ForkCut.withdraw` reports the stub it kept, on a
   channel that already exists (the refusal its caller re-raises, as a note, or the campaign's
   readout), and never masks the refusal it rides. No new log line stands in for the report. Re-test:
   a withdraw whose delete raises leaves the stub named where a read verb or the refusal shows it.
2. **`gate.py --changed` prints its "tree clean" line twice**: one `Selection.build` per run; its
   second caller is found and folded.

OWNS: `promptpotter/application/bench/resume_and_fork/**`, the withdraw's callers in
`application/jobs/launcher/opening.py` (only the report), `scripts/gate.py` (the selection only), and
their tests. Not yours: the test world (w7-tests2 rewrites it on land/w7-wipe; name every test file
you touch).

LEAN: `promptpotter/` shrinks or holds, except for the report itself. Digests, behaviour keys and
equals-main (every leg) are unchanged. Run data: none.
