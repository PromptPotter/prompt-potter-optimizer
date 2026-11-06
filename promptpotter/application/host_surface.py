"""The surface a host assistant drives: tools, resources and prompts, one registry for every host.

The tools ARE the catalogue — a `CommandKind` row a host may press (`Press.host`) and a `ReadKind`
row that answers once — so no transport adds or drops one. A `HostSession` serves one caller: every
`HostRequest` enters at `HostSession.answer`, admitted there once; a tool call validates its
arguments as the row's own model off the event loop, and runs a command through the one dispatcher
or a read through its `ReadParams.answer`. A kind its row answers as a task
(`CommandKind.answering`) answers a `HostTask`, the MCP Tasks handle: its status is what its run
returned (`Job.ending`, worded by `STOP_REASON_INFO`) or how its job lapsed, as its release
stamped it, a reaped job's included, its result the answer its one dispatch recorded, and its cancel the one `cancel-command`. A task IS its
command: its id is the command's, and its status is read off the account's command log. Transports: in-process (call `answer`) and `presentation/mcp/`.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast, get_origin
from urllib.parse import quote, unquote

from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from promptpotter.application.bench.task_context import checkin_template
from promptpotter.application.caller import Caller
from promptpotter.application.commands.command_reading import (
    TERMINAL,
    CommandStatus,
    TaskStatus,
    command_status,
    owned_command,
)
from promptpotter.application.commands.dispatcher import (
    MAX_IDEMPOTENCY_KEY_CHARS,
    CommandCall,
    CommandDispatcher,
    CommandOutcome,
    command_answer,
)
from promptpotter.application.jobs.launcher.admission import PassHold
from promptpotter.application.jobs.launcher.head_to_head import (
    AdmittedHeadToHead,
)
from promptpotter.application.jobs.launcher.launch import Admitted
from promptpotter.application.jobs.quota import refuse_request_rate
from promptpotter.application.read_params import (
    PARAMS_FOR_READ,
    HierarchyParams,
    HierarchySubtreeParams,
    ReadParams,
)
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CancelCommandPayload,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    ReadKind,
)
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.command_log import CommandLog
from promptpotter.shared.errors import (
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.named_rows import returned

# A command tool's one argument beside its payload: what a retried call is recognised by.
IDEMPOTENCY_KEY = "idempotency_key"
_KEY_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_IDEMPOTENCY_KEY_CHARS,
    "description": "Chosen by the caller and sent again on a retry, which then answers the first "
    "call's outcome instead of acting twice.",
}


def _base64_bounded(schema: object) -> object:
    """A bytes field's bound is on its decoded bytes; its base64 argument is bounded as encoded."""
    if isinstance(schema, list):
        return [_base64_bounded(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    bounded = {k: _base64_bounded(v) for k, v in schema.items()}
    if bounded.get("format") == "base64url" and isinstance(cap := bounded.get("maxLength"), int):
        bounded["maxLength"] = 4 * -(-cap // 3)
    return bounded


@dataclass(frozen=True, slots=True)
class HostTool:
    """One catalogue row offered as a tool: named by its kind, its input its row's model, described
    by that model's own docstring."""

    kind: CommandKind | ReadKind

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def model(self) -> type[CommandPayload] | type[ReadParams]:
        if isinstance(self.kind, ReadKind):
            return PARAMS_FOR_READ[self.kind]
        return PAYLOAD_MODEL_FOR_KIND[self.kind]

    @property
    def description(self) -> str:
        doc = inspect.cleandoc(vars(self.model).get("__doc__") or "")
        if isinstance(self.kind, CommandKind):
            gate = self.kind.capability or "the account's own act"
            return f"{doc} (A recorded command; gate: {gate}.)"
        return doc

    @property
    def read_only(self) -> bool:
        return isinstance(self.kind, ReadKind)

    @property
    def destructive(self) -> bool:
        """MCP's `destructiveHint`: a read writes nothing; a command's row says."""
        return isinstance(self.kind, CommandKind) and self.kind.destructive

    @property
    def as_task(self) -> bool:
        return isinstance(self.kind, CommandKind) and self.kind.answering == "task"

    def input_schema(self) -> dict[str, Any]:
        schema = cast(dict[str, Any], _base64_bounded(self.model.model_json_schema()))
        if self.read_only:
            return schema
        return {
            **schema,
            "properties": {**schema.get("properties", {}), IDEMPOTENCY_KEY: _KEY_SCHEMA},
            "required": [*schema.get("required", []), IDEMPOTENCY_KEY],
        }

    def answers(self) -> object | None:
        """A read's answer, or a command's modelled applied answer (`command_answer`); ``None``
        where the command answers its record."""
        if isinstance(self.kind, ReadKind):
            return returned(PARAMS_FOR_READ[self.kind].answer)
        return command_answer(self.kind)

    def output_schema(self) -> dict[str, Any] | None:
        """The answer's JSON Schema, an object at its root as MCP requires: a non-object answer is
        carried under ``result`` (`structured`)."""
        return _output(self)[0]

    def structured(self, value: JsonValue) -> dict[str, JsonValue]:
        """The call's value as its `output_schema` declares it."""
        carried = _output(self)[1] or not isinstance(value, dict)
        return {"result": value} if carried else value  # type: ignore[return-value]

    def arguments(self, sent: Mapping[str, Any]) -> CommandCall[CommandPayload] | ReadParams:
        """Validated as JSON, the wire a host speaks: an upload's bytes arrive base64, decoded here,
        so a call runs this off the event loop. A command's call carries the key its caller sent
        (`CommandCall` refuses none)."""
        fields = dict(sent)
        key = "" if self.read_only else str(fields.pop(IDEMPOTENCY_KEY, "") or "")
        try:
            validated = self.model.model_validate_json(json.dumps(fields))
        except ValidationError as exc:
            raise PayloadInvalidError.refusing(exc.errors()) from exc
        if isinstance(validated, ReadParams):
            return validated
        return CommandCall(validated, key)


@functools.cache
def _output(tool: HostTool) -> tuple[dict[str, Any] | None, bool]:
    """A tool's output schema and whether its answer rides under ``result``, built once."""
    if (answers := tool.answers()) is None:
        return None, False
    schema = TypeAdapter(answers).json_schema(mode="serialization", by_alias=True)
    if schema.get("type") == "object":
        return schema, False
    defs = schema.pop("$defs", None)
    carried = {"type": "object", "properties": {"result": schema}, "required": ["result"]}
    return (carried if defs is None else {**carried, "$defs": defs}), True


def _answers_once(read: ReadKind) -> bool:
    """A streamed read has no single answer to hand a tool call; its route is its only door."""
    return get_origin(returned(PARAMS_FOR_READ[read].answer)) is not AsyncIterator


# Each described by its model's own docstring, which the model refuses to be declared without.
HOST_TOOLS: dict[str, HostTool] = {
    **{k.value: HostTool(k) for k in CommandKind if k.host},
    **{r.value: HostTool(r) for r in ReadKind if _answers_once(r)},
}


def host_tool(name: str) -> HostTool:
    if (tool := HOST_TOOLS.get(name)) is None:
        raise NotFoundError(f"no tool {name!r}")
    return tool


async def _off_loop[T](answer: Callable[[], T]) -> T:
    """A sync answer reads the disk, so it runs in a worker thread, as a sync route does."""
    return await asyncio.to_thread(answer)


def _jsonable(value: object) -> JsonValue:
    return to_jsonable_python(value, by_alias=True)  # type: ignore[no-any-return]


class HostTask(FrozenModel):
    """A task-answered call as the MCP Tasks extension tracks it: its command, read afresh."""

    task_id: str
    status: TaskStatus
    status_message: str
    created_at: str
    last_updated_at: str

    @classmethod
    def of(cls, read: CommandStatus) -> HostTask:
        return cls(
            task_id=read.command_id,
            status=read.status,
            status_message=f"{read.kind}: {read.said}",
            created_at=read.created_at,
            last_updated_at=read.last_updated_at,
        )


async def _launched(result: object) -> object:
    """An admission's launch, awaited, so the jobs its command names are launched."""
    if isinstance(result, Admitted | AdmittedHeadToHead):
        return await result.launched()
    return result


@dataclass(frozen=True, slots=True)
class HostAnswer:
    """What a tool call answers: a JSON value, or the task tracking it."""

    value: JsonValue = None
    task: HostTask | None = None


# Resources: every course and campaign by its hierarchy address, read as its subtree.
RESOURCE_PREFIX = "promptpotter://hierarchy/"
RESOURCE_TEMPLATE = RESOURCE_PREFIX + "{address}"


@dataclass(frozen=True, slots=True)
class HostResource:
    uri: str
    name: str
    description: str


def resource_uri(address: str) -> str:
    return RESOURCE_PREFIX + quote(address, safe=":~@")


class HostRequest[A](ABC):
    """One request a host sends its session, answered through `HostSession.answer` alone, which
    admits it once at the caller's request rate: a new request is a new arm here, never a door
    of its own."""

    @property
    def reads(self) -> bool:
        """A read, drawn from the read bucket; every other request from the write one."""
        return True

    @abstractmethod
    async def answered_by(self, session: HostSession) -> A: ...


@dataclass(frozen=True, slots=True)
class CallTool(HostRequest[HostAnswer]):
    """One tool call: a read answers its answer, a command its applied answer or its record, and
    a task kind the task it opened."""

    name: str
    arguments: Mapping[str, Any]

    @property
    def reads(self) -> bool:
        """An unknown name draws as a read, then is refused by `answered_by`."""
        return (tool := HOST_TOOLS.get(self.name)) is None or tool.read_only

    async def answered_by(self, session: HostSession) -> HostAnswer:
        return await session._call_tool(host_tool(self.name), self.arguments)


@dataclass(frozen=True, slots=True)
class TaskStatusOf(HostRequest[HostTask]):
    task_id: str

    async def answered_by(self, session: HostSession) -> HostTask:
        return HostTask.of(await session.command_status(self.task_id))


@dataclass(frozen=True, slots=True)
class TaskResultOf(HostRequest[tuple[str, HostAnswer]]):
    """Once the task is terminal, its tool's name and answer, read from what its dispatch
    recorded and never dispatched again: a launch answers its record, any other kind its
    handler's answer. A gate's hold is no end: the result waits past it."""

    task_id: str

    async def answered_by(self, session: HostSession) -> tuple[str, HostAnswer]:
        return await session._task_result(self.task_id)


@dataclass(frozen=True, slots=True)
class CancelTask(HostRequest[HostTask]):
    """What every other door stops it by: its command's `cancel-command`."""

    task_id: str

    @property
    def reads(self) -> bool:
        return False

    async def answered_by(self, session: HostSession) -> HostTask:
        return HostTask.of(await session.cancel_command(self.task_id))


@dataclass(frozen=True, slots=True)
class ListResources(HostRequest[list[HostResource]]):
    """The caller's campaigns and their courses, as the hierarchy listing serves them."""

    async def answered_by(self, session: HostSession) -> list[HostResource]:
        return await session._host_resources()


@dataclass(frozen=True, slots=True)
class ReadResource(HostRequest[JsonValue]):
    uri: str

    async def answered_by(self, session: HostSession) -> JsonValue:
        return await session._read_resource(self.uri)


@dataclass(frozen=True, slots=True)
class RenderPrompt(HostRequest[str]):
    name: str
    arguments: Mapping[str, str]

    async def answered_by(self, session: HostSession) -> str:
        return await session._render_prompt(self.name, self.arguments)


class HostSession:
    """One caller's host requests. Every request enters at `answer`, admitted there once at the
    caller's request rate; a task it opened is its command, read off the account's log."""

    def __init__(self, caller: Caller) -> None:
        self.caller = caller

    async def answer[A](self, request: HostRequest[A]) -> A:
        refuse_request_rate(self.caller.stores.identity, read=request.reads)
        return await request.answered_by(self)

    def _dispatcher(self) -> CommandDispatcher:
        return CommandDispatcher(self.caller.stores, job_registry=self.caller.jobs)

    async def _call_tool(self, tool: HostTool, arguments: Mapping[str, Any]) -> HostAnswer:
        sent = await _off_loop(lambda: tool.arguments(arguments))
        if isinstance(sent, ReadParams):
            return HostAnswer(_jsonable(await sent.answered(self.caller)))
        if tool.as_task:
            return HostAnswer(task=await self._open(sent))
        outcome = await self._dispatcher().dispatch(sent)
        return HostAnswer(_jsonable(outcome.accepted if tool.answers() is None else outcome.result))

    async def _open(self, sent: CommandCall[CommandPayload]) -> HostTask:
        """Its act runs on past the answer, under the hold any stop of its pass takes; the task
        answered is the command it filed, or the first act's where the call was a retry."""
        hold = PassHold()

        async def _dispatch() -> CommandOutcome[Any, Any]:
            with hold.bound(sent.command_id):
                outcome = await self._dispatcher().dispatch(sent)
            await _launched(outcome.result)
            return outcome

        running = asyncio.create_task(_dispatch())
        _ACTING.add(running)
        running.add_done_callback(_settled)
        log = CommandLog(self.caller.stores.base_dir)
        while await _off_loop(lambda: log.state(sent.command_id)) is None and not running.done():  # noqa: ASYNC110 — its filing lands off the loop
            await asyncio.sleep(_FILED_POLL_S)
        command_id = sent.command_id
        if await _off_loop(lambda: log.state(sent.command_id)) is None:
            # Ended before it was filed: refused, or answered as a retry of the first act.
            command_id = (await running).accepted.command_id
        return HostTask.of(await self.command_status(command_id))

    async def command_status(self, command_id: str) -> CommandStatus:
        stores, jobs = self.caller.stores, self.caller.jobs
        return await _off_loop(lambda: command_status(stores, jobs, command_id))

    async def _task_result(self, task_id: str) -> tuple[str, HostAnswer]:
        """Polled to its end: its act runs in whichever process filed it, and its jobs in their own."""
        while (read := await self.command_status(task_id)).status not in TERMINAL:  # noqa: ASYNC110 — a cross-process wait
            await asyncio.sleep(_SETTLE_POLL_S if read.status != "working" else _FILED_POLL_S)
        tool = host_tool(read.kind)
        state = await _off_loop(lambda: owned_command(self.caller.stores, task_id))
        if tool.answers() is None:
            accepted = CommandAcceptedBody(command_id=task_id, subject=read.subject)
            return tool.name, HostAnswer(_jsonable(accepted))
        if read.status != "completed":
            raise ConflictError(
                f"{read.kind}: {read.said}",
                code=f"command_{read.status}",
                details={"command_id": task_id},
            )
        payload = PAYLOAD_MODEL_FOR_KIND[read.kind].model_validate(state.filed.payload)
        replayed = await self._dispatcher().dispatch(
            CommandCall(payload, state.filed.idempotency_key)
        )
        return tool.name, HostAnswer(_jsonable(replayed.result))

    async def cancel_command(self, command_id: str) -> CommandStatus:
        """Its one `cancel-command`, keyed by it so a retried cancel acts once."""
        await self.command_status(command_id)
        cancel = CommandCall(CancelCommandPayload(command_id=command_id), f"cancel:{command_id}")
        await self._dispatcher().dispatch(cancel)
        return await self.command_status(command_id)

    async def _host_resources(self) -> list[HostResource]:
        listing = await HierarchyParams().answered(self.caller)
        listed: list[HostResource] = []
        for campaign in listing.entries:  # type: ignore[attr-defined]
            if not isinstance(campaign, HierarchyCampaign):
                continue
            entries: list[HierarchyCampaign | HierarchyCourse] = [campaign, *campaign.children]
            listed += [
                HostResource(resource_uri(e.address), e.label, f"The {e.level}'s subtree")
                for e in entries
            ]
        return listed

    async def _read_resource(self, uri: str) -> JsonValue:
        if not uri.startswith(RESOURCE_PREFIX):
            raise NotFoundError(f"no resource {uri!r}")
        address = unquote(uri.removeprefix(RESOURCE_PREFIX))
        read = PayloadInvalidError.validating(HierarchySubtreeParams, {"address": address})
        return _jsonable(await read.answered(self.caller))

    async def _render_prompt(self, name: str, arguments: Mapping[str, str]) -> str:
        prompt = HOST_PROMPTS.get(name)
        if prompt is None:
            raise NotFoundError(f"no prompt {name!r}")
        if missing := [a for a in prompt.arguments if not arguments.get(a)]:
            raise PayloadInvalidError(f"prompt {name!r} needs {', '.join(missing)}")
        instruction = _TO_THE_GATE.format(draft_id=arguments["draft_id"])
        template = await _off_loop(checkin_template)
        return template.compile_prompt(consultation_instruction=instruction)


# How often a task's result re-reads its command while its jobs run, and while its act does.
_SETTLE_POLL_S = 2.0
_FILED_POLL_S = 0.05

# A task's act in flight in this process, kept from collection until it ends.
_ACTING: set[asyncio.Task[Any]] = set()


def _settled(running: asyncio.Task[Any]) -> None:
    """Its act ended: what it raised is on its command's log, taken here."""
    _ACTING.discard(running)
    if not running.cancelled():
        running.exception()


# Prompts: what a host is told to do, seeded from the bench's own check-in text.
@dataclass(frozen=True, slots=True)
class HostPrompt:
    name: str
    description: str
    arguments: tuple[str, ...]


REACH_THE_ORIGIN_GATE = HostPrompt(
    "reach-the-origin-gate",
    "Carry an open check-in to its run's round-0 origin gate.",
    ("draft_id",),
)
HOST_PROMPTS = {REACH_THE_ORIGIN_GATE.name: REACH_THE_ORIGIN_GATE}

# Filled into the check-in's own `{{consultation_instruction}}`: the resolver turn's text, driven by tools.
_TO_THE_GATE = (
    f"Here you return no JSON object: you work draft {{draft_id}} through PromptPotter's tools, "
    f"under the invariants above. Read it with `{ReadKind.DRAFT}`; close each open gap with "
    f"`{CommandKind.EDIT_DRAFT_CAMPAIGN}` where the data decides it, or run "
    f"`{CommandKind.RESOLVE_ORIGIN}` for a resolver turn, citing your evidence as above; ask the "
    f"operator where it does not. When no gap is open, `{CommandKind.START_CHECKIN}` launches the "
    "run. It holds at the round-0 origin gate only where the origin's health trips the gate, and "
    "its task then reads `input_required`; a healthy origin goes straight on into the search. If "
    f"it holds, read its `{ReadKind.CYCLE_DASHBOARD}` and answer with "
    f"`{CommandKind.ORIGIN_GATE_DECISION}` only once the operator says."
)
