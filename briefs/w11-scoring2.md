# w11-scoring2: area 04, round 2 — one fitness mean, one measurement per pair (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 04 and the debt doc's `## 04` section, where w11-scoring filed 12 rows. Its report's census named three
means beside `fitness_fold`. Target: one fitness fold, read by every surface. A (sample, candidate) pair
is measured once. A charged error is scored as what it is, on every bench. The backend serves every score.

Links, deletion first:
1. **One fitness mean.** Three rows share one root: no reader reads `fitness_fold`.
   - **`measurement_log` averages `mean_fitness` by hand** (`:608`, `:630`), pooling configurations in the
     dataset scope.
   - **`evaluators.py::compute_fitness` is a second fitness mean** (`potter/.../panels.py:556`).
   - **`axis.py::refresh` pools fold fitness without checking the scorer** (unconfirmed: confirm it first).
   - Each reads the fold; the hand means go.
2. **A verifier-graded charged error leaves the head-to-head pair** (RM348). `GradedCell.scored`
   (`domain/scoring.py`) drops it, and `read_arms` (`scoring/paired.py`) intersects it out of both arms,
   so an arm sheds its own failures. It is scored as a miss and stays paired. Re-test it through a
   verifier-graded world door; add one to the offline world only beside an existing bench and name it.
3. **The stale-data protocol re-measures a (sample, candidate) pair** (`sample_measurement.py`, reached
   from `query_loop.py`'s pair retry): measure the pair once.
4. **The scoring loop imports the contract inside a function** (w11-plugin's row): the race protocol's
   types move to one side of `scoring/` ↔ `optimizer_contract.py`, and the function-local import goes.
5. **An L4 inner panel may average θ over 1PL and 2PL cycles**: decide whether a panel shares a ruler
   model. If it does, state it at one door; if it does not, the panel refuses a mixed read.
6. **Census:** `fitness_fold.py`'s twice-run `level_band` (a nit: fix it in passing),
   `runner/inner/tasks.py:375`'s backend client (a verdict only; it is not your file), and the held
   re-tests: PoBB false-cut rate (RM592), the pick's evidence pooling across rounds, the origin panel
   under `vote: overlap`, and the edited-scorer refit. Each held re-test gets a verdict, or the world door
   it needs.

Not now: the warm gate's se_δ (it needs a world whose warm legs warm a ruler for real, which is area
14's).

OWNS: `application/scoring/**` (`measurement_log.py`, `evaluators.py`, `sample_measurement.py` among
them) less its `runner` import lines (w11-runner re-points those), `domain/scoring.py`,
`domain/fitness_fold.py`, `shared/statistics.py`, `application/intelligence/indexes/axis.py`, and
`optimizers/potter/dispatch/injections/panels.py`'s fitness read only.

DONE WHEN:
- `git grep -n "mean_fitness" -- promptpotter` names no hand mean outside `fitness_fold`.
- A verifier-graded charged error stays in both arms of a head-to-head, as a miss.
- No function-local `optimizer_contract` import under `scoring/`.
- Equals-main names each move.
- The keys are named.
