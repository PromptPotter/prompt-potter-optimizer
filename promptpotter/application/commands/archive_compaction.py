from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveReport,
    compact_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import CompactArchivePayload

__all__ = ["compact_archive"]


def compact_archive(
    dispatcher: CommandDispatcher, payload: CompactArchivePayload
) -> Applier[ArchiveReport]:
    """`inventory` has no arm on purpose: this highway records a `CommandRecord` per call, which would bill a read."""
    stores = dispatcher.stores

    def _apply() -> ArchiveReport:
        match payload.mode:
            case "compact":
                report = compact_measurement_archive(
                    stores, dataset=payload.dataset, apply=payload.apply
                )
            case "restore":
                report = restore_measurement_archive(
                    stores, dataset=payload.dataset, apply=payload.apply
                )
            case "purge-cold":
                report = purge_cold_store(stores, dataset=payload.dataset, apply=payload.apply)
        return report

    # A deduped retry replays an EMPTY report: re-running `purge-cold` would report gone bytes deleted twice.
    return Applier(_apply, replay=ArchiveReport)
