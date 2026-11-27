# w7-ver2: w7-ver's fix pass, rebuilt on the landing tip (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-web the last). It is not on
main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

w7-ver (`origin/arc/w7-ver`, report `origin/out/w7-ver-report:report.md`, read both whole) built root D,
"a writer stamps one version per subject", on an older base. The landing held it back for one regression.
Re-land its links on the tip; conflicts with w7-open, w7-gate and w7-web are yours to resolve, and the
w7-web tip wins where both touched a served shape. Then close what follows.

1. **REGRESSION (blocks the landing): `hierarchy.py::store_version` dropped the authored definitions.**
   At base the hierarchy's version carried `definition_version(s, name)` for each dataset its campaigns
   name. The chain removed it ("read at the store's next write, or a restart"). A hand edit to an
   install or research definition now leaves the hierarchy read and the account feed stale, where at
   base it moved them. An authored tier has no writer, so it is the one honest stat: put each
   definition's version back into the version, keyed by the dataset names the store already holds,
   without walking campaigns per read. `CellsScope.version` already does this; reuse its door.
   **Re-test:** edit an authored definition, and the hierarchy's ETag and the feed's version move with
   no store write. Write it as a test.
2. **Writes that skip the stamp.** `stamp()` is a silent no-op for a write that bypasses the `io.py`
   doors. Known: `session_pointer.py::_point`'s `path.unlink(missing_ok=True)`, which clears the active
   pointer under the account's state subject. Grep every write under a registered subject root that
   skips the doors (`write_text`, `write_bytes`, `os.replace`, `shutil`, `.unlink(`, append opens) and
   route each through a door. **Re-test:** a test that clears the active pointer moves the account's
   version.
3. **The stamp's growth: file it, never fix it here.** One byte per write on one inode is what makes
   `(st_ino, st_size)` sound, so rotating or truncating it races the `O_APPEND` writers. File a row
   (**A subject's stamp grows a byte per write**) whose door keeps the version monotonic across a
   compaction. Also file a row for the process-global `io.py::_SUBJECTS` registry: a global standing
   in for a keyed subject.

OWNS: everything w7-ver owned (its brief, `briefs/w7-ver.md`), plus `session_pointer.py` and each
bypassing writer you route. Not yours: `scripts/` (w7-gate2 runs beside you).

LEAN and proofs as w7-ver's brief states. Report `file_sig(` and version counts at the tip, and show
that a polled live frame never re-sends a closed round (measure it on a run that is still appending,
not only a finished one). Run data: none expected, and `subject stamp` stays a MACHINE kind.
