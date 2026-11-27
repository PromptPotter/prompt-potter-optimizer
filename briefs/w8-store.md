# w8-store: the store's stamps and long paths (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row:
1. **`io.py::_SUBJECTS` is a process-global registry**: a write's subject is matched relative to
   the configured data root (`layout.py` names each subject root under it), never by a directory
   name anywhere on the path. w7-rows4 matched by name and was backed out, because a dataset named
   `jobs` or `projects` took its workspace's stamps. Pin that case in a test. Re-test:
   `stamped_subject(` count 0, and a write under `datasets/jobs/` moves its workspace's version.
2. **A subject's stamp grows a byte per write**: a compaction that keeps the version monotonic,
   race-free against the `O_APPEND` writers. If it cannot be race-free without a lock on every
   write, report the cost measured and stop.
3. **Run data is stat'ed bare outside `store/io.py`**: the data root is spelled once in its long
   form (`config/paths.py`), and every site reads through `io.py`. The sites outside your Owns
   (`optimizer_manifest.py`, `event_stream.py`, `run_state.py`, `measurement_archive.py`,
   `private_root.py`) are re-points.

OWNS: `promptpotter/infrastructure/store/io.py`, `infrastructure/store/layout.py`,
`config/paths.py`, `infrastructure/store/stores.py` (the subject declaration), and their tests.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: the
stamp's on-disk form may move, which is a wipe. A SURVIVOR's stamp that moves needs a one-shot
migration, or the link stops and reports.
