"""L1 / L2 / L3 optimizer loop primitives.

Two state-container entry points are re-exported here:

* :class:`Cycle` — round/escalation state container threaded through every
  layer of the loop (see :mod:`cycle`).

The two firing subpackages carry their own curated surfaces — import from
them directly rather than expecting re-exports here:

* ``promptpotter.application.optimization.escalation`` — L2/L3 firing logic
  + state observation.
* ``promptpotter.application.optimization.cadence`` — round-cadence rules
  driving when L2/L3 fire.

Internals (``dispatch_hub``, ``l1``, ``l1_critique``, ``l1_validators``,
``transitions``, ``elimination``, ``formatting``, ``llm_call``,
``round_diagnostics``, ``decomposition``, ``elevation``,
``observers``) are NOT re-exported. Reach into the submodule directly:
``from promptpotter.application.optimization.l1 import execute_round``.
"""

from promptpotter.application.optimization.cycle import Cycle

__all__ = ["Cycle"]
