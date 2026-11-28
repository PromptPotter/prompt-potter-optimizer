# bb-prune: move N1, delete what has no reader (cloud · Opus)

`briefs/common.md` and `briefs/bb-shared.md` bind you. Spec: `briefs/backbone.md` § 4 row **N1** and § 5
row V13. The bottom-up design's M1 list was partly wrong (V13), so **the list is yours to derive**: § 4
names only a starting set.

Root: code stays after its last reader goes. Each such symbol is a second spelling that the later moves
would otherwise carry, re-point or "migrate".

Census:
1. Over `promptpotter/` (backbone packages first: `domain/`, `application/` outside `optimizers/`,
   `infrastructure/`, `config/`, `shared/`), list every module-level `def`, `class`, constant and
   `__all__` entry whose name has no reader. The name appears only on its own definition and its import
   lines, under a whole-word `git grep -nw` across `promptpotter/`, `scripts/`, `tests/`, `examples/` and
   `webapp/`. A throwaway scan in `/tmp` is fine; never commit it.
2. Clear each candidate by hand against dynamic readers: a registry filled by import or decorator, a
   `getattr`/string dispatch, a served name in `served_contract.py` or `types.generated.ts`, a pydantic
   discriminator, an entry point in `pyproject.toml`, a docs claim the `doc-claims` check reads. A symbol
   read only by its own test goes together with that test.
3. Report the candidates and the verdict on each: deleted, kept because of the reader `path:line`, or
   handed on.

V13's data at the design's tip is your starting set. Fitting the claim: `.identity()` with 0 callers
(`domain/campaign_config.py`, with `_TREATMENT_OWNED` if it falls with it), `WalkRecorder`,
`draft_config`, `_source_pipeline`. Unclear: `mint_facts`, `mint_framed_cycle`, `draft_budget`. Refuted:
`draft_served`, `campaign_connector` and `CampaignRefusal` have readers. `ArmName` is defined twice
(`domain/campaign.py:109`, `domain/paired_reading.py:245`). It is NOT yours: its readers include a member
(`optimizers/potter/election.py`) and `domain/command_kinds.py`, so it waits for N14, after bb-fence's
bound-name key makes the re-point free.

The behaviour key reads only built-in member packages (`application/optimizers/<package>/`, by
`offline_run.py::unrun`). A deletion outside them can move no key, so a moved key or digest means a
deleted symbol had a reader.

Not yours, so hand each one on as a line in the report:
- A field of a persisted record that nobody reads: removing it reshapes run data, so it is W's.
- Anything in a member file (`application/optimizers/**`): it re-keys, so it is bb-fence's.
- Anything in a file another running chain owns: bb-fence's file set (`backbone.md` § 4 N2, N3), bb-commands'
  (§ 4 N12), and bb-ladder's (`scripts/gate.py`, `scripts/complexity_ledger.py`).

Owns: every file you delete a symbol from, except the files above, and the tests of what you delete.

No wipe and no re-key. Equals-main must show decisions, requests and digests all unmoved; a moved digest
means a deleted symbol had a reader, so restore it.

Done when:
- `git diff --shortstat` against your base, under `promptpotter/`, is net-negative. The estimate is −100
  to −250, and the true number is whatever the census finds.
- The full gate is green and equals-main is unmoved.
- The report has the candidate table: name, file, reader count, verdict.
