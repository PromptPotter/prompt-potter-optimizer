"""The host registry served over MCP: list and call it, read its resources, get its prompts, over
stdio to a local host and over Streamable HTTP (`HttpDoor`) on the API.

Every answer is `application/host_surface.py`'s `HostSession`; this module only words it in MCP's
types. A task kind's call answers the Tasks extension's handle: claimed (`resultType: "task"`) where
the request itself declares the extension on a 2026-era wire, and as the call's structured content
everywhere else, since a client MUST reject a claimed result its request did not declare and a
handshake-era wire carries none. `tasks/get`, `tasks/result` and `tasks/cancel` serve both. A
task held at its arms gate asks for its input as one form elicitation per hold: on a 2026-era wire
as `tasks/get`'s input requests, answered by `tasks/update`; on a handshake-era one sent on
`tasks/result`'s stream, tied to the task, where the client declares elicitation. A refusal is its `domain/refusal.py::Refusal` on every answer: a call's error result, every other
request's JSON-RPC error data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast
from urllib.parse import urlsplit

from promptpotter.shared.extras import Extra, require_extra

try:
    import mcp.types as types
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.shared.exceptions import MCPError
    from mcp.shared.message import ServerMessageMetadata
    from starlette.types import Message, Receive, Scope, Send
except ModuleNotFoundError:
    require_extra(Extra.MCP)
    raise

from promptpotter.application import host_surface
from promptpotter.application.account import local_stores
from promptpotter.application.caller import Caller
from promptpotter.application.host_surface import HostAnswer, HostHold, HostSession, HostTask
from promptpotter.config.settings import settings
from promptpotter.domain.refusal import Refusal
from promptpotter.shared.command_result import CommandResult
from promptpotter.shared.errors import PotterError

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
_ACCEPTS_TASKS = types.ClientCapabilities(extensions={TASKS_EXTENSION: {}})
_ELICITS = types.ClientCapabilities(elicitation=types.ElicitationCapability())
# The Tasks spec's tie of a message to the task it serves.
_RELATED_TASK = "io.modelcontextprotocol/related-task"

type Ctx = ServerRequestContext[Any, Any]


def _modern(ctx: Ctx) -> bool:
    return ctx.protocol_version in types.version.MODERN_PROTOCOL_VERSIONS


def _claims_task(ctx: Ctx) -> bool:
    """Per request: a 2026-era envelope declares its own capabilities, so a client advertising the
    extension at discovery may still send a call that accepts no task."""
    return _modern(ctx) and ctx.session.check_client_capability(_ACCEPTS_TASKS)


def _task(task: HostTask) -> types.Task:
    return types.Task(
        task_id=task.task_id,
        status=task.status,
        status_message=task.status_message,
        created_at=task.created_at,
        last_updated_at=task.last_updated_at,
        ttl=None,
    )


def _task_json(task: HostTask) -> dict[str, Any]:
    return _task(task).model_dump(mode="json", by_alias=True, exclude_none=True)


def _text(value: object) -> list[types.ContentBlock]:
    return [types.TextContent(text=json.dumps(value, ensure_ascii=False))]


# JSON-RPC's implementation-defined server range: a throttle passes, so it is no caller's mistake.
_RATE_LIMITED = -32029


def _rpc_code(exc: PotterError) -> int:
    if exc.http_status == 429:
        return _RATE_LIMITED
    return types.INVALID_PARAMS if exc.http_status < 500 else types.INTERNAL_ERROR


def _refused(exc: PotterError) -> MCPError:
    refused = Refusal.of(exc)
    return MCPError(
        code=_rpc_code(exc), message=refused.message, data=refused.model_dump(mode="json")
    )


def refusal_result(exc: PotterError) -> types.CallToolResult:
    """A refusal is the tool's answer, so the model reading it can correct its call."""
    return types.CallToolResult(
        content=_text(Refusal.of(exc).model_dump(mode="json")), is_error=True
    )


def _elicitation(held: HostHold) -> types.ElicitRequest:
    """The hold's one form elicitation, tied to its task."""
    asking = types.ElicitRequestFormParams(
        message=held.message,
        requested_schema=held.requested_schema(),
        meta=cast(types.RequestParamsMeta, {_RELATED_TASK: {"taskId": held.task_id}}),
    )
    return types.ElicitRequest(params=asking)


def _accepted(held: HostHold, reply: object) -> host_surface.AnswerHold | None:
    """A decline, a cancel or no reply answers nothing, and the task stays held."""
    if not isinstance(reply, types.ElicitResult) or reply.action != "accept" or not reply.content:
        return None
    return host_surface.AnswerHold(held, reply.content)


async def _asked(ctx: Ctx, held: HostHold) -> host_surface.AnswerHold | None:
    """A handshake-era wire's ask, on the `tasks/result` request's own stream."""
    reply = await ctx.session.send_request(
        _elicitation(held),
        types.ElicitResult,
        metadata=ServerMessageMetadata(related_request_id=ctx.request_id),
    )
    return _accepted(held, reply)


def _task_read(ctx: Ctx, task: HostTask) -> Any:
    """`tasks/get`'s answer; on a 2026-era wire a held task's carries its holds' input requests."""
    if not (_modern(ctx) and task.holds):
        return types.GetTaskResult(**_task(task).model_dump())
    asking = {
        held.key: _elicitation(held).model_dump(mode="json", by_alias=True, exclude_none=True)
        for held in task.holds
    }
    return {**_task(task).model_dump(mode="json", by_alias=True), "inputRequests": asking}


class UpdateTaskParams(types.RequestParams):
    """The tasks extension's `tasks/update`: a client's answers to a task's input requests, keyed
    as `tasks/get` asked them."""

    task_id: str
    input_responses: types.InputResponses


async def _update(session: HostSession, _ctx: Ctx, params: UpdateTaskParams) -> types.EmptyResult:
    try:
        task = await session.answer(host_surface.TaskStatusOf(params.task_id))
        for held in task.holds:
            if answer := _accepted(held, params.input_responses.get(held.key)):
                await session.answer(answer)
    except PotterError as exc:
        raise _refused(exc) from exc
    return types.EmptyResult()


def _answered(name: str, answer: HostAnswer) -> types.CallToolResult:
    structured = host_surface.host_tool(name).structured(answer.value)
    return types.CallToolResult(content=_text(answer.value), structured_content=structured)


def host_server(session: HostSession) -> Server[Any]:
    """One server per `HostSession`: every request it answers runs as that session's one identity."""

    async def list_tools(
        _ctx: Ctx, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema(),
                    output_schema=None if tool.as_task else tool.output_schema(),
                    # Optional: a call accepting no task gets the handle as its answer.
                    execution=types.ToolExecution(task_support="optional")
                    if tool.as_task
                    else None,
                    # A command may touch the world past this box (a provider's key): the default.
                    annotations=types.ToolAnnotations(
                        read_only_hint=tool.read_only, destructive_hint=tool.destructive
                    ),
                )
                for tool in await session.answer(host_surface.ListTools())
            ]
        )

    async def call_tool(ctx: Ctx, params: types.CallToolRequestParams) -> Any:
        try:
            answer = await session.answer(
                host_surface.CallTool(params.name, params.arguments or {})
            )
        except PotterError as exc:
            return refusal_result(exc)
        if answer.task is None:
            return _answered(params.name, answer)
        task = _task_json(answer.task)
        if _claims_task(ctx):
            return {"resultType": "task", "task": task}
        return types.CallToolResult(
            content=_text({"task": task}), structured_content={"task": task}
        )

    async def get_task(ctx: Ctx, params: types.GetTaskRequestParams) -> Any:
        try:
            task = await session.answer(host_surface.TaskStatusOf(params.task_id))
        except PotterError as exc:
            raise _refused(exc) from exc
        return _task_read(ctx, task)

    async def task_result(
        ctx: Ctx, params: types.GetTaskPayloadRequestParams
    ) -> types.CallToolResult:
        # A client declaring no elicitation is never asked, nor a 2026-era wire, whose input
        # requests ride `tasks/get`: its result waits past every hold.
        asks = not _modern(ctx) and ctx.session.check_client_capability(_ELICITS)
        asked: set[str] = set()
        try:
            while isinstance(
                outcome := await session.answer(
                    host_surface.TaskResultOf(params.task_id, frozenset(asked))
                ),
                HostHold,
            ):
                asked.add(outcome.key)
                if asks and (answer := await _asked(ctx, outcome)) is not None:
                    await session.answer(answer)
        except PotterError as exc:
            raise _refused(exc) from exc
        return _answered(*outcome)

    async def cancel_task(
        _ctx: Ctx, params: types.CancelTaskRequestParams
    ) -> types.CancelTaskResult:
        try:
            task = await session.answer(host_surface.CancelTask(params.task_id))
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.CancelTaskResult(**_task(task).model_dump())

    async def list_resources(
        _ctx: Ctx, _params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        try:
            listed = await session.answer(host_surface.ListResources())
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=r.uri, name=r.name, description=r.description, mime_type="application/json"
                )
                for r in listed
            ]
        )

    async def list_resource_templates(
        _ctx: Ctx, _params: types.PaginatedRequestParams | None
    ) -> types.ListResourceTemplatesResult:
        template = types.ResourceTemplate(
            uri_template=host_surface.RESOURCE_TEMPLATE,
            name="hierarchy",
            description="Any entry by its hierarchy address: a campaign, cycle, round or candidate.",
            mime_type="application/json",
        )
        return types.ListResourceTemplatesResult(resource_templates=[template])

    async def read_resource(
        _ctx: Ctx, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        try:
            value = await session.answer(host_surface.ReadResource(params.uri))
        except PotterError as exc:
            raise _refused(exc) from exc
        text = json.dumps(value, ensure_ascii=False)
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(uri=params.uri, mime_type="application/json", text=text)
            ]
        )

    async def list_prompts(
        _ctx: Ctx, _params: types.PaginatedRequestParams | None
    ) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name=p.name,
                    description=p.description,
                    arguments=[types.PromptArgument(name=a, required=True) for a in p.arguments],
                )
                for p in host_surface.HOST_PROMPTS.values()
            ]
        )

    async def get_prompt(_ctx: Ctx, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        try:
            text = await session.answer(
                host_surface.RenderPrompt(params.name, params.arguments or {})
            )
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.GetPromptResult(
            description=host_surface.HOST_PROMPTS[params.name].description,
            messages=[types.PromptMessage(role="user", content=types.TextContent(text=text))],
        )

    server: Server[Any] = Server(
        "promptpotter",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_resource_templates,
        on_read_resource=read_resource,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )
    server.add_request_handler("tasks/get", types.GetTaskRequestParams, get_task)
    server.add_request_handler("tasks/result", types.GetTaskPayloadRequestParams, task_result)
    server.add_request_handler("tasks/cancel", types.CancelTaskRequestParams, cancel_task)
    server.add_request_handler("tasks/update", UpdateTaskParams, partial(_update, session))
    server.extensions[TASKS_EXTENSION] = {}
    return server


async def serve_stdio(caller: Caller) -> None:
    """Until the host closes stdin; stdout is the wire, so nothing else may print to it."""
    server = host_server(HostSession(caller))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


_SESSION_ID = MCP_SESSION_ID_HEADER.encode()


@dataclass
class _Manager:
    """One authority's session manager, the task serving it, its requests in flight, and the door's
    own table of its sessions: each open one to the loop time it last answered."""

    manager: StreamableHTTPSessionManager
    serving: asyncio.Task[None]
    in_flight: int = 0
    sessions: dict[bytes, float] = field(default_factory=dict)

    def answered(self, scope: Scope, start: Message, now: float) -> None:
        """A session opens on the reply naming it; its DELETE, or a 404 (the SDK forgot it), ends it."""
        named = dict(scope["headers"]).get(_SESSION_ID) or dict(start["headers"]).get(_SESSION_ID)
        status = start["status"]
        if named and (status == 404 or (status < 400 and scope["method"] == "DELETE")):
            self.sessions.pop(named, None)
        elif named and status < 400:
            self.sessions[named] = now

    def ended(self, now: float) -> bool:
        """A session past the SDK's idle timeout ends here as it ends there, on the loop's clock."""
        idle = self.manager.session_idle_timeout or float("inf")
        self.sessions = {named: seen for named, seen in self.sessions.items() if now - seen < idle}
        return not self.in_flight and not self.sessions


class HttpDoor:
    """`host_server` over Streamable HTTP, its caller the one its mount's gate resolved
    (`deps.py::mounted_caller`) onto the request's state. One session manager per authority (its
    account, its token and the rungs they hold), so every session a manager holds runs as that one
    caller: a request on a session another opened finds no session there (404). A manager whose
    sessions all ended in the door's own table (`_Manager.sessions`) leaves at the door's next
    request, so the map holds the authorities with a session open. Its managers run in the API's
    lifespan (`running`), never at import; DNS-rebinding protection reads the install's allowed
    origins."""

    def __init__(self) -> None:
        self._managers: dict[tuple[object, ...], _Manager] = {}
        self._opening = asyncio.Lock()
        self._serving: set[asyncio.Task[None]] | None = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        serving: set[asyncio.Task[None]] = set()
        self._serving = serving
        try:
            yield
        finally:
            self._serving = None
            self._managers.clear()
            for task in serving:
                task.cancel()
            await asyncio.gather(*serving, return_exceptions=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        caller: Caller = scope["state"]["caller"]
        opened = await self._manager(caller)
        # A request cut before it answered moves no session.
        start: Message = {"status": 500, "headers": []}

        async def watched(message: Message) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            await send(message)

        try:
            await opened.manager.handle_request(scope, receive, watched)
        finally:
            async with self._opening:
                opened.in_flight -= 1
                opened.answered(scope, start, now := asyncio.get_running_loop().time())
                # A manager with no request in flight and no session open in its table leaves.
                for authority, held in list(self._managers.items()):
                    if held.ended(now):
                        del self._managers[authority]
                        held.serving.cancel()

    async def _manager(self, caller: Caller) -> _Manager:
        who = caller.stores.identity
        authority = (who.user_id, who.token_id, who.carried_by, who.capabilities)
        async with self._opening:
            if (opened := self._managers.get(authority)) is None:
                assert self._serving is not None, "the door runs in the API's lifespan"
                origins = settings.allowed_origins_list
                guard = TransportSecuritySettings(
                    allowed_hosts=[urlsplit(origin).netloc for origin in origins],
                    allowed_origins=origins,
                )
                manager = StreamableHTTPSessionManager(
                    host_server(HostSession(caller)), security_settings=guard
                )
                started = asyncio.Event()
                serving = asyncio.create_task(_served(manager, started))
                self._serving.add(serving)
                serving.add_done_callback(self._serving.discard)
                await started.wait()
                if serving.done():
                    serving.result()
                opened = self._managers[authority] = _Manager(manager, serving)
            opened.in_flight += 1
        return opened


async def _served(manager: StreamableHTTPSessionManager, started: asyncio.Event) -> None:
    """Until the door stops; *started* is set once it serves, or once it could not."""
    try:
        async with manager.run():
            started.set()
            await asyncio.Event().wait()
    finally:
        started.set()


async def cmd_mcp(args: argparse.Namespace) -> CommandResult:
    """The `mcp` verb, here so only it imports this module: the guard above refuses an install
    without the extra by name, and no other verb reaches it. As the principal every other verb
    acts as: a host is one more door of the terminal's."""
    await serve_stdio(Caller.holding(local_stores(tenant=args.tenant)))
    # Nothing to report: stdout was the wire, and its host has hung up.
    return CommandResult(human="")
