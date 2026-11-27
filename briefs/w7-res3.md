# w7-res3: the resolution rows w7-res and w7-res2 filed (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7-wipe`'s tip** (rebased onto land/w7's backout commit, w7-tests3 the last). It is
not on main, so skip the is-ancestor check and report `git log -1 origin/land/w7-wipe`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7, four rows. Read each whole, along with the w7-res and
w7-res2 commits (`git log --grep=w7-res origin/land/w7-wipe`). Close each as its **Root** states,
one commit per row:
1. **A third reader of a cycle's draw raises its own error**: `fork_siblings.py` calls
   `session.py::drawn_partition`.
2. **`select_optimizer(self.config.optimization)` is spelled three times**: a draft's merge is a
   `RunResolution`, which leaves one site.
3. **`Session.pipeline_schema` holds two facts**: a `Session` holds its read, and the resolution owns
   the schema. Re-test: `Session` has no `pipeline_schema`.
4. **`open_cell` resolves a whole dataset to compile its scorer**: the archive carries its dataset's
   config. If that moves a banked shape, it is a WIPE: the branch already wipes, so name the
   reshaped record and stop to report before you build it if it touches a SURVIVOR
   (`store/data_root.py`).

If a link needs a design the row does not decide, stop it, report it, and go on to the next.

OWNS: `promptpotter/application/pipeline_resolve.py`, `session.py`, `bench/resume_and_fork/`,
`datasets/` (the draft pipeline), `scoring/measurement_log.py`, the archive record `open_cell` reads,
and their tests.

LEAN: `promptpotter/` shrinks. Equals-main (every leg), digests and behaviour keys are unchanged, or
each move is named and explained. Run data: name each wipe beyond the branch's own.
