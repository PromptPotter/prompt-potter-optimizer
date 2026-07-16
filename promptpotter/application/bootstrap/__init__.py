"""Campaign initialization — three concerns split across submodules.

Nothing is re-exported here — every consumer imports the leaf directly.

Two entry points:

* :func:`init_services` — wire stores + LLM client + connector → ``Session``:
  ``from promptpotter.application.bootstrap.wiring import init_services``.
* :func:`init_optimization_loop` — preflight, cycle bootstrap, observability,
  scoring: ``from promptpotter.application.bootstrap.scoring_context import
  init_optimization_loop``.

Everything else (``Session``, ``ScorerSetup``, ``CycleSnapshot``,
``auto_mint_session``, ``populate_session_scoring``, ``bootstrap_cycle``,
``_open_cycle_ledger``) sits in its own submodule: ``from
promptpotter.application.bootstrap.session import Session``.
"""
