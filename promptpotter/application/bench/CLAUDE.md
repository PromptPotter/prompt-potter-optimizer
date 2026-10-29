# application/bench/ — the harness every optimizer runs inside

**An optimizer's state rides `Cycle` as typed `slices` (across rounds) and `outputs` (one round), reached through what a member declares on `NodeContext`.** A member's step gets `node_context`, never the `Cycle`; what the bench derives for every optimizer is `Cycle`'s, never a member's copy.

## checkin — the bench's one llm node

**One node, two modes, one output schema (`CheckinOutput`).** <!-- probe: git grep -cE "class \w*Checkin\w*Output\(" -- promptpotter/*.py <= 1 --> Decomposition turns a raw `task_description` into the six Layer-1 prompt strings plus `task_context`; origin resolution adds `assessment`, `findings`, `next_action` and `recap`.
**Both modes bill a cycle ledger** (`task_context.py::checkin_call_context`): resolution the draft's, decomposition the run's.
