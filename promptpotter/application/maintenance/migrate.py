"""What a landing does to a survivor whose shape it changed, under the landing code and before its
proof. Each migration is idempotent and runs at every landing, so a roll-back's writes are carried at
the next; its module is deleted in the release after it lands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from promptpotter.application.maintenance.migrations import consent_trail

MIGRATIONS: dict[str, Callable[[Path], list[str]]] = {"consent trail": consent_trail.migrate}


def migrate(projects_root: Path) -> list[str]:
    return [f"{name}: {line}" for name, run in MIGRATIONS.items() for line in run(projects_root)]
