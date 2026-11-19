# readings-3: θ's dispersion reads the scorer's declaration; a verify compares bands (cloud)

`briefs/common.md` binds you; read `docs/methods/verdict-resolution.md` and the `readings-2` section of
`docs/specs/code-debt-cleanup.md` on `origin/main` first. Base: `origin/main` at your start. Check
each row at its line; one that no longer names a defect is reported, not built. One commit per link,
each quoting its row's title; cut each row to what is left.

## Links
1. **φ's binariness is inferred from the values** (§ readings-2), the backbone.
   `application/intelligence/rasch.py::_dispersion` returns φ = 1 only when every response is exactly 0
   or 1. A 0.999 standing in for 1.0 cuts θ SE from 0.546 to 0.324. Meanwhile the objective is declared
   graded on every scorer (`domain/scoring.py::ROW_GRADES`), so a binary dataset's δ-on-the-same-rows
   residuals read φ = 0.10: θ_se 0.28 where the information gives 0.89, and the election crowns on noise.
   Root (the row's): the scorer declares whether its objective is two-valued (no `per_cell` cost term on
   a hit), and `fit_theta` takes that declaration instead of reading the values. **Measure first:** on
   the offline world and on two banked campaigns' round files (a binary dataset, e.g. a `bbh-*`
   seat, and a graded one), report θ_se before → after per arm. The election's pick may move. That is
   the fix, not a regression; report each moved election with its θ, θ_se before → after.
   **Re-test:** the row's (a 0.999 for a 1.0 moves θ_se < 1%).
2. The same missing declaration, its two other holders (§ readings-2): `application/evidence/
   comparison.py::metric_measurand` hard-codes `binary=False` (`MetricSpec` declares none), and
   `domain/crowning_bar.py::CrowningBar.separates` never separates a graded measurand. Both read the
   one declaration from link 1. **Re-test:** `git grep -c "binary=False" --
   promptpotter/application/evidence/comparison.py` is 0, plus a fact: a binary measurand's crowning bar
   separates two arms its paired test separates.
3. **A verify compares against a point** (§ readings-2): `domain/verification.py::VerifyReading.held`
   reads the recorded level as known. Root (the row's): the record keeps the level's `Estimate`, and
   the verify reads both bands. A record shape that moves wipes run data: count the wipe
   (`ls .promptpotter/projects/*/campaigns` is the desk's, not yours: say which records move). **Re-test:**
   the row's.

## Equals-main is EXPECTED to move
This chain changes what elections read, so `offline_run.py --against` will MOVE legs and the
estimator digest (`56a65252079145c1`) will move: name old → new, and explain every moved leg by φ /
θ_se (a leg that moves for another reason stops the chain: report it). A digest move re-keys banked L4
cells; that is accepted (it never holds a fix back), so say it in the commit body.

## Owns
`promptpotter/application/intelligence/rasch.py`, `promptpotter/domain/{scoring,paired_reading,ruler,
crowning_bar,verification}.py`, `promptpotter/application/scoring/paired.py`,
`promptpotter/application/evidence/comparison.py`, the scorer declarations (`domain/scoring.py`
/ the authored scorer module that compiles `per_cell` terms: name it), their facts, and the rows.
NOT `promptpotter/infrastructure/store/**`, `application/{jobs,commands,runner}/*`,
`application/scoring/{passes,closed_rounds,measurement_log}.py`, `application/evidence/{read,
head_to_head}.py`, `application/pipeline_resolve.py`, `tests/factories.py` (store-2f lands beside
you; if a link needs one of these, report it and stop that link), NOT the webapp,
`application/cycle_reads.py` (webd-2), NOT `application/optimizers/potter/`.

## Target
Production net ≤ 0 or each added line named. Full `python scripts/gate.py` green.

## Report
Per `common.md`: θ_se before → after per measured arm, every moved election and offline leg with its
cause, the digest old → new, the records that move, a Symptoms list.
