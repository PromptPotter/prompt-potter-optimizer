"""L1 / L2 / L3 optimizer loop primitives.

Nothing is re-exported here — every consumer imports the leaf directly:

* :class:`Cycle` — round/escalation state container threaded through every
  layer of the loop: ``from promptpotter.application.optimization.cycle import Cycle``.

The escalation subpackage carries its own curated surface — import from
it directly rather than expecting re-exports here:

* ``promptpotter.application.optimization.escalation`` — L2/L3 firing
  logic, state observation, and the escalation rules engine
  (``decide_escalation`` over ``DEFAULT_ESCALATION_RULES``).
* ``promptpotter.application.optimization.resume_and_fork`` — resume +
  fork-on-divergence machinery.

Internals (``dispatch_hub``, ``l1/``, ``l1_critique``, ``validators/``,
``elimination``, ``llm_call``, ``round_analysis``,
``decomposition``, ``observers``) are NOT re-exported.
Reach into the submodule directly:
``from promptpotter.application.optimization.l1 import execute_round``.
"""
