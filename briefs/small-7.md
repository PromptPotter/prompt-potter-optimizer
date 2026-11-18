# small-7: four small rows, each its own link (cloud)

`briefs/common.md` binds you. Base: `origin/main` (2975f7225 or later). The rows are in
`docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete it.
One link per row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **The noise-floor pass may bank a refusal beside cells** (unverified): `scoring/passes.py:538`
   sets `refusal` before it knows the pass read nothing, which
   `PassGradedRecord._refused_reads_nothing` rejects. Verify first with the row's re-test; if it
   raises, the refusal is decided once the pass knows what it read. **Re-test:** the row's.
2. **The fence test never sends a row's query to `l1_generate`**: seat a row a placed panel quotes,
   and assert it sits fenced in the composed request. **Re-test:** the row's (it must fail on a
   request that carries the query unfenced: show it red once against a deliberately unfenced call,
   then green).
3. **A reclaim that empties a cell file leaves it**: `rewrite_pair` gains the "delete when empty"
   outcome, so `measurement_archive.py::rewrite_cell_file` never writes a 0-byte file.
   **Re-test:** the row's.
4. **The reclaim control takes its dataset as free text**: a `ReadKind` lists the datasets the
   account's archive holds (`application/read_params.py`, served per
   `application/CLAUDE.md` § Conventions, its row in `webapp/lib/CLAUDE.md` § Display-data
   sources), and `ArchiveCompactionControl.tsx` offers only those. **Re-test:** the row's.

## Owns
`application/scoring/passes.py` (+ the record it builds), `tests/test_security.py`'s fence test,
`infrastructure/store/measurement_archive.py`, `application/read_params.py` and the read's resolver,
`webapp/components/account/ArchiveCompactionControl.tsx` and its hook, their tests, generated
specs/types. NOT `presentation/cli/main.py` (a local landing edits it), NOT
`infrastructure/llm/spend_book.py`, `domain/spend.py`, `run_observers.py`, `read_model.py`,
`runner/**` (the `spend-served` and `l4-words` chains), NOT `store/dataset_access.py`,
`domain/layer_fold.py` (`panel-rule`, landing). A file outside Owns the root needs: report it.

## Target
Net ≤ 0 `promptpotter/` lines for links 1–3; link 4 adds a read and says its lines. Run the
`--against` legs; name every leg and digest that moves, old→new.

## Report
Per `common.md`.
