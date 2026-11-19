# readings-5: readings-4's fix pass — the declaration on what emits, one spelling, a verify's difference (cloud)

`briefs/common.md` binds you. Base: **`origin/arc/readings-4`** (825a3b6ee; read `briefs/readings-3.md`
and `briefs/readings-4.md`, whose invariant stands: a grade's binariness is its scorer's declaration,
never inferred from values). Rebase it onto `origin/main` first (main moved: `package_lines`,
`SERVED_CONTRACT`, `openapi.generated.json` and the ledger conflict; regenerate every generated file
by its script, keep every ceiling reason, the ceiling ends at the measured count), then build below;
push to `arc/readings-5`. An independent review of readings-4 found these, CONFIRMED. All go in;
one commit per numbered fix at most, small ones folded.

## Fixes
1. **Only a formula that IS one matcher call can declare two-valued** (`matchers.py::fitness_scale_of`,
   read by `compiler.py`). A judge's verdict (`judges/simpleqa.py::_LABEL_TO_SCORE`, 1.0/0.0), a
   pass/fail verifier term (`env_reward`, including the clamp `compiler.py` itself recommends:
   `max(0.0, min(1.0, env_reward))`), `label_match(..) * em_match(..)` and `0.0 if x else 1.0` all
   emit 0/1 and are declared graded, so φ is FITTED for them: the very failure readings-4's row
   closed (φ ≈ 0.10, θ_se 0.28 where 0.89 is right). Decision: the declaration sits on what EMITS the
   value (a matcher, a judge's score set, a connector's `CellTerm`), and the formula's two-valuedness
   is derived from its expression over those: a bare two-valued emitter, a clamp of one, a product
   of two-valued ones, a conditional whose branches are the constants 0 and 1; anything else is
   graded. One function owns that derivation. **Re-test:** a fact over each listed shape (two-valued)
   and `0.5 * label_match(..)` (graded).
2. **Binariness has three spellings**: `paired.py` reads `column.binary or key in scorer.two_valued`
   (readings-4 added the `or`), `paired.py::origin_floor` reads `fitness.binary` alone,
   `selection.py::level_band` reads the sheet. Decision: every scorer declares `hit` (two-valued
   under any scorer), `GradeColumn.binary` is deleted, and `_Reader` and `origin_floor` read the
   declaration. Fix the rule `domain/scoring.py` writes down for the split. **Re-test:**
   `git grep -c "column.binary\|\.binary or" -- promptpotter` is 0.
3. **A verify's band test is a ~0.6% test** (`passes.py::_held`, band overlap): recorded 20/20
   (Clopper-Pearson [0.832, 1]) against fresh 14/20 reads held, where Fisher's two-sided p is 0.020.
   Decision: held = the top of the 95% band of the difference fresh − recorded (independent cells:
   Newcombe's hybrid score interval under a two-valued declaration, Welch otherwise) is ≥ 0. A level
   recorded on too few cells for a band answers as it does now (`None`); make
   `verification.py`'s description true (it says the bands "meet", and that a level under two
   cells has no band, false under a two-valued scorer), and drop the `1e-9` "two means' float error"
   comment if no float compare remains. **Re-test:** the 20/20 vs 14/20 case reads not held; 20/20 vs
   19/20 reads held.
4. **Nothing catches a wrong declaration**: `statistics.py::bounded_mean_interval` with `binary=True`
   takes `k = round(mean·n)` on values that are not 0/1, where `paired.py::_lift` raises. Move the
   check into `bounded_mean_interval` (a production invariant, `tests/CLAUDE.md`) and delete
   `_lift`'s copy.
5. **A channel expression is tested against grade names** (`evidence/comparison.py:71`): `expr:objective`
   on a connector reporting a numeric `objective` (`cell_channels_of` admits any report key) is
   declared binary and the evidence read raises. Decision: a channel maps to a grade explicitly;
   only the core `fitness` channel reads a grade's declaration.
6. **The tests pin the flag, not the path**: `tests/factories.py`'s sheet defaults
   `two_valued=frozenset()`, so a sheet disagrees with the scorer beside it (the verify fact in
   `test_scoring.py` declares fitness on its scorer, nothing on its sheets, so `_held`'s two-valued
   path is unpinned), and `test_ruler.py`'s φ fact passes `binary=True` on both sides (its "φ moved
   the SE" assertion is tautological). Decision: the factory's sheet takes its scorer's declaration;
   one fact goes through `frontier` on a `label_match` sheet against an `rr` sheet (θ_se differs by
   φ); the tautological assertion goes.

## Not in this pass (report them as rows to file, full text)
- The four re-wraps through a separately fetched scorer (`node_context.py`, `cycle.py`,
  `measurement_log.py`, `read.py`), whose root is `cell_fold.py::merge_known_outcomes` returning
  `list[GradedCell]` (readings-4's "A merge drops a sheet's declaration").
- `paired.py::read_arms` forces the t through `mean_lift` at one replicate per side, where the means
  are 0/1 and the declaration calls for McNemar; its docstring ("no row is 0 or 1") is false there.

## Owns
readings-4's Owns and reach (its report's eight files outside Owns), plus
`application/scoring/judges/**`, `domain/cell_terms.py`, `application/scoring/statistics.py`, and the
`readings-4` tests. NOT `infrastructure/store/**`, `application/{cycle_reads,pipeline_resolve}.py`,
`application/jobs/mint.py`, `application/maintenance/**`, `presentation/cli/**` (store-3); NOT
`webapp/**` except a regenerated file, `domain/{hierarchy,projection_envelope}.py`,
`application/commands/workspace_edits.py` (webd-3); NOT `infrastructure/llm/**`,
`scripts/offline_world.py`, `tests/test_world_edges.py`, `tests/test_archive.py`,
`tests/test_security.py`.

## Target
Each fix's Re-test before and after. Full `python scripts/gate.py` green, `offline_run.py --against`
the base: every moved leg named with its cause, and NO election, pick or paper's bar moved (CAPO's
one-sided paired t race, GEPA's `minibatch_improves`, LEVI's archive top elite); capo/gepa/levi
digests hash `trace.decisions` plus requests, so attribute each moved digest to a reading, not a
pick. `--digests` base → tip named per fix. `promptpotter` nets ≤ 0 or each added line named.

## Report
Per `common.md`, with a Symptoms list.
