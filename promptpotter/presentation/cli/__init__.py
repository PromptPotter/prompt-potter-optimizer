"""CLI entry surface — thin shells over ``application/``.

- ``campaign_runner.py`` — the ``COMMANDS`` dispatch table + ``main()``. A verb is
  one row there plus one module (or subpackage) under ``commands/``.
- ``commands/`` — one module per verb. Two **write** verbs (``new`` / ``resume``);
  the rest are lifecycle (``archive`` / ``delete`` / ``unarchive`` / ``reset``,
  all in ``lifecycle.py``) or diagnostic (``ab`` / ``verify`` / ``noise-floor`` /
  ``matrix`` / ``reindex``). Sweeps are not a verb — they ride ``new --sweep-batch``.
- ``parsers.py`` — argparse construction.
- ``session.py`` — ``SessionCtx`` typed session-state accessors.

**Thin-shell rule:** no business logic here. A command parses args, calls into
``application/``, and formats the result. Logic that creeps in is drift — push it
down. Recipe for a new verb: ``docs/developer/adding-a-surface.md`` § 7.

There is no ``ingest`` verb — ``new <file.csv>`` folds onto the durable check-in
path. There is no read verb either: reads happen by opening the artifact tree.
"""
