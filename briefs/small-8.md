# small-8: three small rows, each its own link (cloud)

`briefs/common.md` binds you. Base: `origin/arc/small-7` (b4c25d39e: it lands before you, and link 3
reads its fence test). The rows are in
`docs/specs/code-debt-cleanup.md` (links 1–2); quote each row's title in the commit that closes it and
delete it. One link per row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **`EliminationGate` is a one-member enum** (`intelligence/potter/pobb.py:30`, wired through the
   lens and `mask/record.py:31`): delete it and its wiring. If a hashed trace or record key carries
   it, name the digest that moves (old→new) and the run data it wipes. **Re-test:** the row's.
2. **An empty round's error rate reads 0.0** (`domain/round_diagnostics.py::RoundDiagnostics.error_rate`):
   `None` where no cell ran, and every reader (served, CLI, webapp, a round's markdown) words the
   absence; run data with the old shape is wiped (name it). **Re-test:** the row's, in a test module
   other than `tests/test_spend.py` / `tests/test_scoring.py` (the fix chains own those).
3. **The round-result factory keys arms by position** (small-7's report, no row yet):
   `tests/factories.py::round_result` keys arms `c{i}` while `Parent.own_rows` reads
   `ArmKey(individual.id)`, so a seated round's cells never reach the parent's rows unless a test
   re-keys them (small-7's fence test did: `tests/test_security.py`). Root: the factory keys each arm
   by its individual's id; delete every test-side re-key it makes redundant. **Re-test:** the fence
   test passes without its re-key.

## Owns
`intelligence/potter/pobb.py`, the lens files that carry the gate, `mask/record.py`,
`domain/round_diagnostics.py` and its readers, `tests/factories.py`, the tests that re-key arms,
generated specs/types. NOT `presentation/cli/main.py`, NOT `infrastructure/llm/spend_book.py`,
`domain/spend.py`, `run_observers.py`, `store/read_model.py`, `jobs/quota.py`, `runner/**`,
`domain/phases.py`, `application/maintenance/**`, `tests/test_spend.py`, `tests/test_scoring.py`
(the `l4-words-fix` and `spend-served-fix` chains). A file outside Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs and `--digests`; name every leg and digest
that moves, old→new.

## Report
Per `common.md`.
