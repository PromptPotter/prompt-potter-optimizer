"""Every ledger record kind, read by kind only from here: importing it walks `domain/records/`.

The walk lives beside the base, never in it: a base that imports its arms makes every arm's import
import them all, which closes `run_records` -> `l4.proxies` -> `cycle_result` -> `records.error`."""

from __future__ import annotations

from promptpotter.domain import records
from promptpotter.domain.records import RECORDS
from promptpotter.shared.named_rows import walked

walked(records)
RECORDS.complete()

__all__ = ["RECORDS"]
