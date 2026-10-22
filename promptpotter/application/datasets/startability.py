"""Which datasets a new campaign may start from HERE, and which of them a browser check-in can open.

Two questions with two scopes. What the deployment OFFERS (`settings.DATASET_OFFER`) binds every
account but the host's: the host (the terminal, an embedded host program, the claimed browser
account) runs the whole install. Whether a CHECK-IN can open one binds the draft alone: a draft is a
table of labelled rows, so a verifier-graded bank has nothing to put in it and launches from `new`.
The listing and the draft read both off the one row ladder (`loaders.resolve_bank`), and
neither reads a row before the extra the dataset runs through is installed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter import connectors
from promptpotter.application.datasets.loaders import (
    fetch_bank,
    has_source,
    held_bank,
    source_extra,
)
from promptpotter.application.datasets.tabular_ingest import read_tabular
from promptpotter.application.jobs.quota import spends_the_hosts_own_key
from promptpotter.config.paths import install_datasets_root
from promptpotter.config.settings import settings
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.dataset_start import DatasetStartBlock, DatasetUnstartableError, StartBlock
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    declared_backend_type,
    is_dataset_dir,
    resolve_dataset,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DatasetAccessError, NotFoundError, PayloadInvalidError
from promptpotter.shared.extras import Extra, install_hint, installed

if TYPE_CHECKING:
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.domain.sample import Bank, Sample

logger = logging.getLogger(__name__)


def offered(stores: Stores, name: str) -> bool:
    offer = settings.dataset_offer
    return (
        offer is None
        or name in offer
        or spends_the_hosts_own_key(stores)
        or stores.tenant_datasets.slug_exists(name)
    )


def offered_dataset(stores: Stores, name: str) -> ResolvedDataset:
    """Where a NEW campaign's dataset resolves: an unoffered one does not exist, as a 404."""
    if not offered(stores, name):
        raise DatasetAccessError(name)
    return resolve_dataset(stores, name)


_EMPTY = DatasetStartBlock(
    reason=StartBlock.EMPTY, explanation="It holds no row with both a query and an answer."
)


def labelled_rows(samples: Sequence[Sample]) -> list[dict[str, str]]:
    return [
        {"query": s.query, "ground_truth": truth}
        for s in samples
        if s.query and (truth := s.ground_truth)
    ]


def rows_block(samples: Sequence[Sample]) -> DatasetStartBlock | None:
    """The one reading of whether a check-in can hold these rows."""
    if all_verifier_graded(s.ground_truth for s in samples):
        return DatasetStartBlock(
            reason=StartBlock.VERIFIER_GRADED,
            explanation=(
                "Each sample is graded by running a verifier rather than by a labelled answer, so "
                "it is launched from the command line, not from here."
            ),
        )
    return None if labelled_rows(samples) else _EMPTY


def dataset_extra(dataset_dir: Path) -> Extra | None:
    """What the dataset runs through beyond core: its connector's declaration, else its source's."""
    try:
        backend_type = declared_backend_type(dataset_dir)
    except PayloadInvalidError:
        backend_type = ""
    connector = connectors.registered().get(backend_type)
    if connector is not None and connector.extra is not None:
        return connector.extra
    return source_extra(dataset_dir)


def missing_extra(dataset_dir: Path) -> Extra | None:
    extra = dataset_extra(dataset_dir)
    return extra if extra is not None and not installed(extra) else None


def _extra_block(dataset_dir: Path) -> DatasetStartBlock | None:
    """Asked before any row is read: reading one may import the very extra that is absent."""
    if (extra := missing_extra(dataset_dir)) is None:
        return None
    return DatasetStartBlock(
        reason=StartBlock.MISSING_EXTRA,
        explanation=(
            f"It runs through the `{extra}` extra, which this install lacks: "
            f"`{install_hint(extra)}`."
        ),
    )


def _held_block(dataset_dir: Path, held: Bank | None) -> DatasetStartBlock | None:
    """Over the rows the account HOLDS; one not held opens on a fetch, which needs a source."""
    if held is not None:
        return rows_block(held.samples)
    return None if has_source(dataset_dir) else _EMPTY


@dataclass(frozen=True, slots=True)
class DatasetReading:
    """What the listing serves of one dataset: never its rows, which stay with the draft."""

    n_held: int | None
    block: DatasetStartBlock | None


def dataset_reading(stores: Stores, name: str) -> DatasetReading:
    """A SURVEY: one unreadable dataset lists as blocked by that, never as startable or absent."""
    dataset = resolve_dataset(stores, name)
    dataset_dir = dataset.directory
    if (block := _extra_block(dataset_dir)) is not None:
        return DatasetReading(None, block)
    try:
        held = held_bank(stores, dataset)
    except DatasetUnstartableError as exc:
        # A state of THIS install, not a fault: the same refusal opening it answers with.
        return DatasetReading(None, exc.block)
    except (ValueError, OSError, BackendUnreachableError):
        logger.exception("datasets: %s could not be read for the listing", name)
        return DatasetReading(
            None,
            DatasetStartBlock(
                reason=StartBlock.UNREADABLE,
                explanation="Its samples could not be read on this install.",
            ),
        )
    n_held = None if held is None else len(held.samples)
    return DatasetReading(n_held, _held_block(dataset_dir, held))


def checkin_bank(stores: Stores, name: str) -> Bank:
    """The bank a check-in opens on, refused with the reason the listing serves."""
    dataset = resolve_dataset(stores, name)
    if (block := _extra_block(dataset.directory)) is not None:
        raise DatasetUnstartableError(name, block)
    held = held_bank(stores, dataset)
    if (block := _held_block(dataset.directory, held)) is not None:
        raise DatasetUnstartableError(name, block)
    if held is not None:
        return held
    fetched = fetch_bank(stores, dataset)
    if (block := rows_block(fetched.samples)) is not None:
        raise DatasetUnstartableError(name, block)
    return fetched


def checkin_rows(stores: Stores, draft: DraftCampaign) -> list[dict[str, str]]:
    upload = stores.drafts.upload_of(draft.draft_id)
    if upload is not None:
        path = stores.tenant_datasets.upload_path(upload)
        return list(read_tabular(path.read_bytes(), path.name).rows)
    name = draft.source_dataset
    if name is None:
        raise PayloadInvalidError(f"draft {draft.draft_id!r} names neither an upload nor a dataset")
    return labelled_rows(checkin_bank(stores, name).samples)


def _offer() -> dict[str, Path]:
    """The install dataset each offered name names, the offer whole or refused: boot and deploy both."""
    root = install_datasets_root()
    offer = {name: root / name for name in sorted(settings.dataset_offer or ())}
    if absent := [name for name, path in offer.items() if not is_dataset_dir(path)]:
        raise NotFoundError(f"DATASET_OFFER names no dataset in this install: {', '.join(absent)}")
    return offer


def offer_extras() -> set[Extra]:
    """What the deployment must install so every dataset it offers can run."""
    return {extra for path in _offer().values() if (extra := dataset_extra(path))}


def offer_faults() -> list[str]:
    """Each offered dataset whose extra this install lacks, as a line for the boot banner."""
    return [
        f"{name}: needs the `{extra}` extra — {install_hint(extra)}"
        for name, path in _offer().items()
        if (extra := missing_extra(path)) is not None
    ]
