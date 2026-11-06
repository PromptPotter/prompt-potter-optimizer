from __future__ import annotations

import asyncio
import functools
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, SecretStr, TypeAdapter
from pydantic_core import to_jsonable_python

from promptpotter.application.acting_command import (
    PassStoppedError,
    ended_by,
    filed,
)
from promptpotter.application.acting_command import filing as filing_of
from promptpotter.application.cycle_addressing import resolve_subject
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.jobs.interlock import producer_alive
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_kinds import (
    CampaignPayload,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    CyclePayload,
    DraftPayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import (
    CycleDir,
    CycleHop,
    CyclePath,
    Subject,
    decode_cycle_path,
)
from promptpotter.domain.run_records import (
    AnsweredEffect,
    CommandAccepted,
    CommandApplied,
    CommandEffect,
    CommandSubject,
    CommandVoided,
    NoEffect,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.runtime_flags import derive_run_state, run_mark
from promptpotter.infrastructure.store.command_log import CommandLog, superseded_lookahead
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
)
from promptpotter.shared.identity import require_capability
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

# What a command filed by a process gone before its act ended reads as.
_FILER_GONE = "ended: its filer is gone"

# How a command's act ended where it was applied, read off the applier.
AckStatus = Literal["accepted", "applied"]


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote door passes the key its caller retries under, and a retry under it answers the
    first act; an in-process sender sends once, under a fresh one. ``command_id`` is the id the
    command is filed under: a door that tracks it (a host task) mints it before it dispatches."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))

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
    """`effect_fn` reads the effect off what `run` applied, and `replay` answers a retry from the
    first act's, never acting twice; `ack="accepted"` leaves `run` the guard alone, the loop
    marking it applied."""

    run: Callable[[], Awaitable[R]]
    replay: Callable[[CommandEffect], Awaitable[R]]
    effect_fn: Callable[[R], CommandEffect] | None = None
    ack: AckStatus = "applied"
    # Set where what `run` answers carries the act on past its ack (a Start's admitted launch holds
    # its slot, and the draft's claim, to its mint): how that answer is given back where the act
    # raises after `run` answered it. A draft command's claim is otherwise the dispatcher's until
    # the act ends, and released on every raise.
    rides: Callable[[R, BaseException], None] | None = None

    @staticmethod
    def silent(run: Callable[[], Awaitable[object]]) -> Applier[object]:
        return Applier(run, _no_body)

    @staticmethod
    def keeping[A](run: Callable[[], Awaitable[A]], kind: CommandKind) -> Applier[A]:
        """Its applied answer kept whole as its command's effect, so a retry, and a task's
        result read in any process, answer it off the log (`answer_kept`)."""

        def _effect(answer: A) -> CommandEffect:
            if isinstance(answer, _EFFECT_MODELS):
                return cast(CommandEffect, answer)
            return AnsweredEffect(answer=to_jsonable_python(answer))

        async def _replay(effect: CommandEffect) -> A:
            return cast(A, answer_kept(kind, effect))

        return Applier(run, _replay, effect_fn=_effect)

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

        # A refused command satisfies no key (`CommandLog.keyed`): its retry is refused again.
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
    ack: AckStatus


@dataclass(frozen=True, slots=True)
class _Filing:
    """Where a command is filed and its act runs: the address it is filed on, the ledger its act's
    effects land on, and the cycle a loop command's run mark is read off."""

    subject: str
    ledger: CycleEventLog
    cycle_dir: Path | None = None


# A kind's handler: what its row names, typed by the answer its applier carries.
type Handler[R] = Callable[[Any], Awaitable[Applier[R]]]


def addressed[P: CommandPayload](stores: Stores, payload: P) -> P:
    """*payload* as the dispatcher acts on it: its addressed campaign id — a cycle command's leaf,
    below its ``descend`` chain — resolved from the prefix any door sent by the one rule every read
    takes (`resolve_subject`). A door that names a pass's stop before the pass ends holds this."""
    if not isinstance(payload, CampaignPayload):
        return payload
    if isinstance(payload, CyclePayload):
        subject = Subject.below(payload.campaign_id, payload.cycle_id, payload.descend)
        resolved = resolve_subject(stores, subject)
        fields: dict[str, str | None] = {"descend": None, **resolved.command_fields}
    else:
        subject = Subject(payload.campaign_id)
        resolved = resolve_subject(stores, subject)
        fields = {"campaign_id": resolved.campaign_id}
    return payload if resolved == subject else payload.model_copy(update=fields)


def handler_of(kind: CommandKind) -> Handler[Any]:
    return cast(Handler[Any], imported(_HANDLER_PACKAGE, kind.handler))


# An effect that is itself an answer model is kept as it is; any other answer rides `AnsweredEffect`.
_EFFECT_MODELS = get_args(get_args(CommandEffect)[0])


@functools.cache
def _answer_adapter(kind: CommandKind) -> TypeAdapter[object]:
    model = command_answer(kind)
    if model is None:
        raise TypeError(f"{kind} answers its filing, which keeps no answer")
    return TypeAdapter(model)


def answer_kept(kind: CommandKind, effect: CommandEffect) -> object:
    """*kind*'s applied answer as its command's end kept it (`Applier.keeping`)."""
    kept = effect.answer if isinstance(effect, AnsweredEffect) else effect
    return _answer_adapter(kind).validate_python(kept)


def command_answer(kind: CommandKind) -> object | None:
    """The modelled `R` of the kind's handler's `Applier[R]`, what every door answers; `None` where
    it answers the filing instead: an `R` of `object`, or a launch's handle, which a task kind's
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
        inside: CyclePath = (),
    ) -> None:
        self.stores = stores
        self.inline = inline
        self._attached = job_registry
        # The L4 sandbox chain *stores* is a leaf of: what a cycle's subject is filed under.
        self._inside = inside

    @property
    def job_registry(self) -> JobRegistry:
        if self._attached is None:
            self._attached = JobRegistry.attach(self.stores.jobs_dir)
        return self._attached

    @property
    def log(self) -> CommandLog:
        """The account's command log: every command this dispatcher files lands here, a sandbox's
        under its full subject."""
        return self.stores.commands

    def _course(self, hop: CycleHop) -> str:
        return CommandSubject.of(Subject.of((*self._inside, hop)))

    def run_state(self, hop: CycleHop) -> RunState:
        return derive_run_state(self.stores.campaigns.cycle_dir(hop))

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
        """Filed on the account's log at the address `addressed` resolves; `answer` types what it
        answers."""
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
                filing = _Filing(
                    CommandSubject.WORKSPACE, CycleEventLog.open_workspace(self.stores.base_dir)
                )
                applier = await self._applier(Dispatched(self, call.payload))
                return await self._record_and_apply(filing, call, applier)

    async def _on_campaign(
        self, call: CommandCall[CampaignPayload]
    ) -> CommandOutcome[object, CampaignPayload]:
        owned_campaign(self.stores, call.payload.campaign_id)
        # The WORKSPACE ledger: `delete` REMOVES the campaign tree, and its own ledger with it.
        filing = _Filing(
            CommandSubject.of(Subject(call.payload.campaign_id, inside=self._inside)),
            CycleEventLog.open_workspace(self.stores.base_dir),
        )
        applier = await self._applier(Dispatched(self, call.payload))
        return await self._record_and_apply(filing, call, applier)

    async def _on_line(self, call: CommandCall[LinePayload]) -> CommandOutcome[object, LinePayload]:
        campaign = owned_campaign(self.stores, call.payload.campaign_id)
        hop = self.stores.campaigns.line_holder(campaign.root_hop)
        cycle_dir = self.stores.campaigns.cycle_dir(hop)
        filing = _Filing(self._course(hop), CycleEventLog.open(CycleDir(cycle_dir)), cycle_dir)
        applier = await self._applier(DispatchedToCycle(self, call.payload, campaign, hop))
        return await self._record_and_apply(filing, call, applier)

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
                leaf_stores,
                job_registry=self._attached,
                inline=self.inline,
                inside=(*self._inside, hop, *tail[:-1]),
            )._on_cycle(replace(call, payload=inner))
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )
        cycle_dir = self.stores.campaigns.cycle_dir(hop)
        # Its effects land on the cycle's own ledger; a delete's apply removes it, and the log,
        # which no delete reaches, keeps what was asked.
        filing = _Filing(self._course(hop), CycleEventLog.open(CycleDir(cycle_dir)), cycle_dir)
        applier = await self._applier(DispatchedToCycle(self, payload, campaign, hop))
        return await self._record_and_apply(filing, call, applier)

    async def _on_draft(self, call: CommandCall[DraftPayload]) -> CommandOutcome[Any, DraftPayload]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = await self._applier(DispatchedToDraft(self, call.payload, claim))
            draft_dir = self.stores.drafts.draft_dir(call.payload.draft_id)
            filing = _Filing(
                CommandSubject.draft(call.payload.draft_id),
                CycleEventLog.open(CycleDir(draft_dir)),
            )
            outcome = await self._record_and_apply(filing, call, applier)
        except BaseException:
            claim.release()
            raise
        if applier.rides is None:
            claim.release()
        return outcome

    async def _replayed[P: CommandPayload, R](
        self, filing: _Filing, call: CommandCall[P], applier: Applier[R], act_digest: str
    ) -> CommandOutcome[R, P] | None:
        """A retry under its key on its subject answers the first act, off the log, and never acts
        twice. A secret's payload is matched by no retry: what it set is never kept to compare, so
        its retry acts again."""
        if any(isinstance(value, SecretStr) for value in dict(call.payload).values()):
            return None
        kind, log = call.kind, self.log
        existing = log.keyed(kind, act_digest, call.idempotency_key, filing.subject)
        if existing is None:
            return None
        command_id = existing.filed.command_id
        if existing.status == "filed" and not producer_alive(
            self.stores.jobs_dir, existing.filed.filer
        ):
            # Its filer died before the act ended: nothing it asked is known done, so a retry acts.
            log.append(CommandVoided(command_id=command_id, detail=_FILER_GONE))
            return None
        if existing.status == "filed":
            raise ConflictError(
                f"command {kind} under this key is still being applied; read its status.",
                code="command_in_flight",
                details={"command_id": command_id},
            )
        accepted = CommandAcceptedBody(command_id=command_id, subject=existing.filed.subject)
        ack: AckStatus = "accepted" if existing.status == "accepted" else "applied"
        result = await applier.replay(existing.effect)
        return CommandOutcome(payload=call.payload, accepted=accepted, result=result, ack=ack)

    async def _record_and_apply[P: CommandPayload, R](
        self, filing: _Filing, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R, P]:
        kind, log = call.kind, self.log
        # Off the loop: an upload's act hashes its bytes.
        payload, act_digest = await asyncio.to_thread(call.payload.recorded)
        if (replayed := await self._replayed(filing, call, applier, act_digest)) is not None:
            return replayed

        command_id = call.command_id
        filed_as = filing_of(
            self.stores,
            command_id=command_id,
            kind=kind,
            payload=payload,
            idempotency_key=call.idempotency_key,
            act_digest=act_digest,
            subject=filing.subject,
        )
        applied: list[R] = []
        rejected: ConflictError | None = None
        async with filed(log, filed_as) as acting:
            token = set_cycle_ledger(filing.ledger)
            try:
                try:
                    applied.append(await applier.run())
                except RejectedError as exc:
                    rejected = ConflictError(
                        f"command {kind} rejected: {exc.reason}",
                        details={"command_id": command_id, "reason": exc.reason},
                    )
                if rejected is not None:
                    acting.end(ended_by(rejected, command_id))
                elif (stop := acting.cancel()) is not None:
                    # A stop reached the act as it answered: it stands, and the act answers it.
                    raise PassStoppedError(stop.detail)
                else:
                    effect = (
                        applier.effect_fn(applied[0])
                        if applier.effect_fn is not None
                        else NoEffect()
                    )
                    acting.end(self._acked(filing, applier.ack, command_id, effect))
                    self._void_superseded(filing, kind, command_id)
            except BaseException as exc:
                # Admitted and never handed on: what the answer holds is given back in this scope.
                if applied and applier.rides is not None:
                    applier.rides(applied[0], exc)
                raise
            finally:
                reset_cycle_ledger(token)

        if rejected is not None:
            raise rejected
        return CommandOutcome(
            payload=call.payload,
            accepted=CommandAcceptedBody(command_id=command_id, subject=filing.subject),
            result=applied[0],
            ack=applier.ack,
        )

    @staticmethod
    def _acked(
        filing: _Filing, ack: AckStatus, command_id: str, effect: CommandEffect
    ) -> CommandAccepted | CommandApplied:
        if ack == "applied":
            return CommandApplied(command_id=command_id, effect=effect)
        mark = None if filing.cycle_dir is None else run_mark(filing.cycle_dir)
        return CommandAccepted(command_id=command_id, run_mark=mark, effect=effect)

    def _void_superseded(self, filing: _Filing, kind: CommandKind, command_id: str) -> None:
        """A look-ahead's press supersedes the one standing on its cycle: voided on the log, so
        its own status says it stands no more."""
        if kind is not CommandKind.SET_SAMPLE_LOOKAHEAD:
            return
        log = self.log
        if (prior := superseded_lookahead(log, log.place(filing.subject), command_id)) is not None:
            log.append(CommandVoided(command_id=prior, detail=f"superseded by {command_id}"))

    def pause_superseded(self, parent: CycleHop, by: CycleHop) -> None:
        """Filed on the account's log for the parent the fork supersedes, and accepted there."""
        command_id = str(uuid.uuid4())
        payload, act_digest = PauseCyclePayload(
            campaign_id=parent.campaign_id,
            cycle_id=parent.cycle_id,
            reason=f"superseded by fork {by.cycle_id}",
        ).recorded()
        log = self.log
        log.append(
            filing_of(
                self.stores,
                command_id=command_id,
                kind=CommandKind.PAUSE_CYCLE,
                payload=payload,
                idempotency_key=command_id,
                act_digest=act_digest,
                subject=self._course(parent),
            )
        )
        mark = run_mark(self.stores.campaigns.cycle_dir(parent))
        log.append(CommandAccepted(command_id=command_id, run_mark=mark))
