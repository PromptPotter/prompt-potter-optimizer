# wave 9: what every w9 chain shares (read beside `briefs/common.md`)

**BASE (overrides common.md's BASE FIRST):** `origin/release/next`, main's stand-in (f73c3cb84 at
dispatch; main may equal it). Report `git log -1 origin/release/next`. Five chains run at once, one
batch: w9-pass, w9-payer, w9-trace, w9-entry (Opus) and w9-fields (Sonnet). Landing order: w9-fields,
w9-entry, w9-payer, w9-trace, w9-pass. Each owns the paths its brief lists.

**A re-key never defers a fix (operator rule).** A chain may move behaviour keys and digests: land
each move, explain it by its mechanism, and name it old→new in the commit body. A leg whose decisions
move for a reason you cannot name stops the link.

**BASELINE:** the surface-ledger check passes a row at or under a ceiling this branch raised. A chain
that grows `package_lines` raises it in the BASELINE with the reason, never reports it as inherited red.

**The point is a falling filing ratio.** Each closed row used to file about 0.8 new ones. So:
- **A symptom you find in a file a later root owns** goes as ONE line under that root's existing row,
  never as a new row.
- File a new row only for a defect no open row's root covers, and say why none does.
- Report `filed N / closed M` and name each new row's owner root.

**Shared files:**
- **A path another w9 brief owns is that chain's.** A minimal re-point outside your Owns is allowed (an
  import, a call site of a symbol you moved, a comment naming one); name each in the commit body.
  The named shares are: `scripts/offline_run.py` (w9-trace's; w9-pass edits only the `ROUND_KEYS` /
  `ELECTION_KEYS` members its reshape removes), `application/scoring/query_loop.py` (w9-pass's;
  w9-payer edits its `self.payer` line only), `domain/export.py` (w9-fields'; w9-pass edits its one
  `.overlap` read only).
- **Generated files** (`webapp/lib/types.generated.ts`, `docs/**/openapi*`, `events-asyncapi*`,
  `docs/specs/data-root.generated.json`, `maintenance/built_key.py`): regenerate by their script where
  your gate needs it, never hand-edit. The orchestrator regenerates on the merged tree.
- **`scripts/complexity_ledger.py::BASELINE`**: edit only your own rows, each with its reason.
- **Hubs** (`application/read_params.py`, `domain/command_kinds.py`, `tests/test_spend.py`,
  `tests/factories.py`, `main.py::API_ROUTERS`, `webapp/lib/test-fixtures.ts`,
  `promptpotter/assets/behaviour_keys.json`, `docs/specs/code-debt-cleanup.md`): add, rename or delete
  only your own row, block or field.
- **Run data:** a wipe is allowed (no backward compatibility). Name each reshaped record. A SURVIVOR
  row (`store/data_root.py`) that moves ships a one-shot migration, or the link stops and reports.
- `docs/specs/roadmap.md` stays untouched. Name the roadmap item you close in the report.
- Nobody answers mid-run. A design fork your brief does not decide is decided by you along the root
  (CLAUDE.md § Ship the root) and named under "decided in-run". Stop a link only where every root
  choice moves a SURVIVOR.
- **Commit messages:** under ~1000 characters, no markdown headings, few newlines (the operator reads
  them in VS Code). The report carries the detail.
