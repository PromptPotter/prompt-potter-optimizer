# factory-keys: the round-result factory keys arms by individual (cloud)

`briefs/common.md` binds you. Base: `origin/main` (with bundle B: small-8 cut this link because
`tests/test_scoring.py` was another chain's). The row is in `docs/specs/code-debt-cleanup.md`: **The
round-result factory keys arms by position**. Quote its title in the commit that closes it and
delete it. Tests only: no `promptpotter/` file should need to move; if one does, report it.

## Link
1. `tests/factories.py::round_result` keys each arm, and `PARENT`, by its individual's id (as
   `Parent.own_rows` reads `ArmKey(individual.id)`); delete every test-side re-key it makes redundant
   (`tests/test_security.py`'s fence test), and move the tests that read literal `ArmKey("c0")` /
   `ArmKey("c1")` / `ArmKey("parent")` against factory rounds (small-8 found three in
   `test_scoring.py`: `test_a_survey_rounds_starts_and_reference_are_arms_and_its_matrix_their_cells`,
   `test_a_cell_a_declared_bound_cut_is_settled_and_never_re_measured`,
   `test_a_layer_forking_on_an_arm_its_round_left_unelected_re_elects_it`) onto the factory's keys;
   `tests/test_identity.py:3693,3813` key `PARENT` to another individual the same way: fix them
   with it. **Re-test:** the fence test passes without its re-key; `git grep -n 'ArmKey("c[0-9]")'
   -- tests` names no factory-built round.

## Owns
`tests/factories.py`, `tests/test_scoring.py`, `tests/test_security.py`, `tests/test_identity.py`,
any other test reading the factory's arm keys. NOT `promptpotter/**`, NOT the tests `admit-3`
(backend, embedded_run, mint, task_context) or `panel-rule-2` (layer fold, dataset access, inner
tasks) add.

## Target
Tests only, net ≤ 0 lines. `offline_run.py --against` is unmoved by construction: say so after
running it once.

## Report
Per `common.md`.
