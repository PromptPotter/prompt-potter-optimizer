"""What a landing writes to each survivor whose shape it changed, staged under the landing code and
proven before any of it is written (`maintenance/landing.py`). A migration stages nothing once it has
landed; its module is deleted in the release after it lands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from promptpotter.application.maintenance.migrations import (
    claim_marker,
    consent_by_principal,
    consent_trail,
    grant_ceiling,
    job_ending,
    job_refusal,
    retired_ending,
)
from promptpotter.infrastructure.store.data_root import Staged

MIGRATIONS: dict[str, Callable[[Path], list[Staged]]] = {
    "claim marker": claim_marker.staged,
    "consent by principal": consent_by_principal.staged,
    "consent trail": consent_trail.staged,
    "grant ceiling": grant_ceiling.staged,
    "job ending": job_ending.staged,
    "job refusal": job_refusal.staged,
    "retired ending": retired_ending.staged,
}
