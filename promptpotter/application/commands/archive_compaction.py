from __future__ import annotations

from collections.abc import Callable
from typing import Any, get_args

from promptpotter.application.commands.dispatcher import Applier, Dispatched, Replaying
from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveReport,
    compact_measurement_archive,
    purge_cold_store,
    reclaim_dataset,
    restore_measurement_archive,
)
from promptpotter.domain.command_kinds import ArchiveMode, CompactArchivePayload

#: Each mode's pass, every one called alike: the stores, the dataset it is scoped to, and `apply`.
_PASS: dict[ArchiveMode, Callable[..., ArchiveReport]] = {
    "compact": compact_measurement_archive,
    "restore": restore_measurement_archive,
    "purge-cold": purge_cold_store,
    "reclaim": reclaim_dataset,
}
assert set(_PASS) == set(get_args(ArchiveMode.__value__)), "an archive mode runs no pass"


async def compact_archive(dispatched: Dispatched[CompactArchivePayload]) -> Applier[ArchiveReport]:
    """`inventory` has no arm on purpose: this highway records a `CommandRecord` per call, which would bill a read."""
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> ArchiveReport:
        return _PASS[payload.mode](stores, dataset=payload.dataset, apply=payload.apply)

    return Applier(_apply)


async def compacted_once(_: Replaying[Any]) -> ArchiveReport:
    # A deduped retry replays an EMPTY report: re-running `purge-cold` would report gone bytes deleted twice.
    return ArchiveReport()
