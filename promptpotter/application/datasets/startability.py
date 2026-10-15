"""Which datasets a new campaign may start from HERE, and which of them a browser check-in can open.

Two questions with two scopes. What the deployment OFFERS (`settings.DATASET_OFFER`) binds every
account but the host's: the host (the terminal, an embedded host program, the claimed browser
account) runs the whole install. Whether a CHECK-IN can open one binds the draft alone: a draft is a
table of labelled rows, so a verifier-graded bank has nothing to put in it and launches from `new`.
The listing and the draft read both off the one row ladder (`loaders.resolve_dataset_items`), and
neither reads a row before the extra the dataset runs through is installed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.datasets.loaders import dataset_loader, fetch_dataset_items
from promptpotter.application.jobs.quota import spends_the_hosts_own_key
from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.config.settings import settings
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    backend_type_at,
    held_rows,
    is_dataset_dir,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra, install_hint, installed

logger = logging.getLogger(__name__)


def offered(stores: Stores, name: str) -> bool:
    offer = settings.dataset_offer
    return (
        offer is None
        or name in offer
        or spends_the_hosts_own_key(stores)
        or stores.tenant_datasets.slug_exists(name)
    )


def offered_dataset_dir(stores: Stores, name: str) -> Path:
    """Where a NEW campaign's dataset resolves: an unoffered one does not exist, as a 404."""
    if not offered(stores, name):
        raise DatasetAccessError(name)
    return readable_dataset_dir(stores, name)


class StartBlock(StrEnum):
    VERIFIER_GRADED = "verifier_graded"
    EMPTY = "empty"
    MISSING_EXTRA = "missing_extra"
    UNREADABLE = "unreadable"


class DatasetStartBlock(StrictModel):
    reason: StartBlock = Field(description="Why a check-in cannot open this dataset here.")
    explanation: str = Field(description="The reason, as a sentence for the person choosing.")


class DatasetUnstartableError(PayloadInvalidError):
    code = "dataset_unstartable"

    def __init__(self, name: str, block: DatasetStartBlock) -> None:
        super().__init__(f"{name}: {block.explanation}", details={"reason": block.reason.value})


_EMPTY = DatasetStartBlock(
    reason=StartBlock.EMPTY, explanation="It holds no row with both a query and an answer."
)


def labelled_rows(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {"query": str(it["query"]), "ground_truth": str(it["ground_truth"])}
        for it in items
        if it.get("query") and it.get("ground_truth")
    ]


def rows_block(items: Sequence[Mapping[str, Any]]) -> DatasetStartBlock | None:
    """The one reading of whether a check-in can hold these rows."""
    if all_verifier_graded(it.get("ground_truth") for it in items):
        return DatasetStartBlock(
            reason=StartBlock.VERIFIER_GRADED,
            explanation=(
                "Each sample is graded by running a verifier rather than by a labelled answer, so "
                "it is launched from the command line, not from here."
            ),
        )
    return None if labelled_rows(items) else _EMPTY


def dataset_extra(dataset_dir: Path, name: str) -> Extra | None:
    """What the dataset runs through beyond core: its connector's declaration, else its loader's."""
    connector = connectors.registered().get(backend_type_at(dataset_dir))
    if connector is not None and connector.extra is not None:
        return connector.extra
    loader = dataset_loader(name)
    return loader.extra if loader is not None else None


def missing_extra(dataset_dir: Path, name: str) -> Extra | None:
    extra = dataset_extra(dataset_dir, name)
    return extra if extra is not None and not installed(extra) else None


def _extra_block(dataset_dir: Path, name: str) -> DatasetStartBlock | None:
    """Asked before any row is read: reading one may import the very extra that is absent."""
    if (extra := missing_extra(dataset_dir, name)) is None:
        return None
    return DatasetStartBlock(
        reason=StartBlock.MISSING_EXTRA,
        explanation=(
            f"It runs through the `{extra}` extra, which this install lacks: "
            f"`{install_hint(extra)}`."
        ),
    )


def _held_block(name: str, held: Sequence[Mapping[str, Any]] | None) -> DatasetStartBlock | None:
    """Over the rows the account HOLDS; one not held opens on a fetch, which needs a loader."""
    if held is not None:
        return rows_block(held)
    return _EMPTY if dataset_loader(name) is None else None


@dataclass(frozen=True, slots=True)
class DatasetReading:
    """What the listing serves of one dataset: never its rows, which stay with the draft."""

    n_held: int | None
    block: DatasetStartBlock | None


def dataset_reading(stores: Stores, name: str) -> DatasetReading:
    """A SURVEY: one unreadable dataset lists as blocked by that, never as startable or absent."""
    if (block := _extra_block(readable_dataset_dir(stores, name), name)) is not None:
        return DatasetReading(None, block)
    try:
        held = held_rows(stores, name)
    except (ValueError, OSError, BackendUnreachableError):
        logger.exception("datasets: %s could not be read for the listing", name)
        return DatasetReading(
            None,
            DatasetStartBlock(
                reason=StartBlock.UNREADABLE,
                explanation="Its samples could not be read on this install.",
            ),
        )
    return DatasetReading(None if held is None else len(held), _held_block(name, held))


def checkin_items(stores: Stores, name: str) -> list[dict[str, Any]]:
    """The bank a check-in opens on, refused with the reason the listing serves."""
    if (block := _extra_block(readable_dataset_dir(stores, name), name)) is not None:
        raise DatasetUnstartableError(name, block)
    held = held_rows(stores, name)
    if (block := _held_block(name, held)) is not None:
        raise DatasetUnstartableError(name, block)
    if held is not None:
        return held
    fetched = fetch_dataset_items(stores, name)
    if (block := rows_block(fetched)) is not None:
        raise DatasetUnstartableError(name, block)
    return fetched


def offer_extras() -> set[Extra]:
    """What the deployment must install so every dataset it offers can run."""
    root = benchmark_datasets_root()
    return {
        extra
        for name in settings.dataset_offer or ()
        if (extra := dataset_extra(root / name, name))
    }


def offer_faults() -> list[str]:
    """Each offered name this install cannot honour, as a line for the boot banner."""
    root = benchmark_datasets_root()
    faults: list[str] = []
    for name in sorted(settings.dataset_offer or ()):
        if not is_dataset_dir(root / name):
            faults.append(f"{name}: no such dataset in this install")
        elif (extra := missing_extra(root / name, name)) is not None:
            faults.append(f"{name}: needs the `{extra}` extra — {install_hint(extra)}")
    return faults


__all__ = [
    "DatasetReading",
    "DatasetStartBlock",
    "DatasetUnstartableError",
    "StartBlock",
    "checkin_items",
    "dataset_extra",
    "dataset_reading",
    "labelled_rows",
    "missing_extra",
    "offer_extras",
    "offer_faults",
    "offered",
    "offered_dataset_dir",
    "rows_block",
]
