"""Layer-escalation driver — L2 / L3 transitions + the L2→L3 cascade.

Public surface for the runner + tests:

- :func:`escalate_l2` — drive an L2 (or cascading L3) escalation.
- :func:`apply_fork_payload_to_osp` — stamp fork-payload OSP deltas.

Per-layer parse/apply live in :mod:`.l2_driver` and :mod:`.l3_driver`;
the shared transition runner and the cascade live in :mod:`.executor`.
The ``LayerStrategy`` spec + ``TransitionResult`` are in
``optimization/transitions.py``.
"""

from __future__ import annotations

from promptpotter.application.optimization.escalation.firing.executor import (
    apply_fork_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.escalation.firing.l2_driver import (
    _apply_l2,
    _parse_l2,
)
from promptpotter.application.optimization.escalation.firing.l3_driver import (
    _apply_l3,
    _parse_l3,
)

__all__ = [
    "_apply_l2",
    "_apply_l3",
    "_parse_l2",
    "_parse_l3",
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
