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
from typing import TYPE_CHECKING, Any, assert_never

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import DatasetBasis, UploadBasis
from promptpotter.application.datasets.loaders import (
    cut_bank,
    fetch_bank,
    has_source,
    held_bank,
    source_extra,
)
from promptpotter.application.datasets.origin_readiness import columns_confirmed
from promptpotter.application.datasets.tabular_ingest import read_tabular
from promptpotter.config.paths import install_datasets_root
from promptpotter.config.settings import settings
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.dataset_start import DatasetUnstartableError, StartBlock
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.identity.default_claim import is_host
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    is_dataset_dir,
    resolve_dataset,
    runnable_definition,
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
        or is_host(stores.identity)
        or stores.tenant_datasets.slug_exists(name)
    )


def offered_dataset(stores: Stores, name: str) -> ResolvedDataset:
    """Where a NEW campaign's dataset resolves: an unoffered one does not exist, as a 404."""
    if not offered(stores, name):
        raise DatasetAccessError(name)
    return resolve_dataset(stores, name)


def labelled_rows(samples: Sequence[Sample]) -> list[dict[str, str]]:
    return [
        {"query": s.query, "ground_truth": truth}
        for s in samples
        if s.query and (truth := s.ground_truth)
    ]


def rows_block(samples: Sequence[Sample]) -> StartBlock | None:
    """The one reading of whether a check-in can hold these rows."""
    if all_verifier_graded(s.ground_truth for s in samples):
        return StartBlock.VERIFIER_GRADED
    return None if labelled_rows(samples) else StartBlock.EMPTY


def dataset_extra(dataset_dir: Path) -> Extra | None:
    """What the dataset runs through beyond core: its connector's declaration, else its source's."""
    try:
        backend_type = runnable_definition(dataset_dir).backend_type
    except PayloadInvalidError:
        backend_type = ""
    connector = connectors.CONNECTORS.table.get(backend_type)
    if connector is not None and connector.extra is not None:
        return connector.extra
    return source_extra(dataset_dir)


def missing_extra(dataset_dir: Path) -> Extra | None:
    extra = dataset_extra(dataset_dir)
    return extra if extra is not None and not installed(extra) else None


def _held_block(dataset_dir: Path, held: Bank | None) -> StartBlock | None:
    """Over the rows the account HOLDS; one not held opens on a fetch, which needs a source."""
    if held is not None:
        return rows_block(held.samples)
    return None if has_source(dataset_dir) else StartBlock.EMPTY


@dataclass(frozen=True, slots=True)
class DatasetReading:
    """What the listing serves of one dataset: never its rows, which stay with the draft."""

    n_held: int | None
    block: StartBlock | None


def dataset_reading(stores: Stores, name: str) -> DatasetReading:
    """A SURVEY: one unreadable dataset lists as blocked by that, never as startable or absent."""
    dataset = resolve_dataset(stores, name)
    # Asked before any row is read: reading one may import the very extra that is absent.
    if missing_extra(dataset.directory) is not None:
        return DatasetReading(None, StartBlock.MISSING_EXTRA)
    try:
        held = held_bank(stores, dataset)
    except BackendUnreachableError:
        return DatasetReading(None, StartBlock.BACKEND_UNREACHABLE)
    except (ValueError, OSError):
        logger.exception("datasets: %s could not be read for the listing", name)
        return DatasetReading(None, StartBlock.UNREADABLE)
    n_held = None if held is None else len(held.samples)
    return DatasetReading(n_held, _held_block(dataset.directory, held))


def checkin_bank(stores: Stores, name: str) -> Bank:
    """The bank a check-in opens on, refused with the reason the listing serves."""
    dataset = resolve_dataset(stores, name)
    if missing_extra(dataset.directory) is not None:
        raise DatasetUnstartableError(name, StartBlock.MISSING_EXTRA)
    held = held_bank(stores, dataset)
    if (block := _held_block(dataset.directory, held)) is not None:
        raise DatasetUnstartableError(name, block)
    if held is not None:
        return held
    fetched = fetch_bank(stores, dataset)
    if (block := rows_block(fetched.samples)) is not None:
        raise DatasetUnstartableError(name, block)
    return fetched


def upload_source(draft: DraftCampaign, upload: UploadBasis) -> dict[str, Any]:
    """The definition names the kept upload and the cut; the rows stay in the upload alone."""
    return {
        "recipe": "table",
        "upload": upload.kept,
        "query": draft.column_query,
        "truth": draft.column_ground_truth,
    }


def draft_samples(stores: Stores, draft: DraftCampaign) -> Sequence[Sample] | None:
    """What the draft's Start partitions; ``None`` while it has none to read: a read never fetches
    a bank, and never cuts an upload by columns the gate has not confirmed."""
    match draft.basis:
        case UploadBasis() as upload:
            if not columns_confirmed(draft):
                return None
            return cut_bank(stores, upload_source(draft, upload))
        case DatasetBasis(dataset=name):
            held = held_bank(stores, resolve_dataset(stores, name))
            return None if held is None else held.samples
        case _:
            assert_never(draft.basis)


def checkin_rows(stores: Stores, draft: DraftCampaign) -> list[dict[str, str]]:
    match draft.basis:
        case UploadBasis(kept=kept):
            path = stores.tenant_datasets.upload_path(kept)
            return list(read_tabular(path.read_bytes(), path.name).rows)
        case DatasetBasis(dataset=name):
            return labelled_rows(checkin_bank(stores, name).samples)
        case _:
            assert_never(draft.basis)


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
