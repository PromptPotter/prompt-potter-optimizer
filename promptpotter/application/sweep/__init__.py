"""Sweep-batch orchestration — one fork per ``OperatorSweepFile``.

Each operator JSON file under ``datasets/{name}/sweep/`` parses into an
:class:`~promptpotter.domain.run_records.OperatorSweepFile`; the
orchestrator widens it to a ``ForkSpec(trigger=OPERATOR_SWEEP, ...)``
before calling the unified ``_mint_fork`` primitive.
"""
