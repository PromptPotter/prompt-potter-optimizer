from __future__ import annotations

import importlib
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict

from promptpotter.application.commands.payloads import (
    KIND_OF_PAYLOAD,
    CheckinPayload,
    CommandAcceptedBody,
    CommandPayload,
    CompactArchivePayload,
    CyclePayload,
    DatasetReplaced,
    DeleteCyclePayload,
    LifecyclePayload,
    PauseCyclePayload,
    ReplaceDatasetPayload,
    SetCampaignLabelPayload,
    WorkspacePayload,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, decode_cycle_path
from promptpotter.domain.run_records import CommandAckRecord, CommandAckStatus, CommandRecord
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.stores import Stores, owned_campaign, resolve_cycle_path
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
)
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    CAMPAIGN_CREATE_CAP,
    CAMPAIGN_LIFECYCLE_CAP,
    CAMPAIGN_LOOKAHEAD_CAP,
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
    acting_principal_id,
    require_capability,
)

if TYPE_CHECKING:
    from promptpotter.application.jobs.launcher.launch import Inline
    from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.phases import RunState


class RejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _IdempotentMatch(StrictModel):
    model_config = ConfigDict(frozen=True)
    command_id: str
    offset: int


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
                applied = _IdempotentMatch(command_id=record.command_id, offset=at)
    return applied


logger = logging.getLogger(__name__)

__all__ = [
    "CAP_FOR_KIND",
    "HANDLER_FOR_KIND",
    "Applier",
    "CommandCall",
    "CommandDispatcher",
    "CommandOutcome",
    "RejectedError",
    "refused_on_an_arm",
]


CAP_FOR_KIND: dict[str, str] = {
    "archive-campaign": CAMPAIGN_LIFECYCLE_CAP,
    "delete-campaign": CAMPAIGN_LIFECYCLE_CAP,
    "unarchive-campaign": CAMPAIGN_LIFECYCLE_CAP,
    "delete-cycle": CAMPAIGN_LIFECYCLE_CAP,
    "cleanup-empty-cycles": CAMPAIGN_LIFECYCLE_CAP,
    "skip-searchpoint": CAMPAIGN_STEP_CAP,
    "pause-cycle": CAMPAIGN_STEP_CAP,
    "origin-gate-decision": CAMPAIGN_STEP_CAP,
    "step-cycle": CAMPAIGN_STEP_CAP,
    # A verify SPENDS on real cells, so it sits with the verbs that buy measurement, not the step verbs.
    "verify-candidate": CAMPAIGN_RUN_CAP,
    "grade-bench": CAMPAIGN_RUN_CAP,
    "start-run": CAMPAIGN_RUN_CAP,
    "fork-cycle": CAMPAIGN_RUN_CAP,
    "start-checkin": CAMPAIGN_RUN_CAP,
    "change-run-limits": CAMPAIGN_BUDGET_CAP,
    "mint-campaign": CAMPAIGN_CREATE_CAP,
    # WHOSE launch it is, `JobRegistry.cancel_queued` checks against the principal the job was filed under.
    "cancel-queued-run": CAMPAIGN_RUN_CAP,
    # Holding the rung is not enough on the host's key: `quota.py::set_concurrent_cycles`.
    "set-concurrent-cycles": CAMPAIGN_BUDGET_CAP,
    "register-backend": CAMPAIGN_CREATE_CAP,
    "edit-draft-campaign": CAMPAIGN_CREATE_CAP,
    "resolve-origin": CAMPAIGN_CREATE_CAP,
    # The label is how every other surface addresses the campaign to a human.
    "set-campaign-label": CAMPAIGN_LIFECYCLE_CAP,
    # A dataset slug is in the measurement cache key: repointing one re-addresses every campaign on it.
    "replace-dataset": CAMPAIGN_LIFECYCLE_CAP,
    # Its purge step destroys paid spend.
    "compact-archive": CAMPAIGN_LIFECYCLE_CAP,
    "set-sample-lookahead": CAMPAIGN_LOOKAHEAD_CAP,
}

_HANDLER_PACKAGE = "promptpotter.application.commands"
HANDLER_FOR_KIND: dict[str, str] = {
    "pause-cycle": "loop_commands:pause_cycle",
    "origin-gate-decision": "loop_commands:origin_gate_decision",
    "skip-searchpoint": "loop_commands:skip_searchpoint",
    "set-sample-lookahead": "loop_commands:set_sample_lookahead",
    "start-run": "launching:start_run",
    "step-cycle": "launching:step_cycle",
    "fork-cycle": "launching:fork_cycle",
    "mint-campaign": "launching:mint_campaign",
    "start-checkin": "launching:start_checkin",
    "verify-candidate": "measuring:verify_candidate",
    "grade-bench": "measuring:grade_bench",
    "change-run-limits": "limits_and_queue:change_run_limits",
    "set-concurrent-cycles": "limits_and_queue:set_concurrent_cycles",
    "cancel-queued-run": "limits_and_queue:cancel_queued_run",
    "delete-cycle": "cycle_cleanup:delete_cycle",
    "cleanup-empty-cycles": "cycle_cleanup:cleanup_empty_cycles",
    "archive-campaign": "workspace_edits:campaign_lifecycle",
    "unarchive-campaign": "workspace_edits:campaign_lifecycle",
    "delete-campaign": "workspace_edits:campaign_lifecycle",
    "set-campaign-label": "workspace_edits:set_campaign_label",
    "register-backend": "workspace_edits:register_backend",
    "replace-dataset": "workspace_edits:replace_dataset",
    "compact-archive": "archive_compaction:compact_archive",
    "edit-draft-campaign": "draft_editing:edit_draft_campaign",
    "resolve-origin": "origin_resolving:resolve_origin",
}

for _table_name, _table in (("CAP_FOR_KIND", CAP_FOR_KIND), ("HANDLER_FOR_KIND", HANDLER_FOR_KIND)):
    if set(_table) != ALL_DISPATCHED_KINDS:
        raise RuntimeError(
            f"{_table_name} out of sync with the dispatched command set: "
            f"{ALL_DISPATCHED_KINDS.symmetric_difference(_table)}"
        )


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    payload: P
    idempotency_key: str

    @property
    def kind(self) -> str:
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
    if campaign.arm is None:
        return None
    return Applier.refusing(
        f"{campaign.campaign_id} is arm {campaign.arm.arm_key} of head-to-head "
        f"{campaign.arm.head_to_head_id}: its search and steer are the declaration's"
    )


@dataclass(frozen=True, slots=True)
class CommandOutcome[R]:
    accepted: CommandAcceptedBody
    result: R


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
        module, _, name = HANDLER_FOR_KIND[call.kind].partition(":")
        handler: Callable[..., Applier[Any]] = getattr(
            importlib.import_module(f"{_HANDLER_PACKAGE}.{module}"), name
        )
        return handler(self, call.payload, *target)

    async def dispatch_campaign_command(
        self, call: CommandCall[LifecyclePayload | SetCampaignLabelPayload]
    ) -> CommandOutcome[object]:
        owned_campaign(self.stores, call.payload.campaign_id)
        # The WORKSPACE ledger: `archive` MOVES the campaign tree and `delete` REMOVES it.
        ledger = CycleEventLog.open_workspace(self.stores.base_dir)
        return await self._record_and_apply(ledger, call, self._applier(call))

    async def dispatch_cycle_command(
        self,
        call: CommandCall[CyclePayload],
        *,
        expected_version: int | None,
    ) -> CommandOutcome[object]:
        payload = call.payload
        hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
        try:
            tail = decode_cycle_path(payload.descend or "")
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        if tail and payload.inner_refusal is not None:
            raise PayloadInvalidError(payload.inner_refusal, code="inner_cycle_unaddressed")
        leaf_stores, leaf = resolve_cycle_path(self.stores, (hop, *tail))
        if tail:
            inner = payload.model_copy(
                update={"campaign_id": leaf.campaign_id, "cycle_id": leaf.cycle_id, "descend": None}
            )
            return await CommandDispatcher(
                leaf_stores, job_registry=self._attached, inline=self.inline
            ).dispatch_cycle_command(
                CommandCall(inner, call.idempotency_key), expected_version=expected_version
            )
        campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
        campaign = owned_campaign(self.stores, campaign_id)
        cycle_dir = self.stores.campaigns.cycle_dir(hop)
        if self.stores.campaigns.load(hop) is None:
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

    async def dispatch_replace_dataset(
        self, call: CommandCall[ReplaceDatasetPayload]
    ) -> CommandOutcome[DatasetReplaced]:
        return cast(CommandOutcome[DatasetReplaced], await self._dispatch_on_workspace(call))

    async def dispatch_compact_archive(
        self, call: CommandCall[CompactArchivePayload]
    ) -> CommandOutcome[ArchiveReport]:
        return cast("CommandOutcome[ArchiveReport]", await self._dispatch_on_workspace(call))

    async def dispatch_workspace_command(
        self, call: CommandCall[WorkspacePayload]
    ) -> CommandOutcome[object]:
        return await self._dispatch_on_workspace(call)

    async def _dispatch_on_workspace(self, call: CommandCall[Any]) -> CommandOutcome[Any]:
        ledger = CycleEventLog.open_workspace(self.stores.base_dir)
        return await self._record_and_apply(ledger, call, self._applier(call))

    async def dispatch_checkin_command[P: CheckinPayload](
        self, call: CommandCall[P]
    ) -> CommandOutcome[Any]:
        # Asked FIRST: a draft that is gone or a patch it refuses is answered before the campaign is.
        applier = self._applier(call)
        campaign_id = call.payload.checkin_campaign_id
        campaign = owned_campaign(self.stores, campaign_id)
        cycle_dir = self.stores.campaigns.cycle_dir(campaign.root_hop)
        if self.stores.campaigns.load(campaign.root_hop) is None:
            raise NotFoundError(
                f"check-in cycle not found: {campaign_id}/{campaign.root_cycle_id}",
                code="command_target_not_found",
            )
        return await self._record_and_apply(CycleEventLog.open(CycleDir(cycle_dir)), call, applier)

    async def _record_and_apply[P: CommandPayload, R](
        self, ledger: CycleEventLog, call: CommandCall[P], applier: Applier[R]
    ) -> CommandOutcome[R]:
        kind, idempotency_key = call.kind, call.idempotency_key
        self._require_capability_for(kind)
        if applier.replay is not None:
            existing = _find_idempotent_command(ledger, idempotency_key)
            if existing is not None:
                return CommandOutcome(
                    accepted=CommandAcceptedBody(
                        command_id=existing.command_id,
                        correlation_id=idempotency_key,
                        ledger_sequence=existing.offset,
                    ),
                    result=applier.replay(),
                )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        applied: list[R] = []
        try:
            offset = emit_command(
                command_id=command_id,
                kind=kind,
                payload=call.payload.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                issued_by_user_id=acting_principal_id(self.stores.identity),
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
            accepted=CommandAcceptedBody(
                command_id=command_id,
                correlation_id=idempotency_key,
                ledger_sequence=offset if offset is not None else 0,
            ),
            result=applied[0],
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
            kind=KIND_OF_PAYLOAD[PauseCyclePayload],
            payload=payload.model_dump(mode="json"),
            idempotency_key=command_id,
            issued_by_user_id=acting_principal_id(self.stores.identity),
        )
        emit_command_ack(command_id=command_id, status="accepted")

    def _require_capability_for(self, kind: str) -> None:
        cap = CAP_FOR_KIND.get(kind)
        if cap is None:
            logger.warning("command %r has no capability and is unwritable", kind)
            raise NotFoundError("Not found", code="not_found")
        require_capability(self.stores.identity, cap, subject=f"command {kind!r}")
