"""Which datasets a new campaign may start from HERE, and which of them a browser check-in can open.

Two questions with two scopes. What the deployment OFFERS (`settings.DATASET_OFFER`) binds every
account but the host's: the host (the terminal, an embedded host program, the claimed browser
account) runs the whole install. Whether a CHECK-IN can open one binds the draft alone: a draft is a
table of labelled rows, so a verifier-graded bank has nothing to put in it and launches from `new`.
The listing and the draft read both off the one row ladder (`loaders.resolve_bank`): a bank the
account holds opens without its source's extra, which only a fetch needs, and no row is read
before the extra its connector runs through is installed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, assert_never

from promptpotter import connectors
from promptpotter.application.datasets.loaders import (
    cut_bank,
    fetch_bank,
    has_source,
    held_bank,
    source_extra,
)
from promptpotter.application.datasets.origin_readiness import columns_confirmed
from promptpotter.application.datasets.tabular_ingest import read_kept_table
from promptpotter.config.paths import install_datasets_root
from promptpotter.config.settings import settings
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile, UploadBasis
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.dataset_start import DatasetUnstartableError, StartBlock
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.identity.default_claim import is_host
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_READ_ERRORS,
    ResolvedDataset,
    dataset_not_found,
    install_dataset,
    is_dataset_dir,
    own_definition,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    FileUnreadableError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.extras import Extra, install_hint, installed

logger = logging.getLogger(__name__)


def offered(stores: Stores, name: str) -> bool:
    offer = settings.dataset_offer
    return (
        offer is None
        or name in offer
        or is_host(stores.identity)
        or own_definition(stores, name) is not None
    )


def offered_dataset(stores: Stores, name: str) -> ResolvedDataset:
    """Where a NEW campaign's dataset resolves: an unoffered one does not exist, as a 404."""
    if not offered(stores, name):
        raise dataset_not_found(stores, name)
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


def _connector_extra(dataset: ResolvedDataset) -> Extra | None:
    """What every cell of the dataset imports beyond core."""
    try:
        return connectors.CONNECTORS.get(runnable_definition(dataset).connector).extra
    except DEFINITION_READ_ERRORS:
        return None


def dataset_extra(dataset: ResolvedDataset) -> Extra | None:
    """What the dataset runs through beyond core: its connector's declaration, else its source's."""
    return _connector_extra(dataset) or source_extra(dataset)


def _absent(extra: Extra | None) -> Extra | None:
    return extra if extra is not None and not installed(extra) else None


def missing_extra(dataset: ResolvedDataset) -> Extra | None:
    return _absent(dataset_extra(dataset))


def _held_block(dataset: ResolvedDataset, held: Bank | None) -> StartBlock | None:
    """Over the rows the account HOLDS; one not held opens on a fetch, which needs a source and
    the extra that source reads through."""
    if held is not None:
        return rows_block(held.samples)
    if not has_source(dataset):
        return StartBlock.EMPTY
    return None if _absent(source_extra(dataset)) is None else StartBlock.MISSING_EXTRA


@dataclass(frozen=True, slots=True)
class DatasetReading:
    """What the listing serves of one dataset: never its rows, which stay with the draft."""

    n_held: int | None
    block: StartBlock | None


def dataset_reading(stores: Stores, name: str) -> DatasetReading:
    """A SURVEY: one unreadable dataset lists as blocked by that, never as startable or absent."""
    dataset = resolve_dataset(stores, name)
    # Asked before any row is read: reading a panel may import the very extra that is absent.
    if _absent(_connector_extra(dataset)) is not None:
        return DatasetReading(None, StartBlock.MISSING_EXTRA)
    try:
        held = held_bank(stores, dataset)
    except BackendUnreachableError:
        return DatasetReading(None, StartBlock.BACKEND_UNREACHABLE)
    except (FileUnreadableError, PayloadInvalidError, ValueError, OSError):
        logger.exception("datasets: %s could not be read for the listing", name)
        return DatasetReading(None, StartBlock.UNREADABLE)
    n_held = None if held is None else len(held.samples)
    return DatasetReading(n_held, _held_block(dataset, held))


def checkin_bank(stores: Stores, name: str) -> Bank:
    """The bank a check-in opens on, refused with the reason the listing serves."""
    dataset = resolve_dataset(stores, name)
    if _absent(_connector_extra(dataset)) is not None:
        raise DatasetUnstartableError(name, StartBlock.MISSING_EXTRA)
    held = held_bank(stores, dataset)
    if (block := _held_block(dataset, held)) is not None:
        raise DatasetUnstartableError(name, block)
    if held is not None:
        return held
    fetched = fetch_bank(stores, dataset)
    if (block := rows_block(fetched.samples)) is not None:
        raise DatasetUnstartableError(name, block)
    return fetched


def upload_source(draft: DraftFile, upload: UploadBasis) -> dict[str, Any]:
    """The definition names the kept upload and the cut; the rows stay in the upload alone."""
    return {
        "recipe": "table",
        "upload": upload.kept,
        "query": draft.column_query,
        "truth": draft.column_ground_truth,
    }


def draft_samples(stores: Stores, draft: DraftFile) -> Sequence[Sample] | None:
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


def checkin_rows(stores: Stores, draft: DraftFile) -> list[dict[str, str]]:
    match draft.basis:
        case UploadBasis(kept=kept):
            return list(read_kept_table(stores.tenant_datasets.upload_path(kept)).rows)
        case DatasetBasis(dataset=name):
            return labelled_rows(checkin_bank(stores, name).samples)
        case _:
            assert_never(draft.basis)


def _offer() -> dict[str, ResolvedDataset]:
    """The install dataset each offered name names, the offer whole or refused: boot and deploy
    both. Only the install's: a research definition is never offered."""
    root = install_datasets_root()
    offer = {name: install_dataset(root, name) for name in sorted(settings.dataset_offer or ())}
    if absent := [name for name, d in offer.items() if not is_dataset_dir(d.directory)]:
        raise NotFoundError(f"DATASET_OFFER names no dataset in this install: {', '.join(absent)}")
    return offer


def offer_extras() -> set[Extra]:
    """What the deployment must install so every dataset it offers can run."""
    return {extra for dataset in _offer().values() if (extra := dataset_extra(dataset))}


def offer_faults() -> list[str]:
    """Each offered dataset whose extra this install lacks, as a line for the boot banner."""
    return [
        f"{name}: needs the `{extra}` extra — {install_hint(extra)}"
        for name, dataset in _offer().items()
        if (extra := missing_extra(dataset)) is not None
    ]
