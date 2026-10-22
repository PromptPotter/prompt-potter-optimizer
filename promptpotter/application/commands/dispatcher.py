from __future__ import annotations

import importlib
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, assert_never

from promptpotter.application.commands.payloads import (
    KIND_OF_PAYLOAD,
    CommandAcceptedBody,
    DraftPayload,
    EditDraftCampaignPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.campaign import Compare, Optimize
from promptpotter.domain.command_kinds import (
    CampaignPayload,
    CommandKind,
    CommandPayload,
    CyclePayload,
    DeleteCyclePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, decode_cycle_path
from promptpotter.domain.run_records import CommandAckRecord, CommandAckStatus, CommandRecord
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
from promptpotter.shared.errors import ConflictError, NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import acting_principal, require_capability

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


def _find_idempotent_command(
    ledger: CycleEventLog, idempotency_key: str
) -> _IdempotentMatch | None:
    """Matched on the `CommandRecord` alone, a REJECTED attempt satisfies its key forever."""
    keyed: dict[str, int] = {}
    applied: _IdempotentMatch | None = None
    # The ledger's OWN offset, never a fork-chain position: the client aligns the SSE tail on it.
    for offset, record in ledger.iter():
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            keyed[record.command_id] = offset
        elif isinstance(record, CommandAckRecord) and record.status != "rejected":
            at = keyed.get(record.command_id)
            if at is not None:
                applied = _IdempotentMatch(
                    command_id=record.command_id, offset=at, ack=record.status
                )
    return applied


_HANDLER_PACKAGE = "promptpotter.application.commands"


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    """A remote caller passes the key it retries under; every other caller sends once."""

    payload: P
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def kind(self) -> CommandKind:
        return KIND_OF_PAYLOAD[type(self.payload)]


def _no_body() -> None:
    """What a deduped retry answers for a command whose applied form answers nothing either."""


@dataclass(frozen=True, slots=True)
class Applier[R]:
    """`replay=None` dedupes nothing; `ack="accepted"` leaves `run` the guard alone, the loop acking `applied`."""

    run: Callable[[], Awaitable[R]] | Callable[[], R]
    replay: Callable[[], R] | None
    effect_fn: Callable[[], dict[str, Any]] | None = None
    ack: CommandAckStatus = "applied"
    # The draft claim this command holds through its ack; a Start hands its claim to the launch.
    claim: DraftClaim | None = None

    @staticmethod
    def silent(
        run: Callable[[], Awaitable[object]] | Callable[[], object],
    ) -> Applier[object]:
        return Applier(run, _no_body)

    @staticmethod
    def for_loop(refusal: str) -> Applier[object]:
        def _guard() -> None:
            if refusal:
                raise RejectedError(refusal)

        return Applier(_guard, _no_body, ack="accepted")

    @staticmethod
    def refusing(reason: str) -> Applier[object]:
        def _refuse() -> None:
            raise RejectedError(reason)

        return Applier(_refuse, replay=None)


def refused_on_an_arm(campaign: Campaign) -> Applier[object] | None:
    match purpose := campaign.declaration.purpose:
        case Optimize():
            return None
        case Compare(arm=arm):
            return Applier.refusing(
                f"{campaign.campaign_id} is arm {arm.arm_key} of head-to-head "
                f"{arm.head_to_head_id}: its search and steer are the declaration's"
            )
        case _:
            assert_never(purpose)


@dataclass(frozen=True, slots=True)
class CommandOutcome[R]:
    accepted: CommandAcceptedBody
    result: R
    ack: CommandAckStatus


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
            self._attached = JobRegistry.attach()
        return self._attached

    def run_state(self, hop: CycleHop) -> RunState:
        return derive_run_state(self.stores.campaigns.cycle_dir(hop))

    def _applier(self, call: CommandCall[Any], *target: object) -> Applier[Any]:
        kind = call.kind
        require_capability(self.stores.identity, kind.capability, subject=f"command {kind.value!r}")
        module, _, name = kind.handler.partition(":")
        handler: Callable[..., Applier[Any]] = getattr(
            importlib.import_module(f"{_HANDLER_PACKAGE}.{module}"), name
        )
        return handler(self, call.payload, *target)

    async def dispatch(
        self, call: CommandCall[Any], *, expected_version: int | None = None
    ) -> CommandOutcome[Any]:
        """The payload's address base IS its target ledger; ``expected_version`` binds a cycle's."""
        match call.payload:
            case CyclePayload():
                return await self._on_cycle(call, expected_version=expected_version)
            case CampaignPayload():
                return await self._on_campaign(call)
            case EditDraftCampaignPayload() | ResolveOriginPayload() | StartCheckinPayload():
                return await self._on_draft(call)
            case _:
                ledger = CycleEventLog.open_workspace(self.stores.base_dir)
                return await self._record_and_apply(ledger, call, self._applier(call))

    async def _on_campaign(self, call: CommandCall[CampaignPayload]) -> CommandOutcome[object]:
        owned_campaign(self.stores, call.payload.campaign_id)
        # The WORKSPACE ledger: `archive` MOVES the campaign tree and `delete` REMOVES it.
        ledger = CycleEventLog.open_workspace(self.stores.base_dir)
        return await self._record_and_apply(ledger, call, self._applier(call))

    async def _on_cycle(
        self,
        call: CommandCall[CyclePayload],
        *,
        expected_version: int | None,
    ) -> CommandOutcome[object]:
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
            )._on_cycle(CommandCall(inner, call.idempotency_key), expected_version=expected_version)
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        cycle_dir = self.stores.campaigns.cycle_dir(hop)
        if self.stores.campaigns.load(hop, moment=None) is None:
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )

        ledger = CycleEventLog.open(CycleDir(cycle_dir))
        # Checked only when the header is present: the v0 relaxation of ADR-0001, which mandates it.
        if expected_version is not None and ledger.next_offset != expected_version:
            raise ConflictError(
                f"cycle {cycle_id} is at offset {ledger.next_offset}, "
                f"client expected {expected_version}",
                details={
                    "expected_version": expected_version,
                    "actual_version": ledger.next_offset,
                },
            )

        if isinstance(payload, DeleteCyclePayload):
            # Its apply removes the dir AND its ledger, so the record and ack go on the campaign root's.
            ledger = CycleEventLog.open(
                CycleDir(self.stores.campaigns.cycle_dir(campaign.root_hop))
            )
        return await self._record_and_apply(ledger, call, self._applier(call, campaign, hop))

    async def _on_draft(self, call: CommandCall[DraftPayload]) -> CommandOutcome[Any]:
        # Under the draft's claim, which a Start holds until its mint closed: no command lands inside
        # a mint or writes a minted draft back. Owned first, so another user's held claim says nothing.
        owned_draft_data(self.stores, call.payload.draft_id)
        claim = self.stores.drafts.claim(call.payload.draft_id)
        try:
            applier = self._applier(call, claim)
        except BaseException:
            claim.release()
            raise
        draft_dir = self.stores.drafts.draft_dir(call.payload.draft_id)
        try:
            return await self._record_and_apply(
                CycleEventLog.open(CycleDir(draft_dir)), call, applier
            )
        finally:
            if applier.claim is not None:
                applier.claim.release()

    async def _record_and_apply[P: CommandPayload, R](
        self, ledger: CycleEventLog, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R]:
        kind, idempotency_key = call.kind, call.idempotency_key
        if applier.replay is not None:
            existing = _find_idempotent_command(ledger, idempotency_key)
            if existing is not None:
                return CommandOutcome(
                    accepted=CommandAcceptedBody(command_id=existing.command_id),
                    result=applier.replay(),
                    ack=existing.ack,
                )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        applied: list[R] = []
        try:
            emit_command(
                command_id=command_id,
                kind=kind,
                payload=call.payload.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                issued_by=acting_principal(self.stores.identity),
            )
            ack_status: CommandAckStatus = applier.ack
            ack_detail = ""
            try:
                ran = applier.run()
                applied.append(await ran if inspect.isawaitable(ran) else ran)
            except RejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except Exception as exc:
                emit_command_ack(command_id=command_id, status="rejected", detail=str(exc))
                raise
            effect = (
                applier.effect_fn()
                if (applier.effect_fn is not None and ack_status != "rejected")
                else None
            )
            emit_command_ack(
                command_id=command_id, status=ack_status, detail=ack_detail, effect=effect
            )
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
        payload = PauseCyclePayload(
            campaign_id=parent.campaign_id,
            cycle_id=parent.cycle_id,
            reason=f"superseded by fork {by.cycle_id}",
        )
        emit_command(
            command_id=command_id,
            kind=CommandKind.PAUSE_CYCLE,
            payload=payload.model_dump(mode="json"),
            idempotency_key=command_id,
            issued_by=acting_principal(self.stores.identity),
        )
        emit_command_ack(command_id=command_id, status="accepted")
