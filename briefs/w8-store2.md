# w8-store2: one placer, true versions, long paths (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-store landed the subject-by-place stamp
and its compaction. Its report is on `origin/out/w8-store-report`.

You cannot run Windows. Build and prove on Linux, then name every fact the orchestrator re-runs on
the Windows desk under "Windows re-test:" in the report: a test path, a command, what it shows.

SPEC: `docs/specs/code-debt-cleanup.md`, plus w8-store's symptoms (no row yet; file none, close
each). One commit per item:
1. `io.py::_from_root` (lexical) and `io.py::_place` (realpath) are two ways to place a path under
   the data root. One placer stays.
2. `optimizer_manifest.py:95` and `measurement_archive.py::cell_signatures` read `st_mtime_ns` as a
   version, so two writes in one tick on Windows share it. They read the subject's stamp
   (`io.py::stamp_of`) instead.
3. **Run data is read bare outside `store/io.py`** (w8-store and w8-job cut it for lack of
   Windows): the data root is spelled once in its long form (`config/paths.py`), so a ledger past
   `MAX_PATH` reads. On Windows this moves the persisted `Job.workspace` strings, a SURVIVOR. If it
   does, ship a one-shot migration under the landing (`migrate.py`), proven by a test over a job
   file of today's shape. A spelling that matches by prefix only needs no migration: say which.
4. The stamp's epoch grows 2 bytes per 2048 writes, uncompacted. Compact it under the same seqlock,
   or show it is bounded and close it.

OWNS: `promptpotter/infrastructure/store/io.py`, `config/paths.py`,
`application/optimizer_manifest.py` (its version read only),
`infrastructure/store/measurement_archive.py` (`cell_signatures` only), `application/maintenance/migrate.py`
(a migration row only), and their tests.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: name
each wipe.
