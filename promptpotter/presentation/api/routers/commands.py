from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.routing import APIRoute

from promptpotter.application.commands import workspace_edits
from promptpotter.application.commands.dispatcher import (
    CommandCall,
    CommandDispatcher,
    command_answer,
)
from promptpotter.application.commands.launching import launched
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.domain.command_kinds import (
    COMMAND_ID_CHARS,
    PAYLOAD_MODEL_FOR_KIND,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    IngestUploadPayload,
)
from promptpotter.domain.run_records import LaunchAnswer
from promptpotter.presentation.api.deps import (
    IdempotencyKeyDep,
    JobRegistryDep,
    StoresDep,
    bounded_read,
)
from promptpotter.shared.errors import PayloadInvalidError

commands_router = APIRouter(prefix="/commands", tags=["Commands"])


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


# The id a caller files under, so it names the act's `cancel` before the act answers; absent,
# `CommandCall` mints one.
CommandIdHeader = Annotated[
    str | None, Header(alias="Command-Id", min_length=1, max_length=COMMAND_ID_CHARS)
]


class _Dispatching:
    """The kind's payload is the body; the answer is the outcome's (`CommandOutcome.answer`), a
    launch's once it is launched: a Start's mint closed, unless its job queued."""

    def __init__(self, model: type[CommandPayload], answers: object | None) -> None:
        keyword = inspect.Parameter.KEYWORD_ONLY
        params = [
            inspect.Parameter("job_registry", keyword, annotation=JobRegistryDep),
            inspect.Parameter("stores", keyword, annotation=StoresDep),
            inspect.Parameter("payload", keyword, annotation=model),
            inspect.Parameter("key", keyword, annotation=IdempotencyKeyDep),
            inspect.Parameter("command_id", keyword, annotation=CommandIdHeader, default=None),
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
        command_id: CommandIdHeader = None,
    ) -> object:
        dispatcher = CommandDispatcher(stores, job_registry=job_registry)
        call = CommandCall(payload, key)
        if command_id is not None:
            call = replace(call, command_id=command_id)
        outcome = await dispatcher.dispatch(call)
        if self.answers is LaunchAnswer:
            outcome = replace(outcome, result=await launched(outcome.result))
        return outcome.answer


# Each kind is PATHED and NAMED by its `CommandKind`, so no route names a kind no capability gates
# and the command log never files.
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
