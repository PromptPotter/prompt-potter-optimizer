# flight-share: one cell ceiling per backend, the cap read as the book counts it (cloud)

`briefs/common.md` binds you. Base: `origin/main` (it must contain `9b0a13732`, the l4-outer-5
landing; stop and report if it does not). Read `docs/concepts/dispatch-hub.md` and `tests/CLAUDE.md`.

Two rows in `docs/specs/code-debt-cleanup.md` § `### wave-5 · l4`, one link each; delete each row in
the link that closes it.

## Link A: a backend's cell ceiling binds every run the process holds on it
Row "A backend's cell ceiling binds per run, so the outer's inner runs sum past it on one box". Today
`_ScoringPhase.ceiling` reads `connector.max_cells_in_flight` per phase, so 4 inner runs at Max put
64 cells on termnorm's declared 16. The ceiling becomes ONE process-wide count per backend (keyed by
what names the machine: the connector and its base URL), taken where a cell is sent and read by the
flight reading, so `out`/`allowed` serve the shared count. Grep every reader of
`max_cells_in_flight` first; one mechanism, no per-run share beside it. The backend client's
keep-alive expires before the server's (httpx defaults to uvicorn's 5 s, a stale-reuse race): set it
where `backend.py` builds its client, with the reason in one comment.
Proof: an offline world at `--abreast all` with two inner runs on one backend never holds more cells
out than the connector declares; the served flight line says so.

## Link B: the cap's served reading is the book's own count
Row "The cap halts on money its served reading omits". The served cap row (`served_dashboard.py`,
`domain/dashboard_rows.py`) reads the ledger's billed meter while the stop reads
`SpendBook` (billed + unreported + set aside + calls out). One reading: the served row IS the
book's count, by part, at every door (CLI `dashboard`/`set-limits`, REST, webapp). A budget halt's
`run_phase` record names the parts in `detail`. Find which check fired on
`promptpotter-self.run__265c4d` (the operator's C0, 2026-11-18 ~14:21Z, halted at $1.09 billed of
$2.50) by reading the code paths, and say it in the report.
Proof: a fold of an offline ledger with a lapsed cell serves the figure `exhausted()` reads.

## Gates
Per `common.md`; `offline-run` names any key move (old->new in the commit body).

## Owns
`application/scoring/query_loop.py` (ceiling hunks), `infrastructure/backend.py`,
`application/runner/inner/spawn.py` (ceiling declaration), `infrastructure/llm/spend_book.py`,
`application/served_dashboard.py`, `domain/dashboard_rows.py`, `domain/spend.py`,
`application/run_phase_control.py` (the halt's detail), their tests, generated types/specs,
`scripts/complexity_ledger.py` (raise with a reason). A holder outside these is cut and reported.

## Report
Per `common.md`, plus which check fired the C0's halt, and the persisted shapes changed.
