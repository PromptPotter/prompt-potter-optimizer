# wave 8: what every w8 chain shares (read beside `briefs/common.md`)

**BASE (overrides common.md's BASE FIRST):** `origin/release/next`, main's stand-in. Report
`git log -1 origin/release/next`. It is not on main, so skip the is-ancestor check. Five chains run at
once, one batch: batch 1 was w8-gate, w8-launch, w8-resolve, w8-spine and w8-doors (landed); batch 2
is w8-store, w8-launch2, w8-resolve2, w8-web and w8-spine2; batch 3 runs beside it (w8-l4, w8-box,
w8-tests2, w8-resolve3). Batch 4 (on release/next eb7cf8e0c, with store, launch2, resolve2 and
web landed): w8-job, w8-launch3, w8-web2, w8-tests3, w8-box2, running beside w8-spine2, w8-l4 and
w8-resolve3. Batch 5 (on release/next 3bb299ac4): w8-launch4, w8-fork, w8-wallet and w8-store2,
running beside w8-web2, w8-tests3 and w8-spine3. Each owns the paths its brief lists.

**BASELINE:** the surface-ledger check passes a row at or under a ceiling this branch raised. A
chain that grows `package_lines` raises it in the BASELINE with the reason, never reports it as
inherited red.

**The point of this wave is a falling filing ratio.** Each closed row used to file about 0.8 new
ones. The order is central roots first, deletions next, features last. So:
- **A symptom you find in a file a later root owns** (the records spine, `domain/records/`,
  `read_model.py`, `ledger_scan.py`; the browser, `webapp/`, `served_dashboard.py`; the store's
  `io.py`; a rename) goes as ONE line under that root's existing row, never as a new row.
- File a new row only for a defect no open row's root covers, and say why none does.
- Report `filed N / closed M` and name each new row's owner root.

**Shared files:**
- **A path another w8 brief names is that chain's.** You cut the row that needs it, never edit it.
  A minimal re-point outside your Owns is allowed (an import, a call site of a symbol you moved, a
  comment naming one); name each in the commit body.
- **Generated files** (`webapp/lib/types.generated.ts`, `docs/**/openapi*`, `events-asyncapi*`,
  `docs/specs/data-root.generated.json`, `maintenance/built_key.py`): regenerate by their script where
  your gate needs it, and never hand-edit one. The orchestrator regenerates on the merged tree.
- **`scripts/complexity_ledger.py::BASELINE`**: edit only your own rows, each with its reason.
- **Hubs** (`application/read_params.py`, `domain/command_kinds.py`, `tests/test_spend.py`,
  `tests/factories.py`, `main.py::API_ROUTERS`, `webapp/lib/test-fixtures.ts`): add or rename only
  your own row, block or field. w8-doors owns `tests/factories.py` and the world. The others add
  their own tests beside it.
- **`promptpotter/assets/behaviour_keys.json`**: a link that moves a key stops and is reported with
  the key and why. It is not pushed as done.
- **Run data:** a wipe is allowed (no backward compatibility). Name each reshaped record. A SURVIVOR
  row (`store/data_root.py`) that moves ships a one-shot migration, or the link stops and reports.
- `docs/specs/roadmap.md` stays untouched. Name the roadmap item you close in the report.
- Nobody answers mid-run. A design fork your brief does not decide is decided by you along the root
  (CLAUDE.md § Ship the root) and named under "decided in-run". Stop a link only where every root
  choice moves a SURVIVOR or a behaviour key.
- Landing order, batch 2: w8-store, w8-launch2, w8-resolve2, w8-web, w8-spine2 (the orchestrator
  squashes each).
