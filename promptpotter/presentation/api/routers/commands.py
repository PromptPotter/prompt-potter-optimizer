from __future__ import annotations

import inspect
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from promptpotter.application.commands import workspace_edits
from promptpotter.application.commands.dispatcher import (
    CommandCall,
    CommandDispatcher,
    command_answer,
)
from promptpotter.application.commands.launching import launched
from promptpotter.application.cycle_reads import (
    FEED_VERSIONS_HEADER,
    FeedVersion,
    feed_moves_said,
    feed_versions,
)
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.read_workers import off_loop
from promptpotter.domain.command_effects import LaunchAnswer
from promptpotter.domain.command_kinds import (
    COMMAND_ID_CHARS,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    Door,
    IngestUploadPayload,
)
from promptpotter.presentation.api.deps import (
    IdempotencyKeyDep,
    JobRegistryDep,
    StoresDep,
    bounded_read,
)
from promptpotter.shared.errors import PayloadInvalidError


def refused_payload(
    exc: RequestValidationError, model: type[BaseModel] | None = None
) -> PayloadInvalidError:
    """Located in the payload, as every other entry point's refusal of the same model is; *model*
    names the form a union field refuses in at every door (`validating`)."""
    located = (
        {**error, "loc": error["loc"][1:]} if error["loc"][:1] == ("body",) else error
        for error in exc.errors()
    )
    return PayloadInvalidError.refusing(located, model=model)


class _CommandRoute(APIRoute):
    """A command's route refuses its body by the payload model it declares."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()
        # FastAPI sets `dependant` on the instance outside `APIRoute`'s declared attributes.
        bodies = [p.field_info.annotation for p in vars(self)["dependant"].body_params]
        body = bodies[0] if len(bodies) == 1 else None
        model = body if isinstance(body, type) and issubclass(body, BaseModel) else None

        async def refusing(request: Request) -> Response:
            try:
                return await handle(request)
            except RequestValidationError as exc:
                raise refused_payload(exc, model) from exc

        return refusing


commands_router = APIRouter(prefix="/commands", tags=["Commands"], route_class=_CommandRoute)


@commands_router.post(
    f"/{CommandKind.INGEST_UPLOAD}",
    response_model=DraftCampaignWire,
    name=CommandKind.INGEST_UPLOAD,
)
async def ingest_upload(
    response: Response,
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
    dispatcher = CommandDispatcher(stores)
    stood = await _stood(dispatcher)
    outcome = await dispatcher.answer(
        workspace_edits.ingest_upload, CommandCall(payload, key, door=Door.API)
    )
    await _stamped(response, dispatcher, stood)
    return outcome.result


async def _stood(dispatcher: CommandDispatcher) -> dict[str, FeedVersion]:
    return await off_loop(feed_versions, dispatcher.stores, dispatcher.job_registry)


async def _stamped(
    response: Response, dispatcher: CommandDispatcher, stood: dict[str, FeedVersion]
) -> None:
    """The ack names the versions its write moved (`read-cache.ts::reached`)."""
    response.headers[FEED_VERSIONS_HEADER] = await off_loop(
        feed_moves_said, dispatcher.stores, dispatcher.job_registry, stood
    )


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
            inspect.Parameter("response", keyword, annotation=Response),
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
        response: Response,
        job_registry: JobRegistryDep,
        stores: StoresDep,
        payload: CommandPayload,
        key: IdempotencyKeyDep,
        command_id: CommandIdHeader = None,
    ) -> object:
        dispatcher = CommandDispatcher(stores, job_registry=job_registry)
        call = CommandCall(payload, key, door=Door.API)
        if command_id is not None:
            call = replace(call, command_id=command_id)
        stood = await _stood(dispatcher)
        outcome = await dispatcher.dispatch(call)
        if self.answers is LaunchAnswer:
            ran = await launched(outcome.result, outcome.payload, dispatcher, call.door)
            outcome = replace(outcome, result=ran)
        await _stamped(response, dispatcher, stood)
        return outcome.answer


# Each kind is PATHED and NAMED by its `CommandKind`, so no route names a kind no capability gates
# and the command log never files.
_TYPED = {route.path for route in commands_router.routes if isinstance(route, APIRoute)}
for _kind in CommandKind:
    if f"/commands/{_kind}" not in _TYPED:
        _answers = command_answer(_kind)
        commands_router.add_api_route(
            f"/{_kind}",
            _Dispatching(_kind.payload_model, _answers),
            methods=["POST"],
            name=_kind,
            response_model=_answers or CommandAcceptedBody,
            status_code=200 if _answers else 202,
        )
