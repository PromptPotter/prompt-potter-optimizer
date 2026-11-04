# application/bench/ — the harness every optimizer runs inside

**An optimizer's state rides `Cycle` as typed `slices` (across rounds) and `outputs` (one round), reached through what a member declares on `NodeContext`**; a member's step gets `node_context`, never the `Cycle`.

**`checkin` is the bench's one llm node: two modes, one output schema (`CheckinOutput`).** <!-- probe: git grep -cE "class \w*Checkin\w*Output\(" -- promptpotter/*.py <= 1 --> Decomposition turns a raw `task_description` into the Layer-1 prompt strings plus `task_context`; origin resolution adds `assessment`, `findings`, `next_action` and `recap`, offered only the prompt slots its chain's prompt nodes declare (`task_context.py::checkin_output`). Both bill a cycle ledger (`task_context.py::checkin_call_context`): resolution the draft's, decomposition the run's.
