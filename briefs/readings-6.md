# readings-6: a merge keeps its sheet, one replicate reads its declaration, a constant graded side keeps a band (cloud)

`briefs/common.md` binds you. Base: **`origin/main`** (fbb346122, where readings-3/4/5 landed: a grade's
binariness is its scorer's declaration, never inferred from values; read `briefs/readings-5.md` for
that invariant). Push to `arc/readings-6`. Three rows of `docs/specs/code-debt-cleanup.md`
§ wave-5 · readings, each as filed there (read its full text); one commit per row at most.

## Rows
1. **"A merge drops a sheet's declaration."** `domain/cell_fold.py::merge_known_outcomes` returns
   `list[GradedCell]`, so its callers re-wrap through a separately fetched scorer
   (`bench/node_context.py:~360`, `bench/cycle.py:~280`, `scoring/measurement_log.py:~357`, and the row
   names `evidence/read.py:~972`: confirm that one is a re-wrap of a merge before touching it, and say so).
   Decision: the merge takes and returns `CellSheet`s, the declaration rides the sheet. A merge of two
   sheets under different scorers is a programming error and raises (a production invariant,
   `tests/CLAUDE.md`). **Re-test:** `git grep -c "scorer.graded(" -- promptpotter` drops by the number
   of re-wraps you removed (the row says 4).
2. **"`read_arms` forces the t at one replicate per side."** `scoring/paired.py::mean_lift` sets
   `binary=False` because "no row is 0 or 1"; at one replicate per side the means ARE the cells, 0/1
   under a two-valued declaration, and the declaration calls for exact McNemar. Decision: `mean_lift`
   keeps the declaration exactly where every per-cell mean is a single cell, and forces the t only
   where a mean pools replicates; its docstring says that. Check `runner/bench.py`'s `mean_lift` call
   (~line 286) under the same rule. **Re-test:** a 1-replicate arm pair under `label_match` reads
   `flips`; a 2-replicate pair still reads the t.
3. **"A constant graded read is its own band."** `shared/statistics.py::difference_interval`'s Welch
   arm returns `(d, d)` where both sides have zero spread, so five fresh 0.79s against five recorded
   0.80s fails a verify with certainty. `statistics.py` already holds TWO rules for a constant side:
   `bounded_mean_interval` takes the most variance the support allows at the tested mean, and
   `_normal_posterior` floors the SE at `1/(4n)`. Decision: the difference gets the rule
   `bounded_mean_interval` uses (the support bounds a graded side's spread), applied so a side with
   observed spread loses no power it has today; pick the most powerful form whose 95% guarantee holds
   as stated, and say in the report why it is that one. Fold `_normal_posterior`'s floor into the same
   rule only if its readers' bands keep their stated guarantee (name each moved reader), else file it
   as a row. **Re-test:** 5×0.79 fresh vs 5×0.80 recorded reads held; 5×0.30 vs 5×0.80 reads not held;
   the readings-5 binary cases (20/20 vs 14/20 not held, vs 19/20 held) unchanged.

## Not in this pass (already rows; do NOT touch)
- "A verify reads its comparison twice": `VerifyReading` rides `PassGradedRecord` in the ledger
  (`extra="forbid"`), so carrying the band reshapes run data: held for the operator's wipe call.
- "A connector's two-valued term reaches no scorer": spans every `scorer_of` caller.

## Owns
`domain/cell_fold.py`, `application/bench/{cycle,node_context}.py`, `application/scoring/**`,
`application/evidence/read.py`, `shared/statistics.py`, `application/runner/bench.py` (the
`mean_lift` call only), `tests/{test_scoring,test_ruler}.py`, a regenerated file. NOT
`infrastructure/store/**`, `application/{cycle_reads,pipeline_resolve}.py`, `application/jobs/**`,
`application/maintenance/**`, `presentation/cli/**`, `tests/{test_spend,test_archive,test_security}.py`
(store-3b); NOT `application/runner/inner/**`, `infrastructure/run_scope.py`,
`shared/measurement_context.py`, `run_observers.py` (inner-1); NOT `tests/factories.py`,
`domain/verification.py`, `domain/records/**`, `webapp/**` except a regenerated file.

## Target
Each row's Re-test before and after. Full `python scripts/gate.py` green, `offline_run.py --against`
the base: every moved leg named with its cause, and NO election, pick or paper's bar moved (CAPO's
paired t race, GEPA's `minibatch_improves`, LEVI's archive top elite); attribute each moved digest
(`--digests` base → tip) to a reading, not a pick. No persisted model changes shape (the land-data
dry run on a copy of the offline world's data lands nothing new). `promptpotter` nets ≤ 0 or each
added line named.

## Report
Per `common.md`, with a Symptoms list.
