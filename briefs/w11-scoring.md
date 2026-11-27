# w11-scoring: area 04 to its Target, the fillable error and one lift shape (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 04 and the debt doc's `## 04` section.

Links:
1. **An errored, charged cell is replayed, never bought again** (decision 4). `domain/cell_fold.py::
   repairable_holes` counts a charged error, and a resumed walk buys it again. That is one root:
   `ErrorCategory` does not say which errors are fillable. Fix it at the category, and `ReplayFeed` replays
   what was charged.
2. **A lift is read one way**: `comparable_to` / `lift_over` are the only difference of two θ. Fold the
   second reading and **the warm gate's missing se_δ** onto it.
3. **The paired SE floor** stays the declared `1/(4n)` rule (decision 9), stated once in
   `shared/statistics.py`; **the composite step's band** rides it.
4. **The four config knobs** (decision 5): keep `degradation_fatal_fastpath`, and delete
   `enable_2pl_graduation`, `lift_reference`, `fitness_ceiling` and `rounds_to_ceiling` along with every
   reader. This touches config files that are w11-config's: delete only their rows and name each.
5. **A round close re-resolves the config it already holds** (`measurement_log.py` calls `cycle_config`
   inside the hard-sample view).
6. **Census:** `sample_measurement.py`, `search_point_scorer.py` (the sole gateway), `formula/compiler.py`,
   `intelligence/indexes/axis.py`, `row_policy.py`, `domain/fitness_fold.py`, `query_loop.py`,
   `domain/scoring.py`, `measurement_log.py`, plus area 04's recovered items. Each gets a verdict. Read
   the banked ledgers the offline world holds for the RM348 question (does an errored cell bias a
   head-to-head?) and answer it in the report.

Held, mechanism not feature, so file the re-test only: PoBB's false-cut rate, the pick's evidence pooling
across rounds, the origin panel under `vote: overlap`, and the edited-scorer refit (decision 10 is
approved: refuse below a coverage).

OWNS: `application/scoring/**`, `application/intelligence/**`, `application/mask/**`, `domain/scoring.py`,
`ruler.py`, `paired_reading.py`, `fitness_fold.py`, `crowning_bar.py`, `domain/bench.py`, `cell_fold.py`,
`shared/statistics.py`, `composite.py`, `measurement_archive.py::ReplayFeed`.

DONE WHEN: `offline_run.py --charged` crossed-resumed `harness.backend_cells` equals crossed's · one
constant-side rule in `statistics.py` · `git grep -nE "enable_2pl_graduation|lift_reference|fitness_ceiling|rounds_to_ceiling" -- promptpotter webapp/app webapp/lib`
is empty · is every score a consumer shows served? · keys: name each move by its mechanism.
