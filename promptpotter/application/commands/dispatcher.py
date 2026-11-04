from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, SecretStr

from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_kinds import (
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
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, decode_cycle_path
from promptpotter.domain.run_records import (
    CommandAckRecord,
    CommandAckStatus,
    CommandEffect,
    CommandRecord,
    NoEffect,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import (
    Stores,
    owned_campaign,
    owned_draft_data,
    resolve_cycle_path,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    IdempotencyKeyReusedError,
    NotFoundError,
    PayloadInvalidError,
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


class _IdempotentMatch(FrozenModel):
    command_id: str
    offset: int
    ack: CommandAckStatus
    effect: CommandEffect


def _find_idempotent_command(
    ledger: CycleEventLog, kind: CommandKind, act_digest: str, idempotency_key: str
) -> _IdempotentMatch | None:
    """Matched on (kind, act, key), the act its retry names — the whole validated act, by its
    digest; a key the ledger holds for another act is refused, never replayed. A REJECTED attempt
    satisfies no key."""
    keyed: dict[str, int] = {}
    applied: _IdempotentMatch | None = None
    # The ledger's OWN offset, never a fork-chain position: the client aligns the SSE tail on it.
    for offset, record in ledger.iter():
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            if record.kind != kind or record.act_digest != act_digest:
                raise IdempotencyKeyReusedError(
                    f"Key {idempotency_key!r} already names a {record.kind} command with another "
                    f"payload on this ledger; send this {kind} under a key of its own.",
                    details={"command_id": record.command_id},
                )
            keyed[record.command_id] = offset
        elif isinstance(record, CommandAckRecord) and record.status != "rejected":
            at = keyed.get(record.command_id)
            if at is not None:
                applied = _IdempotentMatch(
                    command_id=record.command_id, offset=at, ack=record.status, effect=record.effect
                )
    return applied


_HANDLER_PACKAGE = "promptpotter.application.commands"
# The longest key a door accepts; each declares it on its own wire (a header, a tool's schema).
MAX_IDEMPOTENCY_KEY_CHARS = 256


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote door passes the key its caller retries under, and a retry under it answers the
    first act; an in-process sender sends once, under a fresh one."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)

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
        """The payload's address base IS its target ledger; `answer` types what it answers."""
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
                ledger = CycleEventLog.open_workspace(self.stores.base_dir)
                applier = await self._applier(Dispatched(self, call.payload))
                return await self._record_and_apply(ledger, call, applier)

    async def _on_campaign(self, call: CommandCall[CampaignPayload]) -> CommandOutcome[object]:
        owned_campaign(self.stores, call.payload.campaign_id)
        # The WORKSPACE ledger: `delete` REMOVES the campaign tree, and its own ledger with it.
        ledger = CycleEventLog.open_workspace(self.stores.base_dir)
        applier = await self._applier(Dispatched(self, call.payload))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_line(self, call: CommandCall[LinePayload]) -> CommandOutcome[object]:
        campaign = owned_campaign(self.stores, call.payload.campaign_id)
        hop = self.stores.campaigns.line_holder(campaign.root_hop)
        ledger = CycleEventLog.open(CycleDir(self.stores.campaigns.cycle_dir(hop)))
        applier = await self._applier(DispatchedToCycle(self, call.payload, campaign, hop))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_cycle(self, call: CommandCall[CyclePayload]) -> CommandOutcome[object]:
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
            )._on_cycle(CommandCall(inner, call.idempotency_key))
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )
        # Its apply removes the dir AND its ledger, so the record and ack go on the campaign root's.
        recorded_on = campaign.root_hop if isinstance(payload, DeleteCyclePayload) else hop
        ledger = CycleEventLog.open(CycleDir(self.stores.campaigns.cycle_dir(recorded_on)))
        applier = await self._applier(DispatchedToCycle(self, payload, campaign, hop))
        return await self._record_and_apply(ledger, call, applier)

    async def _on_draft(self, call: CommandCall[DraftPayload]) -> CommandOutcome[Any]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft_data(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = await self._applier(DispatchedToDraft(self, call.payload, claim))
            draft_dir = self.stores.drafts.draft_dir(call.payload.draft_id)
            outcome = await self._record_and_apply(
                CycleEventLog.open(CycleDir(draft_dir)), call, applier
            )
        except BaseException:
            claim.release()
            raise
        if applier.rides is None:
            claim.release()
        return outcome

    async def _record_and_apply[P: CommandPayload, R](
        self, ledger: CycleEventLog, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R]:
        kind, idempotency_key = call.kind, call.idempotency_key
        # Off the loop: an upload's act hashes its bytes.
        payload, act_digest = await asyncio.to_thread(call.payload.recorded)
        if applier.replay is not None:
            # The digest holds no secret's text, so a retry would match two secrets as one act.
            if any(isinstance(value, SecretStr) for value in dict(call.payload).values()):
                raise TypeError(f"{kind} replays, yet carries a secret its digest cannot name")
            existing = _find_idempotent_command(ledger, kind, act_digest, idempotency_key)
            if existing is not None:
                return CommandOutcome(
                    accepted=CommandAcceptedBody(command_id=existing.command_id),
                    result=await applier.replay(existing.effect),
                    ack=existing.ack,
                )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        applied: list[R] = []
        try:
            emit_command(
                command_id=command_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                act_digest=act_digest,
                issued_by=acting_principal(self.stores.identity),
            )
            ack_status: CommandAckStatus = applier.ack
            ack_detail = ""
            try:
                applied.append(await applier.run())
            except RejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except Exception as exc:
                emit_command_ack(
                    command_id=command_id, status="rejected", detail=str(exc), effect=NoEffect()
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
            reset_cycle_ledger(token)

        if not applied:
            raise ConflictError(
                f"command {kind} rejected: {ack_detail}",
                details={"command_id": command_id, "reason": ack_detail},
            )

        return CommandOutcome(
            accepted=CommandAcceptedBody(command_id=command_id), result=applied[0], ack=ack_status
        )

    def pause_superseded(self, parent: CycleHop, by: CycleHop) -> None:
        """Rides the ledger bound by the fork command being recorded, which is the parent's."""
        command_id = str(uuid.uuid4())
        payload, act_digest = PauseCyclePayload(
            campaign_id=parent.campaign_id,
            cycle_id=parent.cycle_id,
            reason=f"superseded by fork {by.cycle_id}",
        ).recorded()
        emit_command(
            command_id=command_id,
            kind=CommandKind.PAUSE_CYCLE,
            payload=payload,
            idempotency_key=command_id,
            act_digest=act_digest,
            issued_by=acting_principal(self.stores.identity),
        )
        emit_command_ack(command_id=command_id, status="accepted", effect=NoEffect())
