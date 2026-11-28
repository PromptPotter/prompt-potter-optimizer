"""What a landing writes to each survivor whose shape it changed, staged and proven before any is written.

Each maps the shape the last release wrote and reads through `data_root.py::read_as_staged`, in this
order ([decisions](../../../docs/decisions.md) § 0011)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from promptpotter.application.maintenance.migrations import (
    job_endings,
    job_hops,
    job_workspaces,
    sealed_api_keys,
)
from promptpotter.infrastructure.store.data_root import Staged

MIGRATIONS: dict[str, Callable[[Path, Mapping[Path, bytes]], list[Staged]]] = {
    "job endings": job_endings.staged,
    "job hops": job_hops.staged,
    "job workspaces": job_workspaces.staged,
    "sealed api keys": sealed_api_keys.staged,
}
