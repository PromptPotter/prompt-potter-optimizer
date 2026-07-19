# JustLogic (depths 6-7) — SUPERSEDED / dead cut

**This cut is dead.** The live L4 inner instrument is **`justlogic-d234`** — JustLogic
3-class deductive reasoning (`TRUE` / `FALSE` / `Uncertain`) over an **iid random mix of
depths 2, 3, and 4**.

The `justlogic` loader (depths 6-7, `load_justlogic` in
`promptpotter/application/datasets/loaders.py`) is retained **only** so its already-banked
measurements stay addressable under their cache keys — do not wire it for new work.

Every origin score, depth-"band" verdict, and hedge-proportion number this cut once carried
is **VOID**: a data-deprecation-era artifact that reproduces nothing. Do not cite them.

Source dataset, citation, canonical-split details, and scoring: `michaelchenkj/JustLogic`
(Chen 2025, arXiv 2501.14851). HF ships one `train` split (4,900 rows, 700/depth × 7); the
canonical test set is withheld (leakage control), so any cut carved from `train` is not
leaderboard-comparable.
