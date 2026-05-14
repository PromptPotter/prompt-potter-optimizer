"""L1 / L2 / L3 optimizer loop primitives.

Two state-container entry points are re-exported here:

* :class:`Cycle` — round/escalation state container threaded through every
  layer of the loop (see :mod:`cycle`).

The escalation subpackage carries its own curated surface — import from
it directly rather than expecting re-exports here:

* ``promptpotter.application.optimization.escalation`` — L2/L3 firing
  logic, state observation, and the escalation rules engine
  (``decide_escalation`` over ``DEFAULT_ESCALATION_RULES``).
* ``promptpotter.application.optimization.resume_and_fork`` — resume +
  fork-on-divergence machinery.

Internals (``dispatch_hub``, ``l1/``, ``l1_critique``, ``validators/``,
``transitions``, ``elimination``, ``llm_call``, ``round_analysis``,
``decomposition``, ``elevation``, ``observers``) are NOT re-exported.
Reach into the submodule directly:
``from promptpotter.application.optimization.l1 import execute_round``.
"""

from promptpotter.application.optimization.cycle import Cycle

__all__ = ["Cycle"]
