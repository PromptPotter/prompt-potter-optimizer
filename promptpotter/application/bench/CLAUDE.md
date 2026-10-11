# application/bench/ — the harness every optimizer runs inside

**The bench never imports an optimizer** — owned by [`../CLAUDE.md`](../CLAUDE.md) § Layer rule; nothing here reaches past `optimizers/__init__.py` and `optimizers/nodes.py`.

## Load-bearing

- Don't add a second decomposition node → § checkin

## checkin — the bench's one llm node

`checkin` belongs to no optimizer and is **not a loop layer**: it runs *around* the loop, skips the injection path, and its template may name `{{consultation_instruction}}` alone (`task_context.py::_checkin_template`). It is **not** thereby a "non-ledger" call — both modes bill a cycle ledger through `task_context.py::checkin_call_context`, so tokens, cost and audit record land like any other: resolution on the check-in campaign's, decomposition on the run it frames.

**One node, two modes, one output schema (`CheckinOutput`) — don't add a second decomposition/resolution node.** Task decomposition (the first mint on an unframed dataset, or `new --task-file`) turns a raw `task_description` into the six Layer-1 prompt strings plus `task_context`; origin resolution (web ingest) turns a draft origin into `assessment` + `findings` + `next_action` + `recap`. Both produce the six decomposition fields and both drivers capture them, so an origin turn returns the resolved origin *and* a seeded starting prompt the operator edits before mint.
