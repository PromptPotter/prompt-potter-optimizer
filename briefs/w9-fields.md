# w9-fields: a record holds nothing unread (root C2's tail) (cloud · Sonnet)

`briefs/common.md` and `briefs/w9-shared.md` bind you. Lands first of wave 9's batch.

SPEC: `docs/specs/code-debt-cleanup.md`, three rows, one root:
1. **Fields written, never read: 13 left**.
2. **A record holds no value another record derives (root C2)**: now only a pointer to row 1; it
   closes with it.
3. **`ReviewReading.feedback` has no reader**.

Root: each unread field is deleted with its writer, or names its reader in `_READ_UNSEEN`. A field
that does have a reader stays, and the row names it.

OWNS: `domain/prompt_block.py` (`Cited`, `Reported`), `domain/export.py` (`LineageEntity`,
`PromptExport`), `domain/round_audit.py` (`NodeInput.variables`), `optimizers/potter/l1.py` (the
`ReviewReading` site), each dropped field's writers, `scripts/complexity_ledger.py::BASELINE`'s
`p_unread_fields` row, and their tests. Not yours: the `.overlap` read in `domain/export.py`
(w9-pass's); any file w9-pass, w9-payer, w9-trace or w9-entry owns (re-point only, named).

Keys: capo-pobb's key moves (`feedback`), and potter's if a citation field reaches a prompt; name
each old→new. Wipe: packs and exports holding the dropped fields; name each.

LEAN: pure deletion (expected about +10 / −120).
