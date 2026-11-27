# w10-file: the recovered symptoms, re-tested and filed (cloud · Sonnet · docs only)

`briefs/common.md` and `briefs/w10-shared.md` bind you, except that you edit NO code: your only write is
`docs/specs/code-debt-cleanup.md`.

INPUT: `briefs/recovered_symptoms.md`: 235 Symptoms / Rows-to-file sections of chain reports dated
2026-11-13 to 11-27, deleted before their items were filed. The map pass counted ~405 items whose symbols
are alive and no row names; a sample of 26 found 9 fixed since, 5 no defect, 1 duplicate, 11 still
plausible. Areas 01, 06 and 13 are their own w10 chains' to re-test: skip items whose files sit there.

For every other item:
1. **Re-test at HEAD** by its named symbols and paths (`git grep`, read the code it names). Classes:
   fixed since · no defect (a cut note, a stale claim) · already a row (by root, not only by title) ·
   still holds.
2. **File what holds**, under the section of the area whose files it names: one row per ROOT, in the
   doc's entry format (`file::symbol` — the defect — the root — **Rides with:** `area NN's chain` —
   **Re-test:**), the reporting chain and date in parentheses after the title. Several items with one
   root are one row; an item an existing row covers is one line in that row. Every backticked
   `path::symbol` must resolve to exactly one file (the gate's doc-claims check): qualify the path.
3. Work area by area, most candidates first: 14 (69), 12 (60), 02 (39), 03 (34), 06/05/04 skipped or
   small, the rest after; the "no path" items (64) last, filed only where a re-test finds the code.
4. Put each new row at the END of its area's section (w10-survey edits the sections' heads).

REPORT: per area, items re-tested / fixed / no defect / already a row / filed (rows, lines); the full
classification table in `report.md` (one line per item: its block heading, class, where it went).

DONE WHEN: every item outside areas 01, 06 and 13 has a class in the report · `python scripts/gate.py
--only doc-claims,instruction-files` is green.
