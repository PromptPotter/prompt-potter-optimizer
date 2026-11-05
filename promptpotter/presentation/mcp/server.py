"""The host registry served over MCP: list and call it, read its resources, get its prompts.

Every answer is `application/host_surface.py`'s `HostSession`; this module only words it in MCP's
types, and is the one place a command is called a task. A task kind's call that asks to run as a
task is started (`HostSession.start_command`) and answers the Tasks extension's handle, its command
id: claimed (`resultType: "task"`) on a connection that speaks the extensions vocabulary, and as the
call's structured content on a handshake-era one, which has no claimed result to carry it.
`tasks/get` is the `command-status` read, `tasks/result` the command's answer replayed off its
record (`HostSession.command_result`), `tasks/cancel` the `cancel-command` command. A refusal keeps its
`PotterError.code` on every answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from promptpotter.shared.extras import Extra, require_extra

try:
    import mcp.types as types
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.shared.exceptions import MCPError
except ModuleNotFoundError:
    require_extra(Extra.MCP)
    raise

from promptpotter.application import host_surface
from promptpotter.application.caller import Caller
from promptpotter.application.command_status import CommandStatus
from promptpotter.application.host_surface import HostSession
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    from pydantic import JsonValue

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"

type Ctx = ServerRequestContext[Any, Any]


def _task(task: CommandStatus) -> types.Task:
    return types.Task(
        task_id=task.command_id,
        status=task.status,
        status_message=task.said,
        created_at=task.created_at,
        last_updated_at=task.last_updated_at,
        ttl=None,
    )


def _task_json(task: CommandStatus) -> dict[str, Any]:
    return _task(task).model_dump(mode="json", by_alias=True, exclude_none=True)


def _text(value: object) -> list[types.ContentBlock]:
    return [types.TextContent(text=json.dumps(value, ensure_ascii=False))]


def _refused(exc: PotterError) -> MCPError:
    code = types.INVALID_PARAMS if exc.http_status < 500 else types.INTERNAL_ERROR
    return MCPError(code=code, message=str(exc), data={"code": exc.code})


def _answered(name: str, value: JsonValue) -> types.CallToolResult:
    structured = host_surface.host_tool(name).structured(value)
    return types.CallToolResult(content=_text(value), structured_content=structured)


def _refusal(exc: PotterError) -> types.CallToolResult:
    """A refusal is the tool's answer, so the model reading it can correct its call."""
    refused = {"code": exc.code, "message": str(exc)}
    return types.CallToolResult(content=_text(refused), is_error=True)


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
                    output_schema=tool.output_schema(),
                    # Called without a task, it answers inline, as `tasks/result` answers it.
                    execution=types.ToolExecution(task_support="optional")
                    if tool.startable
                    else None,
                    # A command may touch the world past this box (a provider's key): the default.
                    annotations=types.ToolAnnotations(
                        read_only_hint=tool.read_only, destructive_hint=tool.destructive
                    ),
                )
                for tool in host_surface.HOST_TOOLS.values()
            ]
        )

    async def call_tool(ctx: Ctx, params: types.CallToolRequestParams) -> Any:
        tool = host_surface.HOST_TOOLS.get(params.name)
        as_task = params.task is not None and tool is not None and tool.startable
        try:
            if not as_task:
                return _answered(
                    params.name, await session.call_tool(params.name, params.arguments or {})
                )
            started = await session.start_command(params.name, params.arguments or {})
        except PotterError as exc:
            return _refusal(exc)
        task = _task_json(started)
        if ctx.protocol_version in types.version.MODERN_PROTOCOL_VERSIONS:
            return {"resultType": "task", "task": task}
        return types.CallToolResult(
            content=_text({"task": task}), structured_content={"task": task}
        )

    async def get_task(_ctx: Ctx, params: types.GetTaskRequestParams) -> types.GetTaskResult:
        try:
            task = await session.command_status(params.task_id)
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.GetTaskResult(**_task(task).model_dump())

    async def task_result(
        _ctx: Ctx, params: types.GetTaskPayloadRequestParams
    ) -> types.CallToolResult:
        try:
            status, value = await session.command_result(params.task_id)
        except PotterError as exc:
            return _refusal(exc)
        return _answered(status.kind, value)

    async def cancel_task(
        _ctx: Ctx, params: types.CancelTaskRequestParams
    ) -> types.CancelTaskResult:
        try:
            task = await session.cancel_command(params.task_id)
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.CancelTaskResult(**_task(task).model_dump())

    async def list_resources(
        _ctx: Ctx, _params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        try:
            listed = await session.resources()
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
            description="Any entry by its hierarchy address: a campaign, course, round or candidate.",
            mime_type="application/json",
        )
        return types.ListResourceTemplatesResult(resource_templates=[template])

    async def read_resource(
        _ctx: Ctx, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        try:
            value = await session.read_resource(params.uri)
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
            text = await session.render_prompt(params.name, params.arguments or {})
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
    server.extensions[TASKS_EXTENSION] = {}
    return server


async def serve_stdio(caller: Caller) -> None:
    """Until the host closes stdin; stdout is the wire, so nothing else may print to it."""
    server = host_server(HostSession(caller))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
