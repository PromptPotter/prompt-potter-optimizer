# application/runner/inner/ — the L4 recursion's machinery

## Where L4 lives

**L4's law is `domain/l4/` (pure over `CycleResult`); its machinery is this package** (`tasks`, `spawn_context`, `spawn`). The `promptpotter` connector's `in_process_run` delegates to `spawn.py::run_inner_cycle`. L4 is a recursion, not a 4th layer: [`../../optimizers/potter/CLAUDE.md`](../../optimizers/potter/CLAUDE.md) § Add no 4th layer.

## What a change here keeps

- **The inner campaign runs in its own `asyncio.Task`** (`spawn.py::_run_inner_campaign`): the child's context copy carries the outer's pause inward. **The outer L1's mutations are laid into the cell's `optimization.nodes` at spawn** (`spawn.py::_laid_arm`), so they reach the inner optimizer only, never the outer's. A mutation no render can apply is refused at selection and banked as the cell's fault (`CellUnscoreableError`), never dropped.
- **Sandboxes are a FLAT per-cycle registry** `<workspace>/.inner/<key>/` (`infrastructure/store/layout.py`), never nested, so the recursion stays re-entrant.
- **The sandbox owner is the last of `inside`, the asker the last of `enclosing`; a fork moves only the asker** (`spawn_context.py::retarget_inner_spawn`).
