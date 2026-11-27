# wave 10: what every w10 chain shares (read beside `briefs/common.md`)

**BASE (overrides common.md's BASE FIRST):** `origin/release/next`, main's stand-in (42bd9ca42 at dispatch; main is f73c3cb84).
Report `git log -1 origin/release/next`. Five chains run at once: w10-records, w10-jobs, w10-identity (Opus),
w10-file, w10-survey (Sonnet). Landing order: w10-survey, w10-file, w10-identity, w10-jobs, w10-records.

**The unit of work is an AREA, not a row list.** `docs/specs/code-debt-cleanup.md` is sectioned by the
cleanup map's 14 areas (`## NN · name`). Each section opens with the area's **Target** (what the area is
when clean) and its **Unexamined** modules (no row has read them as a shape). A code chain brings its
area to its Target:
1. **Census first, edit second.** Re-test every row in your section. Open every Unexamined module and
   read it against the Target: each gets a verdict in the report, "meets Target" or the row it files or
   the fix it lands. Then re-test your area's items in `briefs/recovered_symptoms.md` (chain reports
   deleted before their symptoms were filed; a block heading names its date and chain; grep by your
   area's paths). About 0.4 of them still hold.
2. **Fix along the root** what the census finds inside your area; one defect at N sites is one root.
3. **File what you cannot close**: under the section of the area whose files it names, one row per root,
   in the doc's entry format, **Rides with:** `area NN's chain`. A symptom a row already covers is one
   line in that row, never a new row.
4. **Close with the Done-when** your brief lists: run each check and report its answer.

**Nothing you saw goes unfiled.** The report's Symptoms appendix is a ledger, not a list: each item says
where it went (fixed in commit X · a line in row Y · new row Z). An item with no home is a failed link.
Report `filed N / closed M`, and each Unexamined module's verdict.

**A re-key never defers a fix (operator rule).** Land each behaviour-key or digest move, explain it by
its mechanism, name it old→new in the commit body. A leg whose decisions move for a reason you cannot
name stops the link.

**BASELINE:** the surface-ledger check passes a row at or under a ceiling this branch raised. A chain
that grows `package_lines` raises it in `scripts/complexity_ledger.py::BASELINE` with the reason; set it
to the measured count, never above.

**Shared files:**
- A path another w10 brief owns is that chain's. A minimal re-point outside your Owns is allowed (an
  import, a call site of a symbol you moved, a comment naming one); name each in the commit body.
- **Generated files** (`webapp/lib/api/types.generated.ts`, `docs/specs/*generated*`,
  `presentation/api/served_contract.py`, `maintenance/built_key.py`): regenerate by their script where
  your gate needs it, never hand-edit. The orchestrator regenerates on the merged tree.
- **Hubs** (`application/read_params.py`, `domain/command_kinds.py`, `tests/test_spend.py`,
  `tests/factories.py`, `main.py::API_ROUTERS`, `webapp/lib/test-fixtures.ts`,
  `promptpotter/assets/behaviour_keys.json`, `docs/specs/code-debt-cleanup.md`): add, rename or delete
  only your own row, block or field. In the debt doc, edit only your area's section, except a new row
  filed under another area's section.
- **Run data:** a wipe is allowed (no backward compatibility). Name each reshaped record. A SURVIVOR row
  (`store/data_root.py`) that moves ships a one-shot migration under the landing, or the link stops.
- `docs/specs/roadmap.md` stays untouched; name the roadmap item you close in the report.
- Nobody answers mid-run. A fork your brief does not decide is decided by you along the root
  (CLAUDE.md § Ship the root) and named under "decided in-run". The operator's criterion for a choice he
  delegated: whatever makes development smoothest and yields the cleanest implementation.
- **Commit messages:** under ~1000 characters, no markdown headings, few newlines. The report carries
  the detail.
- Never CSS anchor positioning or the overlay frame lag (operator: dropped for good).
