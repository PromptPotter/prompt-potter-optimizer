# spreadsheetbench-s20

Twenty SpreadsheetBench tasks, run as containerized agent episodes through Harbor. Source, licence,
sample shape and the `open → adhere` step schema are `../spreadsheetbench-s10/dataset.md`'s; this
cut changes the panel and nothing else about the episode.

## Why this exists

**A search panel, where `spreadsheetbench-s10` could only be an instrument check.** Six rounds of
Qwen on s10 measured twelve edits and elected none, and the origin itself was not stable at ten
cells: `noise-floor --k 3` of the unchanged C0 scored 7, 7 and 6 of ten and flipped four cells
between draws. An edit had to move more cells than that noise to register at all.

This cut doubles the bank. The origin is measured on all twenty (`sp_budget_origin`), each
candidate on fifteen per round (`sp_budget_round`), and the origin prompt is s10's C0 unchanged.

## The cut

The first twenty of `spreadsheetbench-verified@1.0` in published order — s10's ten, then the next
ten. Arbitrary, and said out loud: the order is upstream's. **The ten added tasks are unscreened**,
so the origin reading is also their screen: a task no arm can solve, or every arm solves, carries
no signal and belongs in the reading of the first round. The shared ten replay what s10 measured
under the same configuration (`datasets/CLAUDE.md` § Re-cutting a dataset needs a NEW name).
