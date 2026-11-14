from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, TypeAliasType, Union, cast, get_args, get_origin

from pydantic import BaseModel, JsonValue, TypeAdapter

from promptpotter.application.cycle_addressing import resolve_course, resolve_subject
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.jobs.launcher.launch import Inline, LaunchOutcome
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.views.render.phase import stop_next_lines
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.command_events import (
    WORKSPACE_SUBJECT,
    CommandAckRecord,
    CommandAckStatus,
    CommandEffect,
    CommandRecord,
    KeptRefusal,
    draft_subject,
    subject_key,
)
from promptpotter.domain.command_kinds import (
    COMMAND_ID_CHARS,
    CampaignPayload,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    CyclePayload,
    Door,
    DraftPayload,
    LinePayload,
)
from promptpotter.domain.cycle_paths import CycleHop, Subject, decode_cycle_path
from promptpotter.domain.phases import StopReason
from promptpotter.domain.records.run_records import LaunchAnswer, NoEffect, StandingEffect
from promptpotter.infrastructure.producer_lock import this_producer
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import (
    Stores,
    owned_campaign,
    resolve_cycle_path,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    FileKind,
    FileUnreadableError,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
)
from promptpotter.shared.identity import acting_principal, require_capability
from promptpotter.shared.named_rows import imported, returned


class RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_HANDLER_PACKAGE = "promptpotter.application.commands"
# A retry polls: the first call's end may land from another process, which no signal carries.
_ACTING_POLL_S = 0.25
# The longest key a door accepts; each declares it on its own wire (a header, a tool's schema).
MAX_IDEMPOTENCY_KEY_CHARS = 256


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote door passes the key its caller retries under, and a retry under it answers the
    first act; an in-process sender sends once, under a fresh one. Its command id is minted here,
    so a door names what it filed (its `cancel`) before the act answers. *door* is where it was
    pressed, named by every caller."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    door: Door = field(kw_only=True)

    def __post_init__(self) -> None:
        if not 0 < len(self.command_id) <= COMMAND_ID_CHARS:
            raise BadRequestError(
                f"A command id is 1 to {COMMAND_ID_CHARS} characters.", code="command_id_invalid"
            )
        key = self.idempotency_key.strip()
        if not key:
            raise BadRequestError(
                "An idempotency key is required on every command a door sends.",
                code="idempotency_key_missing",
            )
        if len(key) > MAX_IDEMPOTENCY_KEY_CHARS:
            raise BadRequestError(
                f"An idempotency key is at most {MAX_IDEMPOTENCY_KEY_CHARS} characters.",
                code="idempotency_key_too_long",
            )
        object.__setattr__(self, "idempotency_key", key)

    @property
    def kind(self) -> CommandKind:
        return self.payload.kind


@dataclass(frozen=True, slots=True)
class Replaying[P: CommandPayload]:
    """A retry's first act, answered off its end: never off the target it named, which that act
    may have moved, minted or deleted since. A launch's jobs read live, off *jobs*."""

    stores: Stores
    jobs: JobRegistry
    payload: P
    end: CommandAckRecord

    def misread(self, said: str) -> FileUnreadableError:
        """Its end acks another kind's effect: the account's command log that holds it."""
        return FileUnreadableError(
            FileKind.LEDGER, path=str(self.stores.commands.path), reason=said
        )


type Replay[R] = Callable[[Replaying[Any]], Awaitable[R]]

_REPLAYS: dict[CommandKind, Replay[Any]] = {}


def replays[R](*kinds: CommandKind) -> Callable[[Replay[R]], Replay[R]]:
    """Registers how a retry of *kinds* answers its first act. A kind answering nothing, or one
    whose end keeps its answer (`keeps_answer`), registers none."""

    def _registered(replay: Replay[R]) -> Replay[R]:
        for kind in kinds:
            if kind in _REPLAYS:
                raise TypeError(f"{kind} already replays by {_REPLAYS[kind]!r}")
            _REPLAYS[kind] = replay
        return replay

    return _registered


async def _no_body(_: Replaying[Any]) -> None:
    """What a deduped retry answers for a command whose applied form answers nothing either."""


@dataclass(frozen=True, slots=True)
class Applier[R]:
    """`effect_fn` reads the ack's effect off what `run` applied; `ack="accepted"` leaves `run`
    the guard alone, the loop acking `applied`. A retry never reaches it (`replay_of`)."""

    run: Callable[[], Awaitable[R]]
    effect_fn: Callable[[R], CommandEffect] | None = None
    ack: CommandAckStatus = "applied"
    # Set where what `run` answers carries the act on past its ack (a Start's admitted launch holds
    # its slot, and the draft's claim, to its mint): how that answer is given back where the act
    # raises after `run` answered it. A draft command's claim is otherwise the dispatcher's until
    # the act ends, and released on every raise.
    rides: Callable[[R, BaseException], Awaitable[None]] | None = None

    @staticmethod
    def silent(run: Callable[[], Awaitable[object]]) -> Applier[object]:
        return Applier(run)

    @staticmethod
    def blocking[T](act: Callable[[], T]) -> Applier[T]:
        """A sync act, which may wait on a gate or a store's lock: off the loop, its one offload."""

        async def _off_loop() -> T:
            return await asyncio.to_thread(act)

        return Applier(_off_loop)

    @staticmethod
    def for_loop(dispatched: DispatchedToCycle[Any], refusal: str) -> Applier[object]:
        """Stamped with the hold read beside *refusal*: a run takes only a pause of its own."""
        held = dispatched.dispatcher.stores.campaigns.holder(dispatched.hop)
        job = None if held is None else held.job
        standing = StandingEffect(hold=None if job is None else job.job_id)

        async def _guard() -> None:
            if refusal:
                raise RejectedError(refusal)

        return Applier(_guard, effect_fn=lambda _: standing, ack="accepted")

    @staticmethod
    def refusing(reason: str) -> Applier[object]:
        async def _refuse() -> None:
            raise RejectedError(reason)

        return Applier(_refuse)


@dataclass(frozen=True, slots=True)
class Dispatched[P: CommandPayload]:
    dispatcher: CommandDispatcher
    payload: P
    # What it is filed as: a pass reads its `cancel` by it (`RunControl.acting`).
    command_id: str
    # Where it was pressed, which an act it files in turn names (a cancel's pause, a fork's parent's).
    door: Door = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class DispatchedToCycle[P: CommandPayload](Dispatched[P]):
    campaign: Campaign
    hop: CycleHop


@dataclass(frozen=True, slots=True)
class DispatchedToDraft[P: CommandPayload](Dispatched[P]):
    claim: DraftClaim


@dataclass(frozen=True, slots=True)
class CommandOutcome[R, P: CommandPayload]:
    # What the dispatcher acted on: its address resolved from the prefix the door sent.
    payload: P
    accepted: CommandAcceptedBody
    result: R
    ack: CommandAckStatus

    @property
    def answer(self) -> object:
        """What every door serves: the model its kind applies, else the filing it was accepted as."""
        if (answers := command_answer(self.payload.kind)) is LaunchAnswer:
            assert isinstance(self.result, LaunchOutcome), "a launch kind answers its launch"
            return LaunchAnswer(
                command_id=self.accepted.command_id, launches=self.result.as_launches()
            )
        return self.accepted if answers is None else self.result


@dataclass(frozen=True, slots=True)
class Opened[R, P: CommandPayload]:
    """A command filed (the first act's, for a retry), its *act* awaited by whoever opened it."""

    accepted: CommandAcceptedBody
    act: Coroutine[Any, Any, CommandOutcome[R, P]]


# A kind's handler: what its row names, typed by the answer its applier carries.
type Handler[R] = Callable[[Any], Awaitable[Applier[R]]]


def addressed[P: CommandPayload](stores: Stores, payload: P) -> P:
    """*payload* as the dispatcher acts on it: its addressed campaign id and a cycle command's
    cycle id — the leaf's, below its ``descend`` chain — resolved from the prefixes any door sent
    by the one rule every read takes (`resolve_subject`). A door that tracks what it sent holds
    this."""
    if not isinstance(payload, CampaignPayload):
        return payload
    if isinstance(payload, CyclePayload):
        subject = Subject.below(payload.campaign_id, payload.cycle_id, payload.descend)
        resolved = resolve_course(stores, subject)
        fields: dict[str, str | None] = {"descend": None, **resolved.command_fields}
    else:
        subject = Subject(payload.campaign_id)
        resolved = resolve_subject(stores, subject)
        fields = {"campaign_id": resolved.campaign_id}
    return payload if resolved == subject else payload.model_copy(update=fields)


# `CommandPayload.recorded`: the act as its filing keeps it, and the digest its key names.
type _Recorded = tuple[dict[str, JsonValue], str]


def _inner_refusal(payload: CommandPayload) -> str | None:
    """A cycle kind's refusal of an inner cycle, sent into one."""
    if isinstance(payload, CyclePayload) and payload.descend:
        return payload.inner_refusal
    return None


def _at_leaf[P: CyclePayload](payload: P, leaf: CycleHop) -> P:
    """A payload sent down a ``descend`` chain as its leaf files it."""
    update = {"campaign_id": leaf.campaign_id, "cycle_id": leaf.cycle_id, "descend": None}
    return payload.model_copy(update=update)


def _rejected(command_id: str, detail: str, refused: Exception) -> CommandAckRecord:
    """Its end keeps the refusal its door serves, where it is one."""
    kept = KeptRefusal.of(refused) if isinstance(refused, PotterError) else None
    return CommandAckRecord(command_id=command_id, status="rejected", detail=detail, refusal=kept)


def handler_of(kind: CommandKind) -> Handler[Any]:
    return cast(Handler[Any], imported(_HANDLER_PACKAGE, kind.handler))


def interrupted_lines(verb: str) -> list[str]:
    """Past a second Ctrl+C, which quit before *verb*'s act said its stop: a run's is its paused
    stop (`stop_next_lines`), and a pass is pressed again, replaying every cell it banked."""
    kinds = [kind for kind in CommandKind if kind.cli_verb == verb]
    if any(command_answer(kind) is LaunchAnswer for kind in kinds):
        return stop_next_lines(StopReason.PAUSED)
    return ["next: every cell it measured is banked, and a rerun replays them", f"cli:  `{verb}`"]


def command_answer(kind: CommandKind) -> object | None:
    """The modelled `R` of the kind's handler's `Applier[R]`, what every door answers; `None` where
    it answers the record instead (an `R` of `object` or `Any`); a launch's, its `LaunchAnswer`.
    Any other `R` no wire model carries is refused here."""
    (applied,) = get_args(returned(handler_of(kind)))
    if applied is object or applied is Any:
        return None
    if isinstance(applied, TypeAliasType):
        applied = applied.__value__
    arms = get_args(applied) if get_origin(applied) in (Union, types.UnionType) else (applied,)
    if all(isinstance(arm, type) and issubclass(arm, LaunchOutcome) for arm in arms):
        return LaunchAnswer
    if all(
        arm is type(None) or (isinstance(arm, type) and issubclass(arm, BaseModel)) for arm in arms
    ):
        return cast(object, applied)
    raise TypeError(f"{kind} applies {applied!r}, which no wire model carries")


def keeps_answer(kind: CommandKind) -> bool:
    """A task kind's modelled answer is kept on its end: its task's result and its retry answer
    the act's own, whatever became of its target since. A launch's is its jobs, read live."""
    return kind.answering == "task" and command_answer(kind) not in (None, LaunchAnswer)


def _answer_adapter(kind: CommandKind) -> TypeAdapter[Any]:
    return TypeAdapter(command_answer(kind))


def kept_answer(kind: CommandKind, end: CommandAckRecord) -> object:
    """What the act answered, as its end kept it (`keeps_answer`)."""
    return _answer_adapter(kind).validate_python(end.answer)


async def _kept(replaying: Replaying[Any]) -> object:
    return kept_answer(replaying.payload.kind, replaying.end)


def replay_of(kind: CommandKind) -> Replay[Any]:
    """How a retry of *kind* answers its first act: what its end kept, nothing for a kind that
    answers nothing, else the replay its handler's module registered (`replays`)."""
    handler = handler_of(kind)
    if (replay := _REPLAYS.get(kind)) is not None:
        return replay
    if keeps_answer(kind):
        return _kept
    if get_args(returned(handler))[0] is object:
        return _no_body
    raise TypeError(f"{kind} answers a retry with nothing its end keeps; register its replay")


class CommandDispatcher:
    def __init__(
        self,
        stores: Stores,
        job_registry: JobRegistry | None = None,
        *,
        inline: Inline | None = None,
    ) -> None:
        self.stores = stores
        self.inline = inline
        self._attached = job_registry

    @property
    def job_registry(self) -> JobRegistry:
        if self._attached is None:
            self._attached = JobRegistry.attach(self.stores.jobs_dir)
        return self._attached

    async def _applier(self, dispatched: Dispatched[Any]) -> Applier[Any]:
        return await handler_of(dispatched.payload.kind)(dispatched)

    async def answer[R, P: CommandPayload](
        self, handler: Handler[R], call: CommandCall[P]
    ) -> CommandOutcome[R, P]:
        """`dispatch`, its answer typed by *handler*'s own `Applier[R]`: refused unless *handler*
        is the one the call's row names, so the type a caller reads is what the act answers."""
        if handler is not handler_of(call.kind):
            raise TypeError(f"{call.kind} is answered by {call.kind.handler}, not {handler!r}")
        return cast(CommandOutcome[R, P], await self.dispatch(call))

    async def dispatch(self, call: CommandCall[Any]) -> CommandOutcome[object, Any]:
        """`open`, then its act run to its end; `answer` types what it answers."""
        return await (await self.open(call)).act

    async def open(self, call: CommandCall[Any]) -> Opened[object, Any]:
        """The payload's address base IS its target ledger, acted on as `addressed` resolves it:
        filed once every refusal before its act passed, which raises here."""
        acted = await asyncio.to_thread(addressed, self.stores, call.payload)
        if acted is not call.payload:
            call = replace(call, payload=acted)
        kind = call.kind
        if kind.capability is not None:
            require_capability(
                self.stores.identity, kind.capability, subject=f"command {kind.value!r}"
            )
        if not kind.pressed_at(call.door):
            raise BadRequestError(
                f"command {kind.value!r} is not pressed at the {call.door} door.",
                code="command_door_refused",
            )
        if (inner_refusal := _inner_refusal(call.payload)) is not None:
            raise PayloadInvalidError(inner_refusal, code="inner_cycle_unaddressed")
        filed_as, subject = await asyncio.to_thread(self._filed_on, call.payload)
        # Off the loop: an upload's act hashes its bytes.
        recorded = await asyncio.to_thread(filed_as.recorded)
        if subject is not None and not call.payload.unmatched:
            # Before any check of its target: the first act may have minted or deleted it.
            found = await self._settled(subject, kind, recorded[1], call.idempotency_key)
            if found is not None:
                record, end = found
                accepted = CommandAcceptedBody(command_id=record.command_id, subject=subject)
                return Opened(accepted, self._replayed(call.payload, accepted, end))
        match call.payload:
            case CyclePayload():
                return await self._on_cycle(call, recorded)
            case LinePayload():
                return await self._on_line(call, recorded)
            case CampaignPayload():
                return await self._on_campaign(call, recorded)
            case DraftPayload():
                return await self._on_draft(call, recorded)
            case _:
                applier = await self._applier(
                    Dispatched(self, call.payload, call.command_id, door=call.door)
                )
                return await self._filed(WORKSPACE_SUBJECT, call, applier, recorded)

    async def _settled(
        self, subject: str, kind: CommandKind, act_digest: str, idempotency_key: str
    ) -> tuple[CommandRecord, CommandAckRecord] | None:
        """The act a retry under *idempotency_key* answers, once a first call still acting on it
        ended: a key acts once."""
        while True:
            book = await asyncio.to_thread(self.stores.commands.book)
            found = book.keyed(subject, kind, act_digest, idempotency_key)
            if found is None:
                return None
            if (end := found[1]) is not None:
                return found[0], end
            await asyncio.sleep(_ACTING_POLL_S)

    async def _replayed(
        self, payload: CommandPayload, accepted: CommandAcceptedBody, end: CommandAckRecord
    ) -> CommandOutcome[object, Any]:
        replaying = Replaying(self.stores, self.job_registry, payload, end)
        result = await replay_of(payload.kind)(replaying)
        return CommandOutcome(payload=payload, accepted=accepted, result=result, ack=end.status)

    def _filed_on(self, payload: CommandPayload) -> tuple[CommandPayload, str | None]:
        """*payload* as it is filed and the subject it is filed on, both read off its address
        alone; ``None`` for a line command whose campaign holds no line to read it off."""
        match payload:
            case CyclePayload(descend=descend) if descend:
                *_, leaf = decode_cycle_path(descend)
                below = Subject.below(payload.campaign_id, payload.cycle_id, descend)
                return _at_leaf(payload, leaf), subject_key(below)
            case CyclePayload():
                hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
                return payload, subject_key(self.stores.subject(hop))
            case LinePayload():
                campaign = self.stores.campaigns.load_campaign(payload.campaign_id)
                if campaign is None:
                    return payload, None
                holder = self.stores.campaigns.line_holder(campaign.root_hop)
                return payload, subject_key(self.stores.subject(holder))
            case CampaignPayload():
                return payload, subject_key(Subject(payload.campaign_id))
            case DraftPayload():
                return payload, draft_subject(payload.draft_id)
        return payload, WORKSPACE_SUBJECT

    async def _on_campaign(
        self, call: CommandCall[CampaignPayload], recorded: _Recorded
    ) -> Opened[object, CampaignPayload]:
        owned_campaign(self.stores, call.payload.campaign_id)
        applier = await self._applier(
            Dispatched(self, call.payload, call.command_id, door=call.door)
        )
        subject = subject_key(Subject(call.payload.campaign_id))
        return await self._filed(subject, call, applier, recorded)

    async def _on_line(
        self, call: CommandCall[LinePayload], recorded: _Recorded
    ) -> Opened[object, LinePayload]:
        campaign = owned_campaign(self.stores, call.payload.campaign_id)
        hop = self.stores.campaigns.line_holder(campaign.root_hop)
        applier = await self._applier(
            DispatchedToCycle(self, call.payload, call.command_id, campaign, hop, door=call.door)
        )
        subject = subject_key(self.stores.subject(hop))
        return await self._filed(subject, call, applier, recorded)

    async def _on_cycle(
        self, call: CommandCall[CyclePayload], recorded: _Recorded
    ) -> Opened[object, CyclePayload]:
        payload = call.payload
        hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
        tail = decode_cycle_path(payload.descend)
        leaf_stores, leaf = resolve_cycle_path(self.stores, (hop, *tail))
        if tail:
            return await CommandDispatcher(
                leaf_stores, job_registry=self._attached, inline=self.inline
            )._on_cycle(replace(call, payload=_at_leaf(payload, leaf)), recorded)
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )
        applier = await self._applier(
            DispatchedToCycle(self, payload, call.command_id, campaign, hop, door=call.door)
        )
        subject = subject_key(self.stores.subject(hop))
        return await self._filed(subject, call, applier, recorded)

    async def _on_draft(
        self, call: CommandCall[DraftPayload], recorded: _Recorded
    ) -> Opened[Any, DraftPayload]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = await self._applier(
                DispatchedToDraft(self, call.payload, call.command_id, claim, door=call.door)
            )
            opened = await self._filed(
                draft_subject(call.payload.draft_id), call, applier, recorded
            )
        except BaseException:
            claim.release()
            raise

        async def _released() -> CommandOutcome[Any, DraftPayload]:
            try:
                outcome = await opened.act
            except BaseException:
                claim.release()
                raise
            if applier.rides is None:
                claim.release()
            return outcome

        return Opened(opened.accepted, _released())

    async def _filed[P: CommandPayload, R](
        self, subject: str, call: CommandCall[P], applier: Applier[R], recorded: _Recorded
    ) -> Opened[R, P]:
        """*subject* is the log's spelling of what the command is filed on (`command_events.py`);
        its filer is the producer token, which a first filing takes off the loop."""
        payload, act_digest = recorded
        filer = await asyncio.to_thread(this_producer, self.stores.jobs_dir)
        self.stores.commands.file(
            CommandRecord(
                command_id=call.command_id,
                kind=call.kind,
                payload=payload,
                idempotency_key=call.idempotency_key,
                act_digest=act_digest,
                issued_by=acting_principal(self.stores.identity),
                subject=subject,
                filer=filer,
            )
        )
        accepted = CommandAcceptedBody(command_id=call.command_id, subject=subject)
        return Opened(accepted, self._applied(call, accepted, applier))

    async def _applied[P: CommandPayload, R](
        self, call: CommandCall[P], accepted: CommandAcceptedBody, applier: Applier[R]
    ) -> CommandOutcome[R, P]:
        """The filing and its end are one scope: a filing no act ended is ended ``cancelled``."""
        kind, command_id, log = call.kind, call.command_id, self.stores.commands
        applied: list[R] = []
        ended = False
        try:
            try:
                applied.append(await applier.run())
            except RejectedError as exc:
                ended = True
                refused = ConflictError(
                    f"command {kind} rejected: {exc.reason}",
                    details={"command_id": command_id, "reason": exc.reason},
                )
                await asyncio.to_thread(log.append, _rejected(command_id, exc.reason, refused))
                raise refused from None
            except Exception as exc:
                ended = True
                await asyncio.to_thread(log.append, _rejected(command_id, str(exc), exc))
                raise
            effect = NoEffect() if applier.effect_fn is None else applier.effect_fn(applied[0])
            kept = (
                _answer_adapter(kind).dump_python(applied[0], mode="json")
                if keeps_answer(kind)
                else None
            )
            ended = True
            ack = CommandAckRecord(
                command_id=command_id, status=applier.ack, effect=effect, answer=kept
            )
            await asyncio.to_thread(log.append, ack)
        except BaseException as exc:
            # Admitted and never handed on: what the answer holds is given back in this scope.
            if applied and applier.rides is not None:
                await applier.rides(applied[0], exc)
            if not ended:
                interrupted = f"interrupted: {type(exc).__name__}"
                await asyncio.to_thread(log.ack, command_id, "cancelled", interrupted)
            raise

        return CommandOutcome(
            payload=call.payload, accepted=accepted, result=applied[0], ack=applier.ack
        )
