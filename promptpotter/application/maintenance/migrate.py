"""What a landing writes to each survivor whose shape it changed, staged under the landing code and
proven before any of it is written (`maintenance/landing.py`). A migration stages nothing once it has
landed; its module is deleted in the release after it lands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from promptpotter.application.maintenance.migrations import consent_trail
from promptpotter.infrastructure.store.data_root import Staged

MIGRATIONS: dict[str, Callable[[Path], list[Staged]]] = {"consent trail": consent_trail.staged}
