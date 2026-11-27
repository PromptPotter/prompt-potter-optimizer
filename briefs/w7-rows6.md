# w7-rows6: three wave-7 rows the night's landings filed (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe`'s tip** (w7-res3 the last). It is not on main, so skip the is-ancestor check
and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7, three rows. Read each whole. One commit per row,
each closed as its **Root** says:
1. **An exception's notes reach only the CLI's human door**: a `Refusal` carries its notes and every
   door serves them: `--json`, the REST body, MCP, and the webapp's refusal display. The served
   contract moves, so regenerate the types. Re-test: a fork whose withdraw fails names its kept stub
   in the API's refusal body. Extend w7-rows5's test in `tests/test_archive.py`; do not add a second one.
2. **A tolerant read of a cycle's draw is undeclared**: one declared read beside `drawn_partition`
   that says when a cycle may have no draw yet. The six sites go through it, or through
   `drawn_partition` where `None` cannot happen there. Prove which per site: name what makes the draw
   present at that site, or the state where it is absent. Re-test: `git grep -c "last(hop,
   PartitionRecord)"` is 0.
3. **A dataset read is a third resolution** (filed as SUSPECTED): first confirm it. Does
   `resolve_pipeline_for_dataset` parse and narrow what `RunResolution` already does? If yes, it is a
   `RunResolution` with no read and no cycle. If no (it answers a different question), withdraw the
   row with that question named and build nothing.

If a link needs a design the row does not decide, stop it, report it, and go on to the next.

OWNS: `promptpotter/shared/` (the refusal), the doors' refusal rendering (`presentation/cli/main.py`,
the API's error handler, the MCP host, the webapp's refusal component), `application/session.py`'s
draw reads and the six sites the row names, `application/pipeline_resolve.py`, and their tests.

LEAN: `promptpotter/` shrinks, or each line of growth is named. Equals-main (every leg), digests and
behaviour keys are unchanged, or each move is named and explained. Run data: none expected; name
any that moves.
