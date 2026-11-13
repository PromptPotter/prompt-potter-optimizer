"""What a landing writes to each survivor whose shape it changed, staged and proven before any is written.

Each maps the shape the last release wrote and reads through `data_root.py::read_as_staged`, in this
order ([decisions](../../../docs/decisions.md) § 0011)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from promptpotter.application.maintenance.migrations import (
    grant_ceiling,
    host_account,
    terms_consent,
)
from promptpotter.infrastructure.store.data_root import Staged

MIGRATIONS: dict[str, Callable[[Path, Mapping[Path, bytes]], list[Staged]]] = {
    "grant ceiling": grant_ceiling.staged,
    "host account": host_account.staged,
    "terms consent": terms_consent.staged,
}
