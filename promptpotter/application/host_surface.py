"""The surface a host assistant drives: tools, resources and prompts, one registry for every host.

The tools ARE the catalogue — a `CommandKind` row a host may press (`Press.host`) and a `ReadKind`
row that answers once — so no transport adds or drops one. A `HostSession` serves one caller: every
`HostRequest` enters at `HostSession.answer`, admitted there once; a tool call validates its
arguments as the row's own model off the event loop, and runs a command through the one dispatcher
or a read through its `ReadParams.answer`. A kind its row answers as a task
(`CommandKind.answering`) answers a `HostTask`, the MCP Tasks handle: its status is what its run
returned (`Job.ending`, worded by `STOP_REASON_INFO`) or how its job lapsed, as its release
stamped it, a reaped job's included, its result the answer its one dispatch recorded, and its cancel each job's own
command. Transports: in-process (call `answer`) and `presentation/mcp/`.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import secrets
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast, get_origin
from urllib.parse import quote, unquote

from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from promptpotter.application.bench.task_context import checkin_template
from promptpotter.application.caller import Caller
from promptpotter.application.commands.dispatcher import (
    MAX_IDEMPOTENCY_KEY_CHARS,
    CommandCall,
    CommandDispatcher,
    CommandOutcome,
    addressed,
    command_answer,
)
from promptpotter.application.commands.measuring import pass_pause, stop_pass
from promptpotter.application.jobs.launcher.admission import PassHold, PassStoppedError
from promptpotter.application.jobs.launcher.head_to_head import (
    AdmittedHeadToHead,
    HeadToHeadLaunched,
)
from promptpotter.application.jobs.launcher.launch import Admitted, Launched
from promptpotter.application.jobs.quota import refuse_request_rate
from promptpotter.application.read_params import (
    PARAMS_FOR_READ,
    HierarchyParams,
    HierarchySubtreeParams,
    ReadParams,
)
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CancelQueuedRunPayload,
    CommandKind,
    CommandPayload,
    PauseCyclePayload,
    ReadKind,
)
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.hierarchy import HierarchyCampaign, HierarchyCourse
from promptpotter.domain.job import UNRESOLVED_HOP, InnerRun, Job
from promptpotter.domain.phases import (
    INNER_PAUSE_REFUSAL,
    STOP_REASON_INFO,
    StopCategory,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.run_records import LaunchedJob
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    NotFoundError,
    PayloadInvalidError,
    PotterError,
    TaskUncancellableError,
)
from promptpotter.shared.identity import acting_principal
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


type TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]

# Past its run's end a task's status is how the run ended, and a pause the operator asked for is a
# cancel; a run held at a gate waits on its requester.
_ENDED: dict[StopOutcome, TaskStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "failed",
    StopOutcome.FAILED: "failed",
    StopOutcome.PAUSED: "failed",
}
_LAPSED: dict[str, TaskStatus] = {
    "refused": "failed",
    "withdrawn": "cancelled",
    "orphaned": "failed",
}
# One job held or working holds the task open; past that, the worst ending speaks for it.
_PRECEDENCE: tuple[TaskStatus, ...] = (
    "input_required",
    "working",
    "failed",
    "cancelled",
    "completed",
)
_TERMINAL = frozenset[TaskStatus]({"completed", "failed", "cancelled"})


def _ended(what: str, ending: StopReason, *, stepped: bool) -> tuple[TaskStatus, str]:
    info = STOP_REASON_INFO[ending]
    if stepped:
        return "completed", f"{what}: stepped its rounds. {info.next_step}"
    status = _ENDED[info.outcome]
    if info.outcome is StopOutcome.PAUSED and info.category is StopCategory.OPERATOR:
        status = "cancelled"
    return status, f"{what}: {info.label}. {info.next_step}"


def job_status(job: Job, *, held: bool) -> tuple[TaskStatus, str]:
    """One job's task status and its words; *held* is its cycle holding at a gate for an answer."""
    if job.ending is not None:
        return _ended(job.what, job.ending, stepped=job.stepped)
    if job.lapse is not None:
        return _LAPSED[job.lapse], f"{job.what}: {job.refusal or job.lapse}"
    if job.released:
        return "completed", f"{job.what}: returned"
    if held:
        return "input_required", f"{job.what}: holding at a gate for its answer"
    return "working", f"{job.what}: {job.stage}"


def _held(job: Job) -> bool:
    """Its cycle holds at a gate (`RunState.gate`) while the run waits on an answer."""
    if job.released or job.hop == UNRESOLVED_HOP:
        return False
    cycle_dir = cycle_dir_for(WorkspaceDir(Path(job.workspace)), job.hop)
    return derive_run_state(cycle_dir).gate is not None


class HostTask(FrozenModel):
    """A task-answered call as the MCP Tasks extension tracks it, read afresh each time it is asked."""

    task_id: str
    status: TaskStatus
    status_message: str
    created_at: str
    last_updated_at: str


def _launches(result: object) -> list[LaunchedJob] | None:
    """What a launch's answer records of each job it launched; ``None`` for any other answer."""
    match result:
        case Launched(launch=launch):
            return [launch]
        case HeadToHeadLaunched(launches=launches):
            return list(launches)
        case _:
            return None


async def _launched(result: object) -> object:
    """An admission's launch, awaited, so a task names the jobs it launched."""
    if isinstance(result, Admitted | AdmittedHeadToHead):
        return await result.launched()
    return result


@dataclass(slots=True)
class _Tracked:
    """A task-answered call: the call, the hold of any paid pass it runs, its dispatch in flight,
    and what that dispatch recorded — its outcome and each job it launched."""

    tool: HostTool
    call: CommandCall[CommandPayload]
    hold: PassHold
    dispatching: asyncio.Task[tuple[CommandOutcome[Any, Any], list[LaunchedJob] | None]]
    created_at: str = field(default_factory=utcnow_iso)
    dispatched_at: str | None = None


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
        return await session._status(self.task_id)


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
    """What every other door stops it by: a paid pass in flight paused under its own hold
    (`stop_pass`), and each job a launch launched by its own act (`cancel_acts`)."""

    task_id: str

    @property
    def reads(self) -> bool:
        return False

    async def answered_by(self, session: HostSession) -> HostTask:
        return await session._cancel_task(self.task_id)


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
    """One caller's host requests, and the tasks its task-answered calls opened. Every request
    enters at `answer`, admitted there once at the caller's request rate."""

    def __init__(self, caller: Caller) -> None:
        self.caller = caller
        self._tasks: dict[str, _Tracked] = {}

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
            return HostAnswer(task=await self._open(tool, sent))
        outcome = await self._dispatcher().dispatch(sent)
        return HostAnswer(_jsonable(outcome.accepted if tool.answers() is None else outcome.result))

    async def _open(self, tool: HostTool, sent: CommandCall[CommandPayload]) -> HostTask:
        """Tracked as the dispatcher acts on it (`addressed`), so its stop names the cycle the pass
        holds even once another campaign shares the prefix it was sent by."""
        task_id, hold = secrets.token_urlsafe(12), PassHold()
        acted = await _off_loop(lambda: addressed(self.caller.stores, sent.payload))
        call = replace(sent, payload=acted)

        async def _dispatch() -> tuple[CommandOutcome[Any, Any], list[LaunchedJob] | None]:
            with hold.bound():
                outcome = await self._dispatcher().dispatch(call)
            return outcome, _launches(await _launched(outcome.result))

        tracked = _Tracked(tool, call, hold, asyncio.create_task(_dispatch()))
        tracked.dispatching.add_done_callback(lambda _: _stamp(tracked))
        self._tasks[task_id] = tracked
        return await self._status(task_id)

    def _tracked(self, task_id: str) -> _Tracked:
        if (tracked := self._tasks.get(task_id)) is None:
            raise NotFoundError(f"no task {task_id!r}")
        return tracked

    def _jobs(self, launches: list[LaunchedJob]) -> list[Job]:
        """The caller's own launches, by the principal that launched them; another's is absent."""
        principal = acting_principal(self.caller.stores.identity).id
        job_ids = [launch.job_id for launch in launches]
        jobs = self.caller.jobs.launched_by(job_ids, principal_id=principal)
        if jobs is None:
            raise NotFoundError(f"no jobs {','.join(job_ids)!r}")
        return jobs

    def _launch_reads(self, launches: list[LaunchedJob]) -> list[tuple[TaskStatus, str, str]]:
        """Each launched job's status, words and last update, as its registry reads it."""
        return [
            (*job_status(job, held=_held(job)), job.released_at or job.started_at or job.created_at)
            for job in self._jobs(launches)
        ]

    async def _status(self, task_id: str) -> HostTask:
        tracked = self._tracked(task_id)
        running = tracked.dispatching
        updated = tracked.dispatched_at or tracked.created_at
        status: TaskStatus
        if not running.done():
            status, said = "working", f"{tracked.tool.name}: running"
        elif running.cancelled():
            status, said = "cancelled", f"{tracked.tool.name}: cancelled"
        elif isinstance(exc := running.exception(), PassStoppedError):
            status, said = "cancelled", f"{tracked.tool.name}: {exc}"
        elif exc is not None:
            code = exc.code if isinstance(exc, PotterError) else type(exc).__name__
            status, said = "failed", f"{tracked.tool.name}: {code}: {exc}"
        elif (launches := running.result()[1]) is None:
            status, said = "completed", f"{tracked.tool.name}: answered"
        else:
            read = await _off_loop(lambda: self._launch_reads(launches))
            status = next(s for s in _PRECEDENCE if s in {r[0] for r in read})
            said = "; ".join(r[1] for r in read)
            updated = max([updated, *(r[2] for r in read)])
        return HostTask(
            task_id=task_id,
            status=status,
            status_message=said,
            created_at=tracked.created_at,
            last_updated_at=updated,
        )

    async def _task_result(self, task_id: str) -> tuple[str, HostAnswer]:
        tracked = self._tracked(task_id)
        outcome, launches = await asyncio.shield(tracked.dispatching)
        # Polled: its jobs end in their own processes, which no signal here carries.
        while launches is not None and (await self._status(task_id)).status not in _TERMINAL:  # noqa: ASYNC110 — a cross-process job wait
            await asyncio.sleep(_SETTLE_POLL_S)
        answer = outcome.accepted if tracked.tool.answers() is None else outcome.result
        return tracked.tool.name, HostAnswer(_jsonable(answer))

    async def _cancel_task(self, task_id: str) -> HostTask:
        """A pass's stop is sent under its hold, in one step; a launch's acts are each resolved
        before any is dispatched, each keyed so a retried cancel acts once."""
        tracked = self._tracked(task_id)
        stores = self.caller.stores
        if tracked.tool.answers() is not None:
            if not tracked.dispatching.done():
                pause = await _off_loop(
                    lambda: pass_pause(stores, tracked.call.payload, reason=_CANCELLED)
                )
                await stop_pass(
                    stores, tracked.hold, CommandCall(pause, f"host-task-cancel:{task_id}")
                )
            return await self._status(task_id)
        _, launches = await asyncio.shield(tracked.dispatching)
        jobs = [] if launches is None else await _off_loop(lambda: self._jobs(launches))
        acts = await _off_loop(lambda: cancel_acts(jobs, stores.campaigns.workspace))
        dispatcher = self._dispatcher()
        for act in acts:
            await dispatcher.dispatch(act)
        return await self._status(task_id)

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


_CANCELLED = "host task cancelled"


def cancel_acts(jobs: list[Job], workspace: WorkspaceDir) -> list[CommandCall[CommandPayload]]:
    """Each live job's own cancel, resolved for every job before any is dispatched: a queued one
    withdrawn, a running run or paid pass paused on its own cycle in *workspace*, each keyed by
    the job so a retried cancel acts once. A job no command of *workspace* cancels now — its cycle
    refusing the pause (`RunState.pause_refusal`) among them — refuses them all."""
    acts: list[CommandCall[CommandPayload]] = []
    for job in jobs:
        key = f"host-task-cancel:{job.job_id}"
        if job.released:
            continue
        if job.queued:
            acts.append(CommandCall(CancelQueuedRunPayload(job_id=job.job_id), key))
            continue
        if isinstance(job.holder, InnerRun):
            raise TaskUncancellableError(f"{job.what}: {INNER_PAUSE_REFUSAL}")
        if job.hop == UNRESOLVED_HOP:
            raise TaskUncancellableError(f"{job.what} names no cycle to pause yet; cancel again")
        if job.workspace != str(workspace):
            raise TaskUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := derive_run_state(cycle_dir_for(workspace, job.hop)).pause_refusal:
            raise TaskUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.campaign_id, cycle_id=job.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key))
    return acts


# How often a launch's task result re-reads its jobs while they run.
_SETTLE_POLL_S = 2.0


def _stamp(tracked: _Tracked) -> None:
    """Its dispatch ended: stamped, and its exception taken here, as the task's status reads it."""
    tracked.dispatched_at = utcnow_iso()
    if not tracked.dispatching.cancelled():
        tracked.dispatching.exception()


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
