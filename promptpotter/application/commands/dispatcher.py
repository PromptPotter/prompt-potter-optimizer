from __future__ import annotations

import asyncio
import functools
import threading
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Union, cast, get_args, get_origin

from pydantic import BaseModel, SecretStr

from promptpotter.application.cycle_addressing import resolve_subject
from promptpotter.application.jobs.interlock import this_producer
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_kinds import (
    MAX_COMMAND_ID_CHARS,
    CampaignPayload,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    CyclePayload,
    DeleteCyclePayload,
    DraftPayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, Subject, WorkspaceDir
from promptpotter.domain.run_records import CommandAckStatus, CommandEffect, NoEffect
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    emit_command,
    emit_command_ack,
    reset_acting_command,
    reset_cycle_ledger,
    set_acting_command,
    set_cycle_ledger,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.command_index import (
    AskedCommand,
    CommandIndex,
    LedgerOwner,
    OwnerKind,
    Unrecorded,
    ledger_commands,
)
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.stores import (
    Stores,
    owned_campaign,
    owned_draft_data,
    resolve_cycle_path,
)
from promptpotter.shared.errors import (
    ActStoppedError,
    BadRequestError,
    CommandIdReusedError,
    ConflictError,
    IdempotencyKeyReusedError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.identity import Principal, acting_principal, require_capability
from promptpotter.shared.named_rows import imported, returned

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import Inline
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.phases import RunState


class RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# What an act's ack says when no retry may answer from it: refused, or stopped mid-way.
_UNSETTLING: frozenset[CommandAckStatus] = frozenset({"rejected", "stopped"})


def _retried(
    index: CommandIndex,
    ledger: CycleEventLog,
    call: CommandCall[Any],
    act_digest: str,
    *,
    replays: bool,
) -> AskedCommand | None:
    """The first act a retry names, matched on (kind, act, key) — the whole validated act, by its
    digest; a key the ledger holds for another act is refused, never replayed. A named id the
    index files is that act's retry, under its key, or refused: one id names one act, recorded
    once. An attempt refused or stopped satisfies no key."""
    kind, idempotency_key = call.kind, call.idempotency_key
    if call.id_named and (named := index.asked(call.command_id)) is not None:
        if isinstance(named, Unrecorded):
            raise CommandIdReusedError(
                f"Command id {call.command_id!r} is filed for a {named.filed.kind} act whose record "
                "is not on its ledger, so no act writes one under it: read its status, and send "
                "this under an id of its own.",
            )
        record = named.asked.record
        if record.idempotency_key != idempotency_key or record.act_digest != act_digest:
            raise CommandIdReusedError(
                f"Command id {call.command_id!r} already names a {record.kind} command; send "
                f"this {kind} under an id of its own.",
            )
        return named.asked
    if not replays:
        return None
    keyed = [
        asked
        for asked in ledger_commands(ledger.path).values()
        if asked.record.idempotency_key == idempotency_key
    ]
    for asked in keyed:
        if asked.record.kind != kind or asked.record.act_digest != act_digest:
            raise IdempotencyKeyReusedError(
                f"Key {idempotency_key!r} already names a {asked.record.kind} command with another "
                f"payload on this ledger; send this {kind} under a key of its own.",
                details={"command_id": asked.record.command_id},
            )
    return next((asked for asked in reversed(keyed) if asked.acks and _answers_retry(asked)), None)


def _answers_retry(asked: AskedCommand) -> bool:
    return not any(ack.status in _UNSETTLING for ack in asked.acks)


_HANDLER_PACKAGE = "promptpotter.application.commands"
# The longest key a door accepts; each declares it on its own wire (a header, a tool's schema).
MAX_IDEMPOTENCY_KEY_CHARS = 256


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote door passes the key its caller retries under, and a retry under it answers the
    first act; an in-process sender sends once, under a fresh one. `command_id` is the id its
    record takes, which a caller addresses the act by while it runs (its status, its stop): a
    door's caller may name it, else it is minted. A retry answering the first act answers that
    act's id."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    # The id its caller named, which the index may file already; unnamed, the id is `minted_id`,
    # which it cannot. Each is set once, where it is named or minted, and copied as is.
    named_id: str = ""
    # Set once its record lands: an in-process sender leaving the act to run learns it here.
    recorded: asyncio.Event = field(default_factory=asyncio.Event, compare=False, repr=False)
    minted_id: str = field(default_factory=lambda: str(uuid.uuid4()), repr=False)

    def __post_init__(self) -> None:
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
        named = self.named_id.strip()
        if len(named) > MAX_COMMAND_ID_CHARS:
            raise BadRequestError(
                f"A command id is at most {MAX_COMMAND_ID_CHARS} characters.",
                code="command_id_too_long",
            )
        object.__setattr__(self, "named_id", named)

    @property
    def command_id(self) -> str:
        return self.named_id or self.minted_id

    @property
    def id_named(self) -> bool:
        return bool(self.named_id)

    @property
    def kind(self) -> CommandKind:
        return self.payload.kind


async def _no_body(_: CommandEffect) -> None:
    """What a deduped retry answers for a command whose applied form answers nothing either."""


@dataclass(frozen=True, slots=True)
class Applier[R]:
    """`effect_fn` reads the ack's effect off what `run` applied, and `replay` answers a retry from
    the first act's; `replay=None` dedupes nothing; `ack="accepted"` leaves `run` the guard alone,
    the loop acking `applied`."""

    run: Callable[[], Awaitable[R]]
    replay: Callable[[CommandEffect], Awaitable[R]] | None
    effect_fn: Callable[[R], CommandEffect] | None = None
    ack: CommandAckStatus = "applied"
    # Set where what `run` answers carries the act on past its ack (a Start's admitted launch holds
    # its slot, and the draft's claim, to its mint): how that answer is given back where the act
    # raises after `run` answered it. A draft command's claim is otherwise the dispatcher's until
    # the act ends, and released on every raise.
    rides: Callable[[R, BaseException], None] | None = None

    @staticmethod
    def silent(run: Callable[[], Awaitable[object]]) -> Applier[object]:
        return Applier(run, _no_body)

    @staticmethod
    def for_loop(refusal: str) -> Applier[object]:
        async def _guard() -> None:
            if refusal:
                raise RejectedError(refusal)

        return Applier(_guard, _no_body, ack="accepted")

    @staticmethod
    def refusing(reason: str) -> Applier[object]:
        async def _refuse() -> None:
            raise RejectedError(reason)

        return Applier(_refuse, replay=None)


@dataclass(frozen=True, slots=True)
class Dispatched[P: CommandPayload]:
    dispatcher: CommandDispatcher
    payload: P


@dataclass(frozen=True, slots=True)
class DispatchedToCycle[P: CommandPayload](Dispatched[P]):
    campaign: Campaign
    hop: CycleHop


@dataclass(frozen=True, slots=True)
class DispatchedToDraft[P: CommandPayload](Dispatched[P]):
    claim: DraftClaim


@dataclass(frozen=True, slots=True)
class CommandOutcome[R]:
    accepted: CommandAcceptedBody
    result: R
    ack: CommandAckStatus


# A kind's handler: what its row names, typed by the answer its applier carries.
type Handler[R] = Callable[[Any], Awaitable[Applier[R]]]


def handler_of(kind: CommandKind) -> Handler[Any]:
    return cast(Handler[Any], imported(_HANDLER_PACKAGE, kind.handler))


def command_answer(kind: CommandKind) -> object | None:
    """The modelled `R` of the kind's handler's `Applier[R]`, what every door answers; `None` where
    it answers the record instead: an `R` of `object`, or a launch's handle, which a startable
    kind's doors track by its command id. Any other `R` no wire model carries is refused here."""
    (applied,) = get_args(returned(handler_of(kind)))
    if applied is object:
        return None
    arms = get_args(applied) if get_origin(applied) in (Union, types.UnionType) else (applied,)
    if all(
        arm is type(None) or (isinstance(arm, type) and issubclass(arm, BaseModel)) for arm in arms
    ):
        return cast(object, applied)
    if kind.answering == "started":
        return None
    raise TypeError(f"{kind} applies {applied!r}, which no wire model carries")


class OwnedLedger(NamedTuple):
    log: CycleEventLog
    owner: LedgerOwner


def _land(
    stores: Stores,
    owner: LedgerOwner,
    *,
    command_id: str,
    kind: CommandKind,
    payload: dict[str, Any],
    idempotency_key: str,
    act_digest: str,
    issued_by: Principal,
) -> None:
    """Files the id on its ledger's owner, stamped with this process's producer id, then writes
    its record on the bound ledger: one step with no await inside, so an id filed without its
    record reads as landing only while this step runs. A record that raises voids the filing, so
    its status reads that it never landed."""
    stores.command_index.file(
        command_id,
        owner,
        kind=kind,
        issued_by=issued_by.id,
        filer=this_producer(stores.jobs_dir),
    )
    try:
        emit_command(
            command_id=command_id,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            act_digest=act_digest,
            issued_by=issued_by,
        )
    except BaseException:
        stores.command_index.void(command_id)
        raise


# The ack of an act whose caller was cancelled: before its act applied, or mid-way through it.
_CALL_CANCELLED = "call cancelled"


class _Landing:
    """A record's landing met with its caller's cancel: the stopped ack is written once both
    happened, by whichever came second, so it rides no await of the cancelled caller (a second
    cancel, a shutdown's) and lands where that caller runs no more."""

    def __init__(self, command_id: str) -> None:
        self._command_id = command_id
        self._lock = threading.Lock()
        self._landed = self._cancelled = False

    def _met(self, *, landed: bool = False, cancelled: bool = False) -> None:
        with self._lock:
            before = self._landed and self._cancelled
            self._landed |= landed
            self._cancelled |= cancelled
            if not before and self._landed and self._cancelled:
                emit_command_ack(
                    command_id=self._command_id,
                    status="stopped",
                    detail=_CALL_CANCELLED,
                    effect=NoEffect(),
                )

    def landed(self) -> None:
        self._met(landed=True)

    def cancelled(self) -> None:
        self._met(cancelled=True)


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

    def run_state(self, hop: CycleHop) -> RunState:
        return derive_run_state(self.stores.campaigns.cycle_dir(hop))

    async def _applier(self, dispatched: Dispatched[Any]) -> Applier[Any]:
        kind = dispatched.payload.kind
        if kind.capability is not None:
            require_capability(
                self.stores.identity, kind.capability, subject=f"command {kind.value!r}"
            )
        return await handler_of(kind)(dispatched)

    async def answer[R](self, handler: Handler[R], call: CommandCall[Any]) -> CommandOutcome[R]:
        """`dispatch`, its answer typed by *handler*'s own `Applier[R]`: refused unless *handler*
        is the one the call's row names, so the type a caller reads is what the act answers."""
        if handler is not handler_of(call.kind):
            raise TypeError(f"{call.kind} is answered by {call.kind.handler}, not {handler!r}")
        return cast(CommandOutcome[R], await self.dispatch(call))

    async def dispatch(self, call: CommandCall[Any]) -> CommandOutcome[object]:
        """The payload's address base IS its target ledger, its addressed campaign id — a cycle
        command's leaf, below its ``descend`` chain — resolved from the prefix any door sent by the
        one rule every read takes (`resolve_subject`); `answer` types what it answers."""
        if isinstance(payload := call.payload, CampaignPayload):
            if isinstance(payload, CyclePayload):
                subject = Subject.below(payload.campaign_id, payload.cycle_id, payload.descend)
                resolved = await asyncio.to_thread(resolve_subject, self.stores, subject)
                fields: dict[str, str | None] = {"descend": None, **resolved.command_fields}
            else:
                subject = Subject(payload.campaign_id)
                resolved = await asyncio.to_thread(resolve_subject, self.stores, subject)
                fields = {"campaign_id": resolved.campaign_id}
            if resolved != subject:
                call = replace(call, payload=payload.model_copy(update=fields))
        match call.payload:
            case CyclePayload():
                return await self._on_cycle(call)
            case LinePayload():
                return await self._on_line(call)
            case CampaignPayload():
                return await self._on_campaign(call)
            case DraftPayload():
                return await self._on_draft(call)
            case _:
                ledger = self._ledger("workspace", self.stores.base_dir)
                applier = await self._applier(Dispatched(self, call.payload))
                return await self._record_and_apply(ledger, call, applier)

    async def _on_campaign(self, call: CommandCall[CampaignPayload]) -> CommandOutcome[object]:
        owned_campaign(self.stores, call.payload.campaign_id)
        # The WORKSPACE ledger: `delete` REMOVES the campaign tree, and its own ledger with it.
        ledger = self._ledger("workspace", self.stores.base_dir)
        applier = await self._applier(Dispatched(self, call.payload))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_line(self, call: CommandCall[LinePayload]) -> CommandOutcome[object]:
        campaign = owned_campaign(self.stores, call.payload.campaign_id)
        hop = self.stores.campaigns.line_holder(campaign.root_hop)
        ledger = self._ledger("cycle", self.stores.campaigns.cycle_dir(hop))
        applier = await self._applier(DispatchedToCycle(self, call.payload, campaign, hop))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_cycle(self, call: CommandCall[CyclePayload]) -> CommandOutcome[object]:
        """The act lands on its leaf's ledger and applies to that leaf, its record keeping the
        address it was sent to (root hop and ``descend``): a reader of the record resolves the leaf
        as this did, by `Subject.below`."""
        payload = call.payload
        path = Subject.below(payload.campaign_id, payload.cycle_id, payload.descend).path
        if len(path) > 1 and payload.inner_refusal is not None:
            raise PayloadInvalidError(payload.inner_refusal, code="inner_cycle_unaddressed")
        leaf_stores, hop = resolve_cycle_path(self.stores, path)
        if len(path) > 1:
            leaf = CommandDispatcher(leaf_stores, job_registry=self._attached, inline=self.inline)
            return await leaf._on_leaf(call, hop)
        return await self._on_leaf(call, hop)

    async def _on_leaf(
        self, call: CommandCall[CyclePayload], hop: CycleHop
    ) -> CommandOutcome[object]:
        payload = call.payload
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )
        # Its apply removes the dir AND its ledger, so the record and ack go on the campaign root's.
        recorded_on = campaign.root_hop if isinstance(payload, DeleteCyclePayload) else hop
        ledger = self._ledger("cycle", self.stores.campaigns.cycle_dir(recorded_on))
        applier = await self._applier(DispatchedToCycle(self, payload, campaign, hop))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_draft(self, call: CommandCall[DraftPayload]) -> CommandOutcome[Any]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft_data(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = await self._applier(DispatchedToDraft(self, call.payload, claim))
            ledger = self._ledger("draft", self.stores.drafts.draft_dir(call.payload.draft_id))
            outcome = await self._record_and_apply(ledger, call, applier)
        except BaseException:
            claim.release()
            raise
        if applier.rides is None:
            claim.release()
        return outcome

    def _ledger(self, kind: OwnerKind, directory: Path) -> OwnedLedger:
        log = (
            CycleEventLog.open_workspace(WorkspaceDir(directory))
            if kind == "workspace"
            else CycleEventLog.open(CycleDir(directory))
        )
        return OwnedLedger(log, self.stores.command_index.owner(kind, directory))

    async def _record_and_apply[P: CommandPayload, R](
        self, owned: OwnedLedger, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R]:
        ledger = owned.log
        kind, idempotency_key = call.kind, call.idempotency_key
        # Off the loop: an upload's act hashes its bytes.
        payload, act_digest = await asyncio.to_thread(call.payload.recorded)
        # The digest holds no secret's text, so a retry would match two secrets as one act.
        if applier.replay is not None and any(
            isinstance(value, SecretStr) for value in dict(call.payload).values()
        ):
            raise TypeError(f"{kind} replays, yet carries a secret its digest cannot name")
        # A minted id names nothing yet, so only a replaying kind or a named id reads the ledger.
        if applier.replay is not None or call.id_named:
            existing = await asyncio.to_thread(
                functools.partial(_retried, replays=applier.replay is not None),
                self.stores.command_index,
                ledger,
                call,
                act_digest,
            )
            if existing is not None:
                if applier.replay is None or not existing.acks or not _answers_retry(existing):
                    raise CommandIdReusedError(
                        f"Command id {call.command_id!r} names a {kind} act already, which a "
                        "retry does not answer: read its status by that id.",
                    )
                last = existing.acks[-1]
                return CommandOutcome(
                    accepted=CommandAcceptedBody(command_id=existing.record.command_id),
                    result=await applier.replay(last.effect),
                    ack=last.status,
                )

        command_id, issued_by = call.command_id, acting_principal(self.stores.identity)
        token = set_cycle_ledger(ledger)
        acting = set_acting_command(command_id)
        applied: list[R] = []
        try:
            # Off the loop, under the bound ledger (`to_thread` carries the context). A cancel at
            # this await leaves the step running: its completion meets the cancel (`_Landing`).
            landing = _Landing(command_id)
            land = functools.partial(
                _land,
                self.stores,
                owned.owner,
                command_id=command_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                act_digest=act_digest,
                issued_by=issued_by,
            )

            def landed() -> None:
                land()
                landing.landed()

            step = asyncio.ensure_future(asyncio.to_thread(landed))
            step.add_done_callback(lambda done: done.cancelled() or done.exception())
            try:
                await asyncio.shield(step)
            except asyncio.CancelledError:
                landing.cancelled()
                raise
            call.recorded.set()
            ack_status: CommandAckStatus = applier.ack
            ack_detail = ""
            try:
                applied.append(await applier.run())
            except RejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except BaseException as exc:
                # A cancel, an interrupt or an exit stops the act; any other raise rejects it.
                stopped = isinstance(exc, ActStoppedError) or not isinstance(exc, Exception)
                cancel = _CALL_CANCELLED if isinstance(exc, asyncio.CancelledError) else ""
                emit_command_ack(
                    command_id=command_id,
                    status="stopped" if stopped else "rejected",
                    detail=str(exc) or cancel or type(exc).__name__,
                    effect=NoEffect(),
                )
                raise
            try:
                effect = (
                    applier.effect_fn(applied[0])
                    if (applier.effect_fn is not None and ack_status != "rejected")
                    else NoEffect()
                )
                emit_command_ack(
                    command_id=command_id, status=ack_status, detail=ack_detail, effect=effect
                )
            except BaseException as exc:
                # Admitted and never handed on: what the answer holds is given back in this scope.
                if applied and applier.rides is not None:
                    applier.rides(applied[0], exc)
                raise
        finally:
            reset_acting_command(acting)
            reset_cycle_ledger(token)

        if not applied:
            raise ConflictError(
                f"command {kind} rejected: {ack_detail}",
                details={"command_id": command_id, "reason": ack_detail},
            )

        return CommandOutcome(
            accepted=CommandAcceptedBody(command_id=command_id), result=applied[0], ack=ack_status
        )

    def pause_superseded(self, parent: CyclePayload, by: CycleHop) -> None:
        """Rides the ledger bound by the fork command being recorded, which is the parent's, and
        addresses the parent as that fork's record does."""
        command_id, issued_by = str(uuid.uuid4()), acting_principal(self.stores.identity)
        bound = active_cycle_ledger()
        assert bound is not None, "a fork records its parent's pause on the ledger it binds"
        index = self.stores.command_index
        owner = index.owner("cycle", CycleLayout.of_ledger(bound.path).cycle_dir)
        payload, act_digest = PauseCyclePayload(
            campaign_id=parent.campaign_id,
            cycle_id=parent.cycle_id,
            descend=parent.descend,
            reason=f"superseded by fork {by.cycle_id}",
        ).recorded()
        _land(
            self.stores,
            owner,
            command_id=command_id,
            kind=CommandKind.PAUSE_CYCLE,
            payload=payload,
            idempotency_key=command_id,
            act_digest=act_digest,
            issued_by=issued_by,
        )
        emit_command_ack(command_id=command_id, status="accepted", effect=NoEffect())
