from __future__ import annotations

from typing import Annotated

from fastapi import File, Form, UploadFile
from pydantic import Field

from promptpotter.application.commands.dispatcher import CommandCall
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.datasets.draft_patch import candidate_library_from_column
from promptpotter.application.datasets.tabular_ingest import parse_candidate_library
from promptpotter.domain.command_kinds import CommandKind, EditDraftCampaignPayload, UploadBytes
from promptpotter.domain.draft_edit import EditDraftPatch
from promptpotter.domain.strict_model import RequestBody, StrictModel
from promptpotter.presentation.api.deps import IdempotencyKeyDep, StoresDep, bounded_read
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import PayloadInvalidError


def _candidate_library_call(
    draft_id: str, terms: tuple[str, ...], idempotency_key: str
) -> CommandCall[EditDraftCampaignPayload]:
    patch = EditDraftPatch(candidate_library=list(terms))
    return CommandCall(EditDraftCampaignPayload(draft_id=draft_id, patch=patch), idempotency_key)


class _LibraryFile(StrictModel):
    """The uploaded library's bytes, under every upload's one bound."""

    blob: UploadBytes


@datasets_router.post(
    "/draft/candidate-library",
    response_model=DraftCampaignWire,
    name=CommandKind.EDIT_DRAFT_CAMPAIGN,
)
async def upload_candidate_library(
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
    blob = PayloadInvalidError.validating(_LibraryFile, {"blob": await bounded_read(file)}).blob
    terms = parse_candidate_library(blob, file.filename or "")
    return await dispatch_draft_patch(stores, _candidate_library_call(draft_id, terms, key))


class _BuildLibraryBody(RequestBody):
    """Body for building a candidate library from one of the draft's own columns."""

    # Bounded as `domain/command_kinds.py::DraftAddressedPayload` bounds it.
    draft_id: str = Field(min_length=8, max_length=128)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post(
    "/draft/candidate-library/from-column",
    response_model=DraftCampaignWire,
    name=CommandKind.EDIT_DRAFT_CAMPAIGN,
)
async def build_candidate_library_from_column(
    stores: StoresDep,
    body: _BuildLibraryBody,
    key: IdempotencyKeyDep,
) -> DraftCampaignWire:
    """Build a draft's candidate library from the distinct values of one of its own columns and return the updated draft."""
    terms = candidate_library_from_column(stores, body.draft_id, body.column)
    return await dispatch_draft_patch(stores, _candidate_library_call(body.draft_id, terms, key))
