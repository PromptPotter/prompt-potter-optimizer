from __future__ import annotations

import inspect
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.routing import APIRoute

from promptpotter.application.commands import launching, workspace_edits
from promptpotter.application.commands.dispatcher import (
    CommandCall,
    CommandDispatcher,
    command_answer,
)
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.commands.origin_resolving import (
    ResolveOriginResponse,
    dispatch_origin_resolution,
)
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.jobs.launcher.checkin import StartCheckinResponse
from promptpotter.application.jobs.launcher.head_to_head import (
    HeadToHeadLaunched,
    HeadToHeadStarted,
)
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    EditDraftCampaignPayload,
    IngestUploadPayload,
    MintHeadToHeadPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.presentation.api.deps import (
    IdempotencyKeyDep,
    JobRegistryDep,
    StoresDep,
    bounded_read,
)
from promptpotter.shared.errors import PayloadInvalidError

commands_router = APIRouter(prefix="/commands", tags=["Commands"])


@commands_router.post(
    f"/{CommandKind.EDIT_DRAFT_CAMPAIGN}",
    response_model=DraftCampaignWire,
    name=CommandKind.EDIT_DRAFT_CAMPAIGN,
)
async def edit_draft_campaign(
    stores: StoresDep, payload: EditDraftCampaignPayload, key: IdempotencyKeyDep
) -> DraftCampaignWire:
    """Sparse-patch a `DraftFile` and return its full post-mutation shape."""
    return await dispatch_draft_patch(stores, CommandCall(payload, key))


@commands_router.post(
    f"/{CommandKind.RESOLVE_ORIGIN}",
    response_model=ResolveOriginResponse,
    name=CommandKind.RESOLVE_ORIGIN,
)
async def resolve_origin(
    stores: StoresDep, payload: ResolveOriginPayload, key: IdempotencyKeyDep
) -> ResolveOriginResponse:
    """Run one origin-resolver turn against a draft, synchronously, and return ``{resolution, draft}``."""
    return await dispatch_origin_resolution(stores, CommandCall(payload, key))


@commands_router.post(
    f"/{CommandKind.START_CHECKIN}",
    response_model=StartCheckinResponse | HeadToHeadStarted,
    name=CommandKind.START_CHECKIN,
)
async def start_checkin(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    payload: StartCheckinPayload,
    key: IdempotencyKeyDep,
) -> StartCheckinResponse | HeadToHeadStarted:
    """Mint the draft's campaign and root cycle, close the draft and detach the loop; with
    `head_to_head`, one campaign per arm, each detached.

    An incomplete origin answers 422 and the draft stays open.
    """
    launched = await dispatch_start_checkin(
        stores, CommandCall(payload, key), job_registry=job_registry
    )
    if isinstance(launched, HeadToHeadLaunched):
        return launched.started
    return StartCheckinResponse(job_id=launched.launch.job_id, hop=launched.launch.hop)


@commands_router.post(
    f"/{CommandKind.MINT_HEAD_TO_HEAD}",
    response_model=HeadToHeadStarted,
    name=CommandKind.MINT_HEAD_TO_HEAD,
)
async def mint_head_to_head(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    payload: MintHeadToHeadPayload,
    key: IdempotencyKeyDep,
) -> HeadToHeadStarted:
    """Mint and detach every arm, or none; a retry under the same key answers the same arms."""
    dispatcher = CommandDispatcher(stores, job_registry=job_registry)
    outcome = await dispatcher.answer(launching.mint_head_to_head, CommandCall(payload, key))
    return outcome.result.started


@commands_router.post(
    f"/{CommandKind.INGEST_UPLOAD}",
    response_model=DraftCampaignWire,
    name=CommandKind.INGEST_UPLOAD,
)
async def ingest_upload(
    stores: StoresDep,
    key: IdempotencyKeyDep,
    file: Annotated[
        UploadFile,
        File(
            description="Any tabular file (delimited text in any encoding, JSON, JSONL, "
            "XLSX, XLS, ODS), read off its bytes; any columns."
        ),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> DraftCampaignWire:
    """The kind's payload as a form: the file is its ``blob`` and ``filename``."""
    fields = {
        "blob": await bounded_read(file),
        "filename": file.filename or "",
        "slug": slug,
    }
    payload = PayloadInvalidError.validating(IngestUploadPayload, fields)
    outcome = await CommandDispatcher(stores).answer(
        workspace_edits.ingest_upload, CommandCall(payload, key)
    )
    return outcome.result


class _Dispatching:
    """The kind's payload is the body; the answer is the model the kind applies, where it applies
    one, else the `CommandRecord` it appended."""

    def __init__(self, model: type[CommandPayload], answers: object | None) -> None:
        keyword = inspect.Parameter.KEYWORD_ONLY
        params = [
            inspect.Parameter("job_registry", keyword, annotation=JobRegistryDep),
            inspect.Parameter("stores", keyword, annotation=StoresDep),
            inspect.Parameter("payload", keyword, annotation=model),
            inspect.Parameter("key", keyword, annotation=IdempotencyKeyDep),
        ]
        self.answers = answers
        self.__signature__ = inspect.Signature(
            params, return_annotation=answers or CommandAcceptedBody
        )

    async def __call__(
        self,
        *,
        job_registry: JobRegistryDep,
        stores: StoresDep,
        payload: CommandPayload,
        key: IdempotencyKeyDep,
    ) -> object:
        dispatcher = CommandDispatcher(stores, job_registry=job_registry)
        outcome = await dispatcher.dispatch(CommandCall(payload, key))
        return outcome.accepted if self.answers is None else outcome.result


# Each kind is PATHED and NAMED by its `CommandKind`, so no route names a kind no capability gates
# and no ledger records.
_TYPED = {route.path for route in commands_router.routes if isinstance(route, APIRoute)}
for _kind in CommandKind:
    if f"/commands/{_kind}" not in _TYPED:
        _answers = command_answer(_kind)
        commands_router.add_api_route(
            f"/{_kind}",
            _Dispatching(PAYLOAD_MODEL_FOR_KIND[_kind], _answers),
            methods=["POST"],
            name=_kind,
            response_model=_answers or CommandAcceptedBody,
            status_code=200 if _answers else 202,
        )
