from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import File, Form, Response, UploadFile
from pydantic import Field

from promptpotter.application.commands.dispatcher import (
    CommandCall,
    CommandDispatcher,
    CommandOutcome,
)
from promptpotter.application.commands.draft_editing import edit_draft_campaign
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.datasets.draft_patch import candidate_library_from_column
from promptpotter.application.datasets.tabular_ingest import parse_candidate_library
from promptpotter.domain.command_kinds import CommandKind, EditDraftCampaignPayload, UploadBytes
from promptpotter.domain.draft_edit import EditDraftPatch
from promptpotter.domain.strict_model import RequestBody, StrictModel
from promptpotter.presentation.api.deps import (
    CommandIdDep,
    IdempotencyKeyDep,
    StoresDep,
    bounded_read,
    command_answered,
)
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores


async def _attach_library(
    stores: Stores, draft_id: str, terms: tuple[str, ...], idempotency_key: str, command_id: str
) -> CommandOutcome[DraftCampaignWire]:
    patch = EditDraftPatch(candidate_library=list(terms))
    payload = EditDraftCampaignPayload(draft_id=draft_id, patch=patch)
    call = CommandCall(payload, idempotency_key, command_id)
    return await CommandDispatcher(stores).answer(edit_draft_campaign, call)


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
    command_id: CommandIdDep,
    response: Response,
) -> DraftCampaignWire:
    """Attach an uploaded candidate library to a draft, as an ``edit-draft-campaign``, and return the updated draft."""
    blob = PayloadInvalidError.validating(_LibraryFile, {"blob": await bounded_read(file)}).blob
    terms = parse_candidate_library(blob, file.filename or "")
    outcome = await _attach_library(stores, draft_id, terms, key, command_id)
    return command_answered(response, outcome)


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
    command_id: CommandIdDep,
    response: Response,
) -> DraftCampaignWire:
    """Build a draft's candidate library from the distinct values of one of its own columns and return the updated draft."""
    terms = candidate_library_from_column(stores, body.draft_id, body.column)
    outcome = await _attach_library(stores, body.draft_id, terms, key, command_id)
    return command_answered(response, outcome)
