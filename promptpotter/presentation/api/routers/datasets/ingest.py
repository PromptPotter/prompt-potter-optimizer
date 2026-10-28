from __future__ import annotations

from typing import Annotated

from fastapi import File, Form, Request, UploadFile
from pydantic import Field

from promptpotter.application.commands.dispatcher import CommandCall
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.draft_patch import candidate_library_from_column
from promptpotter.application.datasets.ingest import (
    MAX_UPLOAD_BYTES,
    draft_from_dataset_name,
    ingest_draft,
)
from promptpotter.application.datasets.tabular_ingest import parse_candidate_library
from promptpotter.domain.command_kinds import EditDraftCampaignPayload
from promptpotter.domain.draft_edit import EditDraftPatch
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import IdempotencyKeyDep, StoresDep
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import ContentTooLargeError


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
        File(
            description="Any tabular file (delimited text in any encoding, JSON, JSONL, "
            "XLSX, XLS, ODS), read off its bytes; any columns."
        ),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> DraftCampaignWire:
    """Parse an uploaded tabular file into a durable check-in draft; ``/commands/start-checkin`` mints its campaign."""
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)

    draft = await ingest_draft(stores=stores, blob=blob, filename=file.filename or "", slug=slug)
    return draft_wire(stores, draft)


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
    key: IdempotencyKeyDep,
) -> DraftCampaignWire:
    """Attach an uploaded candidate library to a draft, as an ``edit-draft-campaign``, and return the updated draft."""
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)
    terms = parse_candidate_library(blob, file.filename or "")
    return await dispatch_draft_patch(stores, _candidate_library_call(draft_id, terms, key))


class _BuildLibraryBody(StrictModel):
    """Body for building a candidate library from one of the draft's own columns."""

    # Bounded as `domain/command_kinds.py::DraftAddressedPayload` bounds it.
    draft_id: str = Field(min_length=8, max_length=128)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post("/draft/candidate-library/from-column", response_model=DraftCampaignWire)
async def build_candidate_library_from_column(
    stores: StoresDep,
    body: _BuildLibraryBody,
    key: IdempotencyKeyDep,
) -> DraftCampaignWire:
    """Build a draft's candidate library from the distinct values of one of its own columns and return the updated draft."""
    terms = candidate_library_from_column(stores, body.draft_id, body.column)
    return await dispatch_draft_patch(stores, _candidate_library_call(body.draft_id, terms, key))


@datasets_router.post("/{name:path}/draft", response_model=DraftCampaignWire)
async def draft_from_existing_dataset(name: str, stores: StoresDep) -> DraftCampaignWire:
    """Open an authored dataset's files as a prefilled, durable check-in draft."""
    draft = await draft_from_dataset_name(stores=stores, dataset_name=name)
    return draft_wire(stores, draft)
