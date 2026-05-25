"""Layer-escalation driver — L2 / L3 transitions + the L2→L3 cascade.

Public surface for the runner + tests:

- :func:`escalate_l2` — drive an L2 (or cascading L3) escalation.
- :func:`apply_fork_payload_to_osp` — stamp fork-payload OSP deltas.

Per-layer parse/apply + the shared transition runner + the cascade all
live in :mod:`.executor` (L2/L3 are pure ``LayerStrategy`` data, so the
two former driver modules collapsed into the one executor). The
``LayerStrategy`` spec + ``TransitionResult`` are in
``optimization/transitions.py``.
"""

from __future__ import annotations

from promptpotter.application.optimization.escalation.firing.executor import (
    _apply_l2,
    _apply_l3,
    _parse_l2,
    _parse_l3,
    apply_fork_payload_to_osp,
    escalate_l2,
)

__all__ = [
    "_apply_l2",
    "_apply_l3",
    "_parse_l2",
    "_parse_l3",
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
