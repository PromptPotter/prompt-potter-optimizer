# small-13: three small type roots: a fit keyed by ArmKey, a standing merge, a reported zero (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **the sha the dispatch names** (origin/main;
small-12b landed there: the election reads `MeasuredRound.abilities`, the replay fits its banked
rows). Push to `arc/small-13`. Three rows of `docs/specs/code-debt-cleanup.md` (read their full text):
§ wave-5 · l4 "A fit keyed by `str` beside arms keyed by `ArmKey`", § wave-5 · readings "θ's frontier
reads every row of its sheet", § passes-6 "A reported zero of reasoning reads as unreported". One
commit per row at most.

## Rows
1. **ArmKey fit.** `intelligence/rasch.py::candidate_abilities` (and what it returns, `RoundAbilities`'
   maps) keyed by `ArmKey`, so a caller holding `{ArmKey: CellSheet}` passes it whole. Census every
   caller first (`git grep -n "candidate_abilities(\|RoundAbilities" -- promptpotter tests`). **Re-test:**
   `git grep -n "for k in self.arms}, raced" -- promptpotter` == 0; `tests/test_scoring.py::_elect`
   passes its dict whole.
2. **A standing merge.** Decision: `domain/scoring.py::CellSheet.merged` returns a STANDING sheet (one
   row per cell, the later read winning as `standing()` already decides), so no reader of a merge can
   see a cell twice; `bench/difficulty.py::frontier` then reads its sheet as handed. Census every
   `.merged(` caller and every `.standing()` read directly after a merge: a `.merged(x).standing()`
   collapses to `.merged(x)`. If a caller needs the unfolded rows (a history), name it and stop the
   link instead of adding a second method. **Re-test:** `frontier` over a merge of two reads of one cell
   fits one row; `git grep -c "\.merged(.*)\.standing()" -- promptpotter` == 0.
3. **A reported zero.** `TokenAccount.reasoning` and `.cache_write` become `int | None` as `cache_read`
   is; `domain/spend.py::StepUsage.from_account` and `openai_compat.py::ReportedUsage.tokens` drop
   their `or None` / `or 0`. Census every reader of those two fields (a sum over `None` must say what
   it does: an unreported part is unreported, never 0). Old ledger lines that carry an int still load
   (a widening, no wipe): confirm by loading one banked `llm_call` record shape in a fact or name the
   wipe. **Re-test:** a reply reporting `reasoning_tokens: 0` reaches a step's usage as 0, one
   reporting none as `None`.

## Owns
`intelligence/rasch.py`, `optimizers/potter/election.py` (the replay's fit only),
`scoring/selection.py` (types only), `domain/scoring.py::CellSheet.merged`, `bench/difficulty.py`,
the `.merged(` callers, `domain/spend.py`, `infrastructure/llm/openai_compat.py`, `TokenAccount`'s
module and its readers. Tests: one fact per row, in `tests/test_scoring.py` (rows 1-2) and
`tests/test_spend.py` (row 3), plus the callers rows 1-2 retype. SHARED with tests-x (running beside
you): `tests/test_scoring.py`, `tests/test_spend.py` — add your facts at the END of each file and
touch no fixture. NOT `tests/conftest.py`, `tests/factories.py`, `stores.py`,
`jobs/{reaper,registry,machine_status}.py`, `pricing.py`, `webapp/**`, `hierarchy.py`.

## Target
Each Re-test before and after. Full `python scripts/gate.py` GREEN; `offline_run.py --against` the
base UNMOVED (rows 1-3 are types: a moved decision is a bug, stop and report it); `--digests`
named per move (row 1 edits `potter/election.py`: `treatment:potter` + `treatment:capo-pobb` move).
`promptpotter` nets ≤ 0.

## Report
Per `common.md`, with a Symptoms list.
