"""Sweep-batch orchestration — one fork per ``OperatorSweepFile``.

Each operator JSON file under ``datasets/{name}/sweep/`` parses into an
:class:`~promptpotter.domain.run_records.OperatorSweepFile`; the
orchestrator widens it to a ``ForkPayload(trigger=OPERATOR_SWEEP, ...)``
before calling the unified ``_mint_fork`` primitive.
"""

from promptpotter.application.sweep.sweep_runner import (
    existing_fork_source_files,
    load_sweep_payloads,
    resolve_sweep_dir,
    run_sweep_batch,
)

__all__ = [
    "existing_fork_source_files",
    "load_sweep_payloads",
    "resolve_sweep_dir",
    "run_sweep_batch",
]
