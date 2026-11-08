"""The host registry served over MCP: list and call it, read its resources, get its prompts.

Every answer is `application/host_surface.py`'s `HostSession`; this module only words it in MCP's
types. A task kind's call answers the Tasks extension's handle: claimed (`resultType: "task"`) where
the request itself declares the extension on a 2026-era wire, and as the call's structured content
everywhere else, since a client MUST reject a claimed result its request did not declare and a
handshake-era wire carries none. `tasks/get`, `tasks/result` and `tasks/cancel` serve both. A
refusal is its `domain/refusal.py::Refusal` on every answer: a call's error result, every other
request's JSON-RPC error data.
"""

from __future__ import annotations

import json
from typing import Any

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
from promptpotter.application.host_surface import HostAnswer, HostSession, HostTask
from promptpotter.domain.refusal import Refusal
from promptpotter.shared.errors import PotterError

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
_ACCEPTS_TASKS = types.ClientCapabilities(extensions={TASKS_EXTENSION: {}})

type Ctx = ServerRequestContext[Any, Any]


def _claims_task(ctx: Ctx) -> bool:
    """Per request: a 2026-era envelope declares its own capabilities, so a client advertising the
    extension at discovery may still send a call that accepts no task."""
    return ctx.protocol_version in types.version.MODERN_PROTOCOL_VERSIONS and (
        ctx.session.check_client_capability(_ACCEPTS_TASKS)
    )


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


def _rpc_code(exc: PotterError) -> int:
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
                for tool in host_surface.HOST_TOOLS.values()
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

    async def get_task(_ctx: Ctx, params: types.GetTaskRequestParams) -> types.GetTaskResult:
        try:
            task = await session.answer(host_surface.TaskStatusOf(params.task_id))
        except PotterError as exc:
            raise _refused(exc) from exc
        return types.GetTaskResult(**_task(task).model_dump())

    async def task_result(
        _ctx: Ctx, params: types.GetTaskPayloadRequestParams
    ) -> types.CallToolResult:
        try:
            name, answer = await session.answer(host_surface.TaskResultOf(params.task_id))
        except PotterError as exc:
            raise _refused(exc) from exc
        return _answered(name, answer)

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
            description="Any entry by its hierarchy address: a campaign, course, round or candidate.",
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
    server.extensions[TASKS_EXTENSION] = {}
    return server


async def serve_stdio(caller: Caller) -> None:
    """Until the host closes stdin; stdout is the wire, so nothing else may print to it."""
    server = host_server(HostSession(caller))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
