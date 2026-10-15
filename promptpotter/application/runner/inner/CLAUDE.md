# application/runner/inner/ — the L4 recursion's machinery

**The `promptpotter` connector's `in_process_run` delegates to `spawn.py::run_inner_cycle`**, because running a whole inner campaign is orchestration. Its graph is SERVED (`pipeline_declaration`) off the manifest the panel's cells run, so no dataset mirrors a node. What a change here must keep, each stated at its site:

- **The inner campaign runs in its own `asyncio.Task`** (`spawn.py::_run_inner_campaign`).
  `_CYCLE_LEDGER` + `_CURRENT_ROUND` (`infrastructure/llm/telemetry.py`), `_ABORT_CHECK`
  (`infrastructure/llm/send_pacing.py`) and the optimizer-prompt overrides are per-task
  ContextVars: the child's COPY is how `_ABORT_CHECK` carries the outer's pause inward, and how
  the outer L1's mutations reach the inner optimizer and never the outer's.
- **Sandboxes are a FLAT per-cycle registry** `<workspace>/.inner/<key>/`, never physically
  nested (`infrastructure/store/layout.py`) — no active-pointer collision, no machine slot held,
  and the recursion stays **re-entrant** at L5+.
- **The sandbox owner is `spawn_cycle_id`, the asker is the last of `enclosing`, and a fork moves
  only the asker** (`spawn_context.py::retarget_inner_spawn`).
- One process, no networking; a localhost-endpoint worker mode would be a new `execution` value
  with no core-loop edit.
