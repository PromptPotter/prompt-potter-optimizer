"""The surface a host assistant drives: tools, resources and prompts, one registry for every host.

The tools ARE the catalogue — a `CommandKind` row a host may press (`Press.host`) and a `ReadKind`
row that answers once — so no transport adds or drops one. A `HostSession` serves one caller and
holds nothing past a request: each enters at one of its methods, admitted there once (`_admit`);
a tool call validates its arguments as the row's own model off the event loop, and runs a command
through the one dispatcher or a read through its `ReadParams.answered`. A kind its row lets run on
(`CommandKind.answering`) may be started instead of answered: its command id is then the handle
(what MCP calls a task), read by the `command-status` row, answered off its ledger record and
stopped by the `cancel-command` command, the same at every door. Transports: in-process (call the methods)
and `presentation/mcp/`.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, cast, get_origin
from urllib.parse import quote, unquote

from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from promptpotter.application.bench.task_context import checkin_template
from promptpotter.application.caller import Caller
from promptpotter.application.command_status import TERMINAL, CommandStatus, asked, settled
from promptpotter.application.commands.cancelling import cancel_command
from promptpotter.application.commands.dispatcher import (
    MAX_IDEMPOTENCY_KEY_CHARS,
    CommandCall,
    CommandDispatcher,
    CommandOutcome,
    command_answer,
)
from promptpotter.application.jobs.launcher.head_to_head import AdmittedHeadToHead
from promptpotter.application.jobs.launcher.launch import Admitted
from promptpotter.application.jobs.quota import refuse_request_rate
from promptpotter.application.read_params import (
    PARAMS_FOR_READ,
    CommandStatusParams,
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
from promptpotter.infrastructure.store.command_index import Unrecorded
from promptpotter.shared.errors import ConflictError, NotFoundError, PayloadInvalidError
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
    def startable(self) -> bool:
        return isinstance(self.kind, CommandKind) and self.kind.answering == "started"

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

    def answered(self, outcome: CommandOutcome[Any]) -> JsonValue:
        """A command's applied answer, else its record."""
        return _jsonable(outcome.accepted if self.answers() is None else outcome.result)

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


def _jsonable(value: object) -> JsonValue:
    return to_jsonable_python(value, by_alias=True)  # type: ignore[no-any-return]


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


# A started command's dispatch runs on past the call that started it, held here so it is not
# collected; nothing is kept of it, as every read of it is off its ledger record.
_RUNNING: set[asyncio.Task[Any]] = set()
# How often a result re-reads whether its command settled: a command's jobs end in their own
# processes, which no signal here carries.
_SETTLE_POLL_S = 2.0


def _ran(running: asyncio.Task[Any]) -> None:
    """Its dispatch ended: its exception taken here, as its ack already recorded it."""
    _RUNNING.discard(running)
    if not running.cancelled():
        running.exception()


async def _dispatched(dispatcher: CommandDispatcher, call: CommandCall[Any]) -> CommandOutcome[Any]:
    """An admission's launch awaited too, so the jobs it asked for start."""
    outcome = await dispatcher.dispatch(call)
    if isinstance(outcome.result, Admitted | AdmittedHeadToHead):
        await outcome.result.launched()
    return outcome


class HostSession:
    """One caller's host requests, each admitted once at the caller's request rate (`_admit`).
    It holds nothing past a request: a started command is read, answered and stopped by its id."""

    def __init__(self, caller: Caller) -> None:
        self.caller = caller

    def _admit(self, kind: CommandKind | ReadKind | None) -> None:
        """A command draws from the write bucket; a read, a prompt and an unknown tool from the
        read one."""
        refuse_request_rate(self.caller.stores.identity, read=not isinstance(kind, CommandKind))

    def _dispatcher(self) -> CommandDispatcher:
        return CommandDispatcher(self.caller.stores, job_registry=self.caller.jobs)

    async def _sent(
        self, name: str, arguments: Mapping[str, Any]
    ) -> tuple[HostTool, CommandCall[CommandPayload] | ReadParams]:
        self._admit(None if (known := HOST_TOOLS.get(name)) is None else known.kind)
        tool = host_tool(name)
        return tool, await asyncio.to_thread(tool.arguments, arguments)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> JsonValue:
        """A read answers its answer, a command its applied answer or its record."""
        tool, sent = await self._sent(name, arguments)
        if isinstance(sent, ReadParams):
            return _jsonable(await sent.answered(self.caller))
        return tool.answered(await _dispatched(self._dispatcher(), sent))

    async def start_command(self, name: str, arguments: Mapping[str, Any]) -> CommandStatus:
        """A startable kind's call left to run on: its status once its record lands. A refusal before
        the record answers here; one after it is the command's failed status."""
        tool, sent = await self._sent(name, arguments)
        if not isinstance(sent, CommandCall) or not tool.startable:
            raise PayloadInvalidError(f"{name} is answered inline, never left running")
        running = asyncio.create_task(_dispatched(self._dispatcher(), sent))
        _RUNNING.add(running)
        running.add_done_callback(_ran)
        landed = asyncio.create_task(sent.recorded.wait())
        await asyncio.wait({running, landed}, return_when=asyncio.FIRST_COMPLETED)
        landed.cancel()
        if sent.recorded.is_set():
            return await self._command_status(sent.command_id)
        # A refusal before the record, or a retry its key answered from the first act.
        return await self._command_status(running.result().accepted.command_id)

    async def _command_status(self, command_id: str) -> CommandStatus:
        read = PayloadInvalidError.validating(CommandStatusParams, {"command_id": command_id})
        return cast(CommandStatus, await read.answered(self.caller))

    async def command_status(self, command_id: str) -> CommandStatus:
        """The `command-status` read."""
        self._admit(ReadKind.COMMAND_STATUS)
        return await self._command_status(command_id)

    async def command_result(self, command_id: str) -> tuple[CommandStatus, JsonValue]:
        """Its settled status and answer, in one admission, a gate's hold being no end: a refusal
        or a stop raised as the act's own; else what its tool answers, the record or the act's
        replay off its ledger record (`Applier.replay`), never a second act."""
        self._admit(ReadKind.COMMAND_STATUS)
        while (status := await self._command_status(command_id)).status not in TERMINAL:  # noqa: ASYNC110 — a cross-process job wait
            await asyncio.sleep(_SETTLE_POLL_S)
        found = await asyncio.to_thread(asked, self.caller.stores, command_id)
        # Settled without an ack: its record never landed, or its filer died mid-act.
        if isinstance(found, Unrecorded) or (ack := settled(found.asked)) is None:
            raise ConflictError(status.said, code="command_unsettled")
        record = found.asked.record
        if ack.status in ("rejected", "stopped"):
            raise ConflictError(
                f"command {record.kind} {ack.status}: {ack.detail}", code=f"command_{ack.status}"
            )
        tool = host_tool(status.kind)
        if tool.answers() is None:
            return status, _jsonable(CommandAcceptedBody(command_id=command_id))
        payload = PAYLOAD_MODEL_FOR_KIND[record.kind].model_validate(record.payload)
        replayed = CommandCall(payload, record.idempotency_key, record.command_id)
        return status, tool.answered(await self._dispatcher().dispatch(replayed))

    async def cancel_command(self, command_id: str) -> CommandStatus:
        """The `cancel-command` command, as every other door sends it."""
        self._admit(CommandKind.CANCEL_COMMAND)
        call = CommandCall(CancelCommandPayload(command_id=command_id))
        return (await self._dispatcher().answer(cancel_command, call)).result

    async def resources(self) -> list[HostResource]:
        """The caller's campaigns and their courses, as the hierarchy listing serves them."""
        self._admit(ReadKind.HIERARCHY)
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

    async def read_resource(self, uri: str) -> JsonValue:
        self._admit(ReadKind.HIERARCHY_SUBTREE)
        if not uri.startswith(RESOURCE_PREFIX):
            raise NotFoundError(f"no resource {uri!r}")
        address = unquote(uri.removeprefix(RESOURCE_PREFIX))
        read = PayloadInvalidError.validating(HierarchySubtreeParams, {"address": address})
        return _jsonable(await read.answered(self.caller))

    async def render_prompt(self, name: str, arguments: Mapping[str, str]) -> str:
        self._admit(None)
        prompt = HOST_PROMPTS.get(name)
        if prompt is None:
            raise NotFoundError(f"no prompt {name!r}")
        if missing := [a for a in prompt.arguments if not arguments.get(a)]:
            raise PayloadInvalidError(f"prompt {name!r} needs {', '.join(missing)}")
        instruction = _TO_THE_GATE.format(draft_id=arguments["draft_id"])
        template = await asyncio.to_thread(checkin_template)
        return template.compile_prompt(consultation_instruction=instruction)


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
    "run, which holds at the round-0 origin gate: its command status reads `input_required` there; read its "
    f"`{ReadKind.CYCLE_DASHBOARD}` and answer with `{CommandKind.ORIGIN_GATE_DECISION}` only once "
    "the operator says."
)
