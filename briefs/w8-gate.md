# w8-gate: the per-squash tax, `optimized.md`, the fsync flag, stale rows (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. Land first: this lane removes the tax every
other landing pays.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per item:
1. **The gate rewrites `maintenance/built_key.py` on every code change**: take the row's first root
   (the key is computed where it is read, and no committed file holds it). If computing it at read
   costs a hash of the package tree over ~50 ms on a cold start, take the second root instead: the
   gate's write is the commit's own step, so the pre-commit hook regenerates and stages it. Name
   which you took, with the measurement.
2. **`optimized.md` is a markdown artifact root G did not name**. DECIDED by the operator: the
   winner's text is plain text in the run folder (root G: the folder is the headless UI).
   `layout.py::optimized_surface` and `campaign_store/store.py::write_optimized_surface` become a
   plain-text file. The caller `runner/run_init.py` is a re-point.
3. **`durable=` is a caller's flag restating `Kind.durable`**: `store/io.py` reads `Kind.durable`,
   and the callers' flag goes.
4. Ledger hygiene, one docs commit, each row re-tested by grep before you touch it:
   - merge the two **Vitest's start…** rows into one;
   - **Names collide across layers**: update the counts (`mint.py` is no longer duplicated);
   - **"Which benchmark a row measures" is spelled twice**: cut it to its `BenchScope` half;
   - the **`fork_edge`** row: re-point to `bench/resume_and_fork/fork_siblings.py`;
   - move **A wiped campaign answers two failure kinds** and **A max-press resume replays on timing**
     to § Held, as unreproduced.

OWNS: `scripts/gate.py` and the gate's helpers, `promptpotter/application/maintenance/built_key.py`
and its writer, `infrastructure/store/io.py` (the `durable` path only), `infrastructure/store/layout.py`
(`optimized_surface` only), `campaign_store/store.py` (`write_optimized_surface` only), the six
`durable=` callers (re-points), the ledger rows named in item 4, and their tests.

LEAN: `promptpotter/` shrinks. Equals-main (every leg), digests and behaviour keys: unchanged, or each
move named and explained. Run data: `optimized.md` → plain text is a run-data reshape. Name it.
