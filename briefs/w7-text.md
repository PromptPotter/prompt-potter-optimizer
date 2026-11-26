# w7-text: one text digest of a round, the markdown artifacts excised (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.

SPEC: `docs/specs/roadmap.md` item **"Then: one text digest of a round"** together with
`docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **The markdown artifacts are excised from
the engine (root G, operator fork "md")**, which closes **A round's markdown names its lift column from
the first arm**. Read both rows whole.

DECIDED (operator, 2026-11-25, root G; it governs where the newer roadmap line reads otherwise):
1. `log.md` (the cycle's and the campaign's: `infrastructure/store/layout.py:189,252`) and
   `review.md` both go. Every fact either shows stays plain text in the run folder:
   `readout.log`, a round file (`rounds/round_NNNN.json`) or a CLI read verb (`evidence` first), never a
   served-only read. The run folder is the headless UI (a device with no browser reads it). That
   includes `review.md`'s checks, variants and critique: list each fact it shows and where it now lives.
2. `review.md`, `log.md` (`views/view_models.py::RoundDigestView`) and the readout each re-derive a
   round off `RoundResult`; `ServedRound` already serves it. One round block, rendered from
   `ServedRound`, is the round's text wherever the folder shows it. `RoundDigestView` goes.
3. The tuning row **The default host pin is a trial with no verdict** reads `review.md`: move its
   reader first (link 1).
4. `export.json` is not part of the decision: keep it unless the census shows it repeats a round file
   whole, then name that in the report and leave it.
5. If the readout's stop line renders in your files, also close **"Rebased to fork" names no fork**
   (wave-6 · what the v0.8.21 box test showed): the stop's words carry the cut round and the
   `RebaseRequest.reason`. Otherwise cut it.

OWNS: `promptpotter/application/views/`, `promptpotter/application/runner/output.py`,
`promptpotter/application/cycle_reads.py`, the two path helpers in `infrastructure/store/layout.py`,
the readout's round renderer (find it; name it), the one line
of `scripts/offline_run.py` that names `review.md` (shared with w7-gate: that line only), every doc,
layer `CLAUDE.md` and skill (`.claude/skills/potter-self/`, `potter-run/`) that names `log.md` or
`review.md`, and the tests that read them.

LINKS: (1) the tuning reader; (2) one round block from `ServedRound`, `RoundDigestView` gone; (3) the
two markdown files excised, their facts placed; (4) docs and skills.

LEAN: `promptpotter/` shrinks clearly (`runner/output.py` is 786 lines). Re-tests:
`git grep -c "review.md" -- promptpotter` 0; `git grep -c "log\.md" -- promptpotter` 0;
`git grep -c "RoundDigestView" -- promptpotter` 0. Folder text only: no behaviour key or digest may
move; name any that does.
