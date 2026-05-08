"""Campaign initialization — three concerns split across submodules.

Two entry points are re-exported here:

* :func:`init_services` — wire stores + LLM client + connector → ``Session``
  (see :mod:`wiring`).
* :func:`init_optimization_loop` — preflight, cycle bootstrap, observability,
  scoring (see :mod:`scoring_context`).

Internals (``Session``, ``ScoringContext``, ``CampaignBundle``,
``auto_mint_session``, ``populate_session_scoring``, ``bootstrap_cycle``,
``_open_cycle_ledger``, ``HOT_UPDATEABLE_KEYS``) are NOT re-exported.
Callers either need an entry point above or the relevant submodule
directly: ``from promptpotter.application.bootstrap.session import Session``.
"""

from promptpotter.application.bootstrap.scoring_context import init_optimization_loop
from promptpotter.application.bootstrap.wiring import init_services

__all__ = ["init_optimization_loop", "init_services"]
