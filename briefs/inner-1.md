# inner-1: an inner run files its own calls under its own kinds (cloud)

`briefs/common.md` binds you. Base: **`origin/main`** at dispatch. Row: `docs/specs/code-debt-cleanup.md`
§ "wave-5 · l4", exactly the row "An inner run a bench pass spawns files all its own calls as
`bench`". Read `docs/concepts/l4.md` first (inner/outer, what an inner cell is).

## The fix
The spawner's `run_scope().filed_as` (`infrastructure/run_scope.py`) survives into the inner scope
(`runner/inner/tasks.py::enter_inner_cell`), so every call an inner run makes is filed as the
spawner's kind (inner cycle_8a2bda4c4c97 reads `{bench: 12}` on the offline world; `telemetry.py`
files `run_scope().filed_as or kind`). Action, as the row decides: `enter_inner_cell` carries the
spawner's kind on `shared/measurement_context.py::InstrumentMode` and clears `filed_as` for the
inner scope; `run_observers.py::arm_spend_book` reads `enclosed_as` off the mode instead of
`enclosing.filed_as`. Census every reader of `filed_as` and `enclosed_as` first: the outer's spend
book must still see the inner run's spend as enclosed by the spawner's kind (a bench pass's inner
spend still counts against the bench), while the inner ledger files each call as itself.
**Re-test:** the `offline_run.py --l4 --straight --bench-trigger every_adoption` leg's inner ledger
files no call as `bench`, and the outer's spend lines per kind are unchanged.

## Owns
`promptpotter/application/runner/inner/**`, `promptpotter/infrastructure/run_scope.py`,
`promptpotter/shared/measurement_context.py`, `promptpotter/application/run_observers.py`, the
`filed_as` read in `promptpotter/infrastructure/llm/telemetry.py` if the fix needs it, and one fact
for it in `tests/test_spend.py` (where the inner spend facts live). NOT:
`application/{scoring,bench,evidence,intelligence,optimizers}/**`, `domain/{scoring,results,
verification}.py`; NOT `infrastructure/store/**`, `tests/{test_archive,test_security}.py` (store-3);
NOT `infrastructure/llm/{pricing,spend_book}.py`, `application/jobs/**`, `tests/test_world_edges.py`.
A holder outside Owns is cut per `common.md`.

## Target
`promptpotter` nets ≤ 0 or each added line named. The Re-test before and after. Full
`python scripts/gate.py` green; `offline_run.py --against` the base: only the l4 legs' inner
ledger filing may move (name each moved line and its cause), no decision moves; `--digests`
base → tip, each moved digest named with its cause.

## Report
Per `common.md`, with a Symptoms list.
