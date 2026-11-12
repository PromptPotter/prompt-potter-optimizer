"""What a landing writes to each survivor whose shape it changed, staged and proven before any is written."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from promptpotter.application.maintenance.migrations import (
    consent_by_principal,
    consent_trail,
    fitness_knobs,
    grant_ceiling,
    host_account,
    job_ending,
    job_refusal,
    pack_tags,
    retired_ending,
)
from promptpotter.infrastructure.store.data_root import Staged

MIGRATIONS: dict[str, Callable[[Path], list[Staged]]] = {
    "consent by principal": consent_by_principal.staged,
    "consent trail": consent_trail.staged,
    "fitness knobs": fitness_knobs.staged,
    "grant ceiling": grant_ceiling.staged,
    "host account": host_account.staged,
    "job ending": job_ending.staged,
    "job refusal": job_refusal.staged,
    "pack tags": pack_tags.staged,
    "retired ending": retired_ending.staged,
}
