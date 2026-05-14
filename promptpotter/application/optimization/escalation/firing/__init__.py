"""Layer-escalation driver — L2 / L3 strategies + the L2→L3 cascade.

Public surface for the runner + tests:

- :func:`escalate_l2` — drive an L2 (or cascading L3) escalation.
- :func:`apply_fork_payload_to_osp` — stamp fork-payload OSP deltas.
- :class:`LayerStrategy`, :data:`L2`, :data:`L3` — strategy registry.

Per-layer parse/apply live in :mod:`.l2_driver` and :mod:`.l3_driver`;
the layer-agnostic shape, the transition harness, and the cascade live
in :mod:`.executor`.
"""

from __future__ import annotations

from promptpotter.application.optimization.escalation.firing.executor import (
    LayerStrategy,
    apply_fork_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.escalation.firing.l2_driver import (
    L2,
    _apply_l2,
    _parse_l2,
)
from promptpotter.application.optimization.escalation.firing.l3_driver import (
    L3,
    _apply_l3,
    _parse_l3,
)

__all__ = [
    "L2",
    "L3",
    "LayerStrategy",
    "_apply_l2",
    "_apply_l3",
    "_parse_l2",
    "_parse_l3",
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
