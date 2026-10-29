# application/runner/inner/ — the L4 recursion's machinery

**The `promptpotter` connector's `in_process_run` delegates to `spawn.py::run_inner_cycle`**; its graph is served (`pipeline_declaration`) off the manifest the panel's cells run.

## Where L4 lives

**L4's law is `domain/l4/` (pure over `CycleResult`); its machinery is this package** — `tasks`, `spawn_context`, `spawn`. That L4 is a recursion, not a 4th layer, is [`../../optimizers/potter/CLAUDE.md`](../../optimizers/potter/CLAUDE.md) § Add no 4th layer.

## What a change here keeps

- **The inner campaign runs in its own `asyncio.Task`** (`spawn.py::_run_inner_campaign`): the child's ContextVar copies carry the outer's pause inward and route the outer L1's mutations to the inner optimizer, never the outer's.
- **Sandboxes are a FLAT per-cycle registry** `<workspace>/.inner/<key>/` (`infrastructure/store/layout.py`), never nested, so the recursion stays re-entrant.
- **The sandbox owner is `spawn_cycle_id`, the asker the last of `enclosing`; a fork moves only the asker** (`spawn_context.py::retarget_inner_spawn`).
