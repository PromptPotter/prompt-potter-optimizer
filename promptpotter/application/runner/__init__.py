"""Optimization loop orchestrator — L1 generate → score → escalate.

Module map (read `entry.py` first — it is the one public entry point):

- `entry.py` — `run_optimization`, the seam every caller enters through. Reads the
  cycle seed, resolves rebase requests post-finalize, owns `_finalize_run`.
- `loop.py` / `round.py` — the round loop and one round's body (generate → score →
  critique → the post-round `decide_escalation` call).
- `termination.py` — the cycle-boundary stop conditions (`BudgetGate`, max_rounds,
  lives). Stop conditions live HERE, never inside the loops.
- `identity.py` — `mint_campaign_id` / `build_origin_cycle_id` / `cycle_config_identity`.
- `origin_gate.py` — the round-0 origin gate.
- `sweep.py` — sweep-mode sibling runs.
- `runner/inner/cycle.py` — `run_inner_cycle`: L4. An inner campaign in its own asyncio
  task under a sandboxed flat registry. **L4 is recursion, not a 4th layer** — there
  is no `l4_*.py` anywhere and there will not be one.
"""

from __future__ import annotations

from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.application.runner.identity import (
    build_origin_cycle_id,
    content_hash_of,
    cycle_config_identity,
    mint_campaign_id,
)

__all__ = [
    "RunMode",
    "build_origin_cycle_id",
    "content_hash_of",
    "cycle_config_identity",
    "mint_campaign_id",
    "run_optimization",
]
