# w7-rows4: three small rows, each as its Root states (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-rows3 the last). It is not
on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7. Close these rows, one commit each, each built as its
**Root** or **Door** line states. Read each whole first. If a link needs a design the row does not
decide, stop that link, report it, and go on to the next.
1. **The MCP host door loads the CLI's verb table**: `CommandResult` moves below presentation (into
   the layer that its producers already import), and `presentation/mcp/server.py` imports no `cli`
   module. Report the stdio host test's wall at base and tip; the budget is not changed.
2. **`--changed` on a branch off an unlanded `land/*` lists the whole wave**: on a clean tree the
   base is the branch's upstream (`@{upstream}` when set, else the nearest `origin/land/*` or
   `origin/main` that contains the merge-base, whichever is closest), and the base it took is
   printed. Re-test: one commit ahead of `origin/land/w7`, `--changed` lists that commit's checks only.
3. **`io.py::_SUBJECTS` is a process-global registry**: the subject is a fact of the layout
   (`layout.py` names each root), and `stamp` reads it off the path. `stamped_subject` and the module
   set go. Re-test: `git grep -c "stamped_subject(" -- promptpotter tests` is 0, and a write in a
   process that built no store still stamps its subject, which a test proves.

OWNS: `presentation/mcp/**`, `presentation/cli/main.py` (the `CommandResult` move only),
`scripts/gate.py` (`--changed` only), `infrastructure/store/io.py`, `infrastructure/store/layout.py`,
and the tests of these links. Not yours: `application/jobs/**`, `commands/launching.py`, `opening.py`
(w7-open3 holds those), and the test world (w7-tests, on land/w7-wipe; name every test file you touch).

LEAN: `promptpotter/` shrinks or holds. Digests, behaviour keys and equals-main (every leg) are
unchanged. Run data: none (a stamp's on-disk form does not move).
