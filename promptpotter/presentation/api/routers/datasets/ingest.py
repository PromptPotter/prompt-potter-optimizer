from __future__ import annotations

from typing import Annotated

from fastapi import File, Form, Header, Request, UploadFile
from pydantic import Field

from promptpotter.application.commands.dispatcher import CommandCall
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.payloads import EditDraftCampaignPayload
from promptpotter.application.datasets.csv_ingest import (
    parse_candidate_library,
)
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.draft_campaign import EditDraftPatch
from promptpotter.application.datasets.draft_patch import candidate_library_from_column
from promptpotter.application.datasets.ingest import (
    MAX_UPLOAD_BYTES,
    SlugTakenError,
    draft_from_dataset_name,
    ingest_draft,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import (
    StoresDep,
)
from promptpotter.presentation.api.routers.commands import ensure_idempotency_key
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import (
    ConflictError,
    ContentTooLargeError,
    PayloadInvalidError,
)


def _candidate_library_call(
    draft_id: str, terms: tuple[str, ...], idempotency_key: str
) -> CommandCall[EditDraftCampaignPayload]:
    patch = EditDraftPatch(candidate_library=list(terms))
    return CommandCall(EditDraftCampaignPayload(draft_id=draft_id, patch=patch), idempotency_key)


def _too_large(observed: int | str) -> ContentTooLargeError:
    return ContentTooLargeError(
        f"Upload {observed} bytes exceeds the per-file cap of {MAX_UPLOAD_BYTES} bytes."
    )


async def _read_capped(request: Request, upload: UploadFile, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise _too_large(declared)

    # Still counted while streaming: the header can lie or be absent.
    chunk_size = 1024 * 1024
    buf = bytearray()
    while chunk := await upload.read(chunk_size):
        buf.extend(chunk)
        if len(buf) > cap:
            raise _too_large(len(buf))
    return bytes(buf)


@datasets_router.post("/ingest", response_model=DraftCampaignWire)
async def ingest_dataset(
    request: Request,
    stores: StoresDep,
    file: Annotated[
        UploadFile,
        File(description="Tabular upload (CSV/TSV/JSON/JSONL/XLSX); any columns."),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> DraftCampaignWire:
    """Parse an uploaded tabular file, mint a durable check-in campaign and return its draft.

    ``draft_id`` is the ``campaign_id``; nothing runs until ``/commands/start-checkin``.
    """
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)

    try:
        draft = await ingest_draft(
            stores=stores, blob=blob, filename=file.filename or "", slug=slug
        )
    except ValueError as exc:
        raise PayloadInvalidError(str(exc), details={"reason": "bad_slug"}) from None
    except SlugTakenError as exc:
        raise ConflictError(
            f"A dataset named '{exc.slug}' already exists.",
            code="slug_collision",
            details={"slug": exc.slug, "suggested_slug": exc.suggested},
        ) from None
    return draft_wire(draft, stores.base_dir)


@datasets_router.post("/draft/candidate-library", response_model=DraftCampaignWire)
async def upload_candidate_library(
    request: Request,
    stores: StoresDep,
    file: Annotated[
        UploadFile,
        File(description="Target library — one entry per line, or a single-column CSV/Excel."),
    ],
    draft_id: Annotated[
        str, Form(description="Draft to attach the library to.", min_length=8, max_length=128)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DraftCampaignWire:
    """Attach an uploaded candidate library to a draft, as an ``edit-draft-campaign``, and return the updated draft.

    ``draft_id`` is the check-in campaign id.
    """
    idemp = ensure_idempotency_key(idempotency_key)
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)
    terms = parse_candidate_library(blob, file.filename or "")
    if not terms:
        raise PayloadInvalidError(
            "The candidate library has no usable entries (every line was blank).",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return await dispatch_draft_patch(stores, _candidate_library_call(draft_id, terms, idemp))


class _BuildLibraryBody(StrictModel):
    """Body for building a candidate library from one of the draft's own columns."""

    # `draft_id` is the owning `campaign_id`: bounded as `commands/payloads.py::_CheckinPayload` bounds it.
    draft_id: str = Field(min_length=8, max_length=128)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post("/draft/candidate-library/from-column", response_model=DraftCampaignWire)
async def build_candidate_library_from_column(
    stores: StoresDep,
    body: _BuildLibraryBody,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DraftCampaignWire:
    """Build a draft's candidate library from the distinct values of one of its own columns and return the updated draft."""
    idemp = ensure_idempotency_key(idempotency_key)
    terms = candidate_library_from_column(stores, body.draft_id, body.column)
    return await dispatch_draft_patch(stores, _candidate_library_call(body.draft_id, terms, idemp))


@datasets_router.post("/{name}/draft", response_model=DraftCampaignWire)
async def draft_from_existing_dataset(name: str, stores: StoresDep) -> DraftCampaignWire:
    """Open an authored dataset's files as a prefilled, durable check-in campaign.

    ``draft_id`` is the ``campaign_id``; nothing runs until ``/commands/start-checkin``.
    """
    draft = await draft_from_dataset_name(stores=stores, dataset_name=name)
    return draft_wire(draft, stores.base_dir)
