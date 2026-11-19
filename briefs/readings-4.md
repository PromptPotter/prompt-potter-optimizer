# readings-4: readings-3 continued — θ reads the scorer's declaration (cloud)

`briefs/common.md` binds you; read `briefs/readings-3.md` and its report (`origin/out/readings-3-report`)
first. Base: **`origin/arc/readings-3`** (5daa39f87), rebased onto `origin/main`, which must hold
store-2g (the store anchor fix, "the anchor by its place": it rewrote `evidence/read.py`,
`scoring/passes.py`, `runner/bench.py` signatures; if main lacks it, stop and report). Resolve
`package_lines` to the measured count, both reasons kept. Push to `arc/readings-4`. readings-3 built
the declaration (`Matcher.two_valued` → `Scorer.two_valued`, `grade_measurands`, the crowning bar)
and stopped its backbone and its verify link on files it did not own; you own them now.

## Links
1. **φ's binariness is inferred from the values** (§ readings-2), the backbone readings-3 stopped:
   `fit_theta` reads the scorer's declaration, never the values (`rasch.py::_dispersion` infers φ = 1
   from them today). Root first (readings-3's Symptom): `domain/scoring.py::CellSheet` carries
   `scorer_id` alone, so no reader past the scorer recovers a declaration: a sheet carries its
   scorer's declarations, built at its four builders (`evidence/read.py:975`, `runner/round.py:903`,
   `potter/pobb.py:69`, `tests/factories.py:295`) and read by `candidate_abilities`
   (`runner/round.py:644`) and `metric_measurand` (`evidence/read.py:465`, whose `binary=False`
   goes). **Measure:** θ_se before → after per arm on the offline world and on two banked round files
   (a binary `bbh-*` seat and a graded one); every election that moves, with θ / θ_se before → after.
   **Re-test:** the row's (a 0.999 for a 1.0 moves θ_se < 1%; it read 2.7% at readings-3's tip) and
   `git grep -c "binary=False" -- promptpotter/application/evidence/comparison.py` is 0.
2. **A verify compares against a point** (readings-3's restated row): `scoring/passes.py::_held` reads
   `recorded.value` against the fresh band's top; it compares the two bands. Re-test: the row's.
3. **A level's band reads a column's binariness, never its scorer's** (readings-3's row to file):
   `selection.py::level_band` reads the scorer's declaration (its callers `runner/bench.py`,
   `scoring/metrics.py` hold the scorer). Re-test: a 20/20 fitness band under `label_match` reads
   [0.832, 1].

## A paper's bar is not ours to move
readings-3 moved LEVI's r1 advance (`advanced` → `not_separated`, t p 0.024 → exact 0.070) and a
CAPO/wounded trigger side (`above` → `spans`), by the paired test now reading the declaration. § readings-2
says "CAPO, GEPA and LEVI keep their papers' bars". Before building, read each of the three's advance /
trigger rule against its paper (`docs/concepts/` and the optimizer's own module docs name it): where the
bar is the paper's, the declaration must not change it (the paper's test stays); where it is ours, the
move stands. Report each with the line that decides it.

## Equals-main is EXPECTED to move
Name every moved leg with its cause (φ / θ_se, a band, or a bar); a leg moving for another cause stops
the chain. Digests old → new; the L4 re-key is accepted (say it in the commit body), and this chain is
the ONE re-key of this row: land everything that moves the estimator here.

## Owns
readings-3's Owns plus `promptpotter/application/runner/round.py`, `application/evidence/read.py`,
`application/scoring/{passes,selection,metrics}.py`, `application/runner/bench.py` (the band's call
only), `application/optimizers/potter/pobb.py` (its `CellSheet` build only), `tests/factories.py`
(its `CellSheet` build only), their facts, the rows. NOT `promptpotter/infrastructure/store/**`, NOT
the webapp.

## Target
Production net ≤ 0 or each added line named. Full `python scripts/gate.py` green.

## Report
Per `common.md`: θ_se per arm before → after, every moved election / leg and its cause, the three
papers' bars with their deciding lines, digests old → new, a Symptoms list.
