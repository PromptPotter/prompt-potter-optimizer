from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel

from promptpotter.application.cycle_addressing import resolve_course, resolve_subject
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.jobs.registry import JobRegistry
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
    DraftPayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import CycleHop, Subject, decode_cycle_path
from promptpotter.domain.run_records import NoEffect
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import (
    Stores,
    owned_campaign,
    resolve_cycle_path,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
)
from promptpotter.shared.identity import acting_principal, require_capability
from promptpotter.shared.named_rows import imported, returned

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import Inline
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.phases import RunState


class RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_HANDLER_PACKAGE = "promptpotter.application.commands"
# The longest key a door accepts; each declares it on its own wire (a header, a tool's schema).
MAX_IDEMPOTENCY_KEY_CHARS = 256


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote door passes the key its caller retries under, and a retry under it answers the
    first act; an in-process sender sends once, under a fresh one. Its command id is minted here,
    so a door names what it filed (its `cancel`) before the act answers."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))

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


async def _no_body(_: CommandEffect) -> None:
    """What a deduped retry answers for a command whose applied form answers nothing either."""


@dataclass(frozen=True, slots=True)
class Applier[R]:
    """`effect_fn` reads the ack's effect off what `run` applied, and `replay` answers a retry from
    the first act's, every kind's but an unmatched payload's (`CommandPayload.unmatched`);
    `ack="accepted"` leaves `run` the guard alone, the loop acking `applied`."""

    run: Callable[[], Awaitable[R]]
    replay: Callable[[CommandEffect], Awaitable[R]]
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

        return Applier(_refuse, _no_body)


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
class CommandOutcome[R, P: CommandPayload]:
    # What the dispatcher acted on: its address resolved from the prefix the door sent.
    payload: P
    accepted: CommandAcceptedBody
    result: R
    ack: CommandAckStatus


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


def _rejected(command_id: str, detail: str, refused: Exception) -> CommandAckRecord:
    """Its end keeps the refusal its door serves, where it is one."""
    kept = KeptRefusal.of(refused) if isinstance(refused, PotterError) else None
    return CommandAckRecord(command_id=command_id, status="rejected", detail=detail, refusal=kept)


def handler_of(kind: CommandKind) -> Handler[Any]:
    return cast(Handler[Any], imported(_HANDLER_PACKAGE, kind.handler))


def command_answer(kind: CommandKind) -> object | None:
    """The modelled `R` of the kind's handler's `Applier[R]`, what every door answers; `None` where
    it answers the record instead: an `R` of `object`, or a launch's handle, which a task kind's
    doors track as its task. Any other `R` no wire model carries is refused here."""
    (applied,) = get_args(returned(handler_of(kind)))
    if applied is object:
        return None
    arms = get_args(applied) if get_origin(applied) in (Union, types.UnionType) else (applied,)
    if all(
        arm is type(None) or (isinstance(arm, type) and issubclass(arm, BaseModel)) for arm in arms
    ):
        return cast(object, applied)
    if kind.answering == "task":
        return None
    raise TypeError(f"{kind} applies {applied!r}, which no wire model carries")


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
        return self.stores.run_state(hop)

    async def _applier(self, dispatched: Dispatched[Any]) -> Applier[Any]:
        kind = dispatched.payload.kind
        if kind.capability is not None:
            require_capability(
                self.stores.identity, kind.capability, subject=f"command {kind.value!r}"
            )
        return await handler_of(kind)(dispatched)

    async def answer[R, P: CommandPayload](
        self, handler: Handler[R], call: CommandCall[P]
    ) -> CommandOutcome[R, P]:
        """`dispatch`, its answer typed by *handler*'s own `Applier[R]`: refused unless *handler*
        is the one the call's row names, so the type a caller reads is what the act answers."""
        if handler is not handler_of(call.kind):
            raise TypeError(f"{call.kind} is answered by {call.kind.handler}, not {handler!r}")
        return cast(CommandOutcome[R, P], await self.dispatch(call))

    async def dispatch(self, call: CommandCall[Any]) -> CommandOutcome[object, Any]:
        """The payload's address base IS its target ledger, acted on as `addressed` resolves it;
        `answer` types what it answers."""
        acted = await asyncio.to_thread(addressed, self.stores, call.payload)
        if acted is not call.payload:
            call = replace(call, payload=acted)
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
                applier = await self._applier(Dispatched(self, call.payload))
                return await self._record_and_apply(WORKSPACE_SUBJECT, call, applier)

    async def _on_campaign(
        self, call: CommandCall[CampaignPayload]
    ) -> CommandOutcome[object, CampaignPayload]:
        owned_campaign(self.stores, call.payload.campaign_id)
        applier = await self._applier(Dispatched(self, call.payload))
        subject = subject_key(Subject(call.payload.campaign_id))
        return await self._record_and_apply(subject, call, applier)

    async def _on_line(self, call: CommandCall[LinePayload]) -> CommandOutcome[object, LinePayload]:
        campaign = owned_campaign(self.stores, call.payload.campaign_id)
        hop = self.stores.campaigns.line_holder(campaign.root_hop)
        applier = await self._applier(DispatchedToCycle(self, call.payload, campaign, hop))
        return await self._record_and_apply(subject_key(self.stores.subject(hop)), call, applier)

    async def _on_cycle(
        self, call: CommandCall[CyclePayload]
    ) -> CommandOutcome[object, CyclePayload]:
        payload = call.payload
        hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
        tail = decode_cycle_path(payload.descend)
        if tail and payload.inner_refusal is not None:
            raise PayloadInvalidError(payload.inner_refusal, code="inner_cycle_unaddressed")
        leaf_stores, leaf = resolve_cycle_path(self.stores, (hop, *tail))
        if tail:
            inner = payload.model_copy(
                update={"campaign_id": leaf.campaign_id, "cycle_id": leaf.cycle_id, "descend": None}
            )
            return await CommandDispatcher(
                leaf_stores, job_registry=self._attached, inline=self.inline
            )._on_cycle(replace(call, payload=inner))
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )
        applier = await self._applier(DispatchedToCycle(self, payload, campaign, hop))
        return await self._record_and_apply(subject_key(self.stores.subject(hop)), call, applier)

    async def _on_draft(self, call: CommandCall[DraftPayload]) -> CommandOutcome[Any, DraftPayload]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = await self._applier(DispatchedToDraft(self, call.payload, claim))
            subject = draft_subject(call.payload.draft_id)
            outcome = await self._record_and_apply(subject, call, applier)
        except BaseException:
            claim.release()
            raise
        if applier.rides is None:
            claim.release()
        return outcome

    async def _record_and_apply[P: CommandPayload, R](
        self, subject: str, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R, P]:
        """*subject* is the log's spelling of what the command is filed on (`command_events.py`)."""
        kind, idempotency_key = call.kind, call.idempotency_key
        log = self.stores.commands
        # Off the loop: an upload's act hashes its bytes.
        payload, act_digest = await asyncio.to_thread(call.payload.recorded)
        if not call.payload.unmatched:
            existing = log.book().keyed(subject, kind, act_digest, idempotency_key)
            if existing is not None:
                record, ack = existing
                return CommandOutcome(
                    payload=call.payload,
                    accepted=CommandAcceptedBody(command_id=record.command_id, subject=subject),
                    result=await applier.replay(ack.effect),
                    ack=ack.status,
                )

        command_id = call.command_id
        if command_id in log.book().filed:
            raise ConflictError(
                f"command {command_id} is already filed; send a new call under an id of its own",
                code="command_id_reused",
            )
        applied: list[R] = []
        # The filing and its end are one scope: a filing no act ended is ended ``cancelled``.
        filed = ended = False
        try:
            log.append(
                CommandRecord(
                    command_id=command_id,
                    kind=kind,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    act_digest=act_digest,
                    issued_by=acting_principal(self.stores.identity),
                    subject=subject,
                )
            )
            filed = True
            try:
                applied.append(await applier.run())
            except RejectedError as exc:
                ended = True
                refused = ConflictError(
                    f"command {kind} rejected: {exc.reason}",
                    details={"command_id": command_id, "reason": exc.reason},
                )
                log.append(_rejected(command_id, exc.reason, refused))
                raise refused from None
            except Exception as exc:
                ended = True
                log.append(_rejected(command_id, str(exc), exc))
                raise
            effect = NoEffect() if applier.effect_fn is None else applier.effect_fn(applied[0])
            ended = True
            log.append(CommandAckRecord(command_id=command_id, status=applier.ack, effect=effect))
        except BaseException as exc:
            # Admitted and never handed on: what the answer holds is given back in this scope.
            if applied and applier.rides is not None:
                applier.rides(applied[0], exc)
            if filed and not ended:
                log.ack(command_id, "cancelled", f"interrupted: {type(exc).__name__}")
            raise

        return CommandOutcome(
            payload=call.payload,
            accepted=CommandAcceptedBody(command_id=command_id, subject=subject),
            result=applied[0],
            ack=applier.ack,
        )

    def pause_superseded(self, parent: CycleHop, by: CycleHop) -> None:
        """Filed on the parent, the fork's own command filed beside it."""
        command_id = str(uuid.uuid4())
        payload, act_digest = PauseCyclePayload(
            campaign_id=parent.campaign_id,
            cycle_id=parent.cycle_id,
            reason=f"superseded by fork {by.cycle_id}",
        ).recorded()
        self.stores.commands.append(
            CommandRecord(
                command_id=command_id,
                kind=CommandKind.PAUSE,
                payload=payload,
                idempotency_key=command_id,
                act_digest=act_digest,
                issued_by=acting_principal(self.stores.identity),
                subject=subject_key(self.stores.subject(parent)),
            ),
            CommandAckRecord(command_id=command_id, status="accepted"),
        )
