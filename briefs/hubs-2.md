# hubs-2: one cycle outcome, a moved panel on its record, one hop-chain read, honest drift (cloud)

`briefs/common.md` binds you. Base: `origin/main`. Spec: `docs/specs/code-debt-cleanup.md`
§ `### hubs (after valid, before layers)`: its four rows are yours, one link each, in this order.
Run each row's Re-test at the base first; a row already closed is named, not built.

## Links
1. **A cycle's outcome is two types** (`domain/cycle_result.py::CycleResult` vs
   `domain/records/run_records.py::CycleFinal`). One type; the rounds read back off the ledger by
   their readers (`domain/l4/proxies.py` and every `CycleFinal` reader: grep them all first). The
   on-disk `CycleFinalRecord` shape changes: no reader of two shapes; name the wipe in the body.
2. **A moved panel is warned into a log** (`infrastructure/store/campaign_store/store.py::write_resolved_run`).
   The moved panel lands as a field of `ResolvedRunRecord` and the readout reads it. A persisted
   shape again: name it.
3. **A hop's chain is spelled 16 times** (`ledger_chain(CycleDir(...), moment=None)`). One store read
   (`CampaignStore.chain`); every call site moves onto it.
4. **`p_record_drift` counts handler tables as mirrors** (`scripts/complexity_ledger.py`). The count
   skips a `_handle_*` table and a fold's declared kinds; set the `BASELINE` row with its reason.

## Owns
`domain/cycle_result.py`, `domain/records/run_records.py` (the `CycleFinal` hunks only: another chain
edits `FlightWaiting` in that file, so touch nothing else there), `application/runner/entry.py`,
`domain/l4/proxies.py`, `infrastructure/store/campaign_store/**`, every `ledger_chain(` call site,
`scripts/complexity_ledger.py`, their tests, regenerated specs/types. A holder outside these is cut
and reported.

## Target
Net negative `promptpotter/` lines over the chain (two types become one, 16 spellings become one).

## Report
Per `common.md`: per link the commit, +added/-deleted, each Re-test before/after, the persisted
shapes changed (record kind and field), digests, Symptoms, Rows to file.
