"""Sole writer of ``CommandRecord``. One order, always: validate, dedupe by ``Idempotency-Key``,
append the record, apply inline, append the ``CommandAckRecord``."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, assert_never

from pydantic import ConfigDict, ValidationError

from promptpotter.application.commands.payloads import (
    KIND_OF_PAYLOAD,
    ArchiveCampaignPayload,
    CampaignPayload,
    CancelQueuedRunPayload,
    ChangeSpendBudgetPayload,
    CheckinPayload,
    CleanupEmptyCyclesPayload,
    CommandAcceptedBody,
    CommandPayload,
    CompactArchivePayload,
    CyclePayload,
    DeleteCampaignPayload,
    DeleteCyclePayload,
    ForkCyclePayload,
    LifecyclePayload,
    MintCampaignPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    RegisterBackendPayload,
    ReplaceDatasetPayload,
    SetCampaignLabelPayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    StartRunPayload,
    StepCyclePayload,
    UnarchiveCampaignPayload,
    VerifyCandidatePayload,
)
from promptpotter.application.datasets.dataset_replace import (
    NothingToReplaceError,
    version_and_repoint,
)
from promptpotter.application.diagnostics.verify import verify_candidate
from promptpotter.application.jobs.launcher.admission import launch
from promptpotter.application.jobs.launcher.mint_and_start import (
    mint_campaign_command,
    start_run_command,
)
from promptpotter.application.jobs.quota import clamp_budget_change, hold_ceiling
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import (
    ArchiveReport,
    compact_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    cleanup_stub_fork_if_empty,
    mint_operator_fork,
)
from promptpotter.application.runner.origin_gate import GateDecision, submit_gate_decision
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.domain.pipeline_overlay import (
    overlay_sets_model_outside_allowed,
    permitted_models_from_narrowing,
)
from promptpotter.domain.results import parse_candidate_label
from promptpotter.domain.run_records import CommandAckRecord, CommandRecord, CycleSeed
from promptpotter.domain.spend import BudgetChange
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.runtime_flags import write_sample_lookahead
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandboxes_dir,
    root_cycle_id,
)
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
    ServiceUnavailableError,
)
from promptpotter.shared.identity import (
    CAMPAIGN_BABYSIT_CAP,
    CAMPAIGN_BUDGET_CAP,
    CAMPAIGN_CREATE_CAP,
    CAMPAIGN_LIFECYCLE_CAP,
    CAMPAIGN_LOOKAHEAD_CAP,
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
    acting_principal_id,
    has_capability,
    require_capability,
)


class _DeleteCycleRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _IdempotentMatch(StrictModel):
    model_config = ConfigDict(frozen=True)
    command_id: str
    offset: int


def _parse_cycle_seed(raw: object) -> CycleSeed:
    """Stamps the C0 lineage provenance ``origin_source="fork_seed"``: every operator fork
    carries a seed, and the wire schema ``OperatorForkOverride`` does not carry the tag."""
    if not isinstance(raw, dict):
        raise PayloadInvalidError("payload.seed (object) is required.")
    try:
        return CycleSeed.model_validate({**raw, "origin_source": "fork_seed"})
    except ValidationError as exc:
        raise PayloadInvalidError(f"payload.seed invalid: {exc}") from exc


def _slugify_backend_id(name: str) -> str:
    """Mirrors the auto-derivation ``RegisterBackendPayload.id`` documents — "auto-derived
    from `name` when omitted"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def _find_idempotent_command(
    ledger: CycleEventLog, idempotency_key: str
) -> _IdempotentMatch | None:
    """Only an APPLIED command replays. On the ``CommandRecord`` alone a REJECTED attempt satisfies
    its key forever, so the 429 at the account ceiling burns the very retry the client is told to
    make and answers it 200 off a body nothing wrote. A match whose ack never landed (the process
    died mid-apply) is not a replay either.

    O(n) over the cycle ledger: a ledger holds thousands of records, not millions, and
    commands are operator-paced.

    The offset is the ledger's own — ``iter()`` yields it now. Recovered with ``enumerate`` it was
    a VIRTUAL position over the fork chain, and that number went out to clients as
    ``ledger_sequence``, whose contract is "the offset at which the `CommandRecord` was appended"
    and whose stated use is aligning the SSE tail. The fresh-append branch beside it has always
    returned the real one, so one field carried two different numbers."""
    keyed: dict[str, int] = {}
    applied: _IdempotentMatch | None = None
    for offset, record in ledger.iter():
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            keyed[record.command_id] = offset
        elif isinstance(record, CommandAckRecord) and record.status == "applied":
            at = keyed.get(record.command_id)
            if at is not None:
                applied = _IdempotentMatch(command_id=record.command_id, offset=at)
    return applied


logger = logging.getLogger(__name__)

__all__ = ["CAP_FOR_KIND", "Applier", "CommandCall", "CommandDispatcher", "CommandOutcome"]


# The one cap→verb ladder (ADR-0005 §3): every command kind that funnels through
# `_record_and_apply` requires exactly one capability, checked at that single
# seam. A tenant owner holds every one (OWNER_COMMAND_CAPABILITIES); a delegated
# sub-principal an attenuated subset. `fork-cycle` sits at `campaign.run` — the babysit
# grant that gates unlocking a LOCKED axis in the seed is a distinct slice.
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
    # A verify SPENDS — it scores real cells against the backend — so it sits with the verbs that
    # buy measurement, not with the step verbs that only move a cycle already paid for.
    "verify-candidate": CAMPAIGN_RUN_CAP,
    "start-run": CAMPAIGN_RUN_CAP,
    "fork-cycle": CAMPAIGN_RUN_CAP,
    "start-checkin": CAMPAIGN_RUN_CAP,
    "change-spend-budget": CAMPAIGN_BUDGET_CAP,
    "mint-campaign": CAMPAIGN_CREATE_CAP,
    # Leaving the queue is the same authority as joining it — and the OWNER check is stricter
    # still, enforced in `JobRegistry.cancel_queued`, so a delegate holding `campaign.run`
    # cannot withdraw somebody else's launch.
    "cancel-queued-run": CAMPAIGN_RUN_CAP,
    "register-backend": CAMPAIGN_CREATE_CAP,
    "edit-draft-campaign": CAMPAIGN_CREATE_CAP,
    "resolve-origin": CAMPAIGN_CREATE_CAP,
    # Renaming is how every OTHER surface addresses the campaign to a human, so it sits
    # with the verbs that decide the campaign's existence rather than with the run capabilities.
    "set-campaign-label": CAMPAIGN_LIFECYCLE_CAP,
    # A dataset slug is part of the measurement cache key, so repointing one re-addresses
    # every campaign that already measured against it — stronger authority than creating a
    # dataset, which is why it sits at `campaign.lifecycle` rather than beside `mint-campaign`.
    "replace-dataset": CAMPAIGN_LIFECYCLE_CAP,
    # Rewrites rows every campaign measured against, and its purge step destroys paid spend
    # outright — the same authority `replace-dataset` sits at, for the same reason.
    "compact-archive": CAMPAIGN_LIFECYCLE_CAP,
    # Its own capability rather than a share of babysit: look-ahead spends the box's shared provider
    # rate bucket, which is the one thing a multi-tenant host may want to withhold from a
    # delegate, and it steers no measurement (the overshoot sample is discarded).
    "set-sample-lookahead": CAMPAIGN_LOOKAHEAD_CAP,
}

# Import-time exhaustiveness — a dispatched kind with no cap is a silent unguarded verb.
if set(CAP_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "CAP_FOR_KIND out of sync with the dispatched command set: "
        f"{ALL_DISPATCHED_KINDS.symmetric_difference(CAP_FOR_KIND)}"
    )


@dataclass(frozen=True, slots=True)
class CommandCall[P: CommandPayload]:
    payload: P
    idempotency_key: str

    @property
    def kind(self) -> str:
        return KIND_OF_PAYLOAD[type(self.payload)]


@dataclass(frozen=True, slots=True)
class Applier:
    """How one command applies and answers for itself. A deduped retry answers ``on_replay``, never
    ``run``; ``dedupe=False`` leaves retries to the domain's guard; ``effect_fn`` lands on the ack."""

    run: Callable[[], Awaitable[Any]] | Callable[[], Any]
    on_replay: Callable[[], Any] | None = None
    dedupe: bool = True
    effect_fn: Callable[[], dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    accepted: CommandAcceptedBody
    result: Any = None


class CommandDispatcher:
    """One per request, carrying the request-scoped ``Stores``. ``job_registry`` is the
    process-wide singleton stashed on ``app.state.job_registry`` at startup."""

    def __init__(self, stores: Stores, job_registry: JobRegistry | None = None) -> None:
        self._stores = stores
        self._job_registry = job_registry

    # ------------------------------------------------------------------
    # Lifecycle (campaign-scoped, workspace-style)
    # ------------------------------------------------------------------
    async def dispatch_lifecycle(self, call: CommandCall[LifecyclePayload]) -> CommandOutcome:
        """The ``CommandRecord`` lands on the WORKSPACE ledger because ``archive`` MOVES the
        campaign tree and ``delete`` REMOVES it — its own ledger cannot be the audit home."""
        self._load_owned_campaign(call.payload.campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger, call, Applier(lambda: self._apply_lifecycle(call.payload))
        )

    async def dispatch_campaign_config(self, call: CommandCall[CampaignPayload]) -> CommandOutcome:
        """An in-place edit of ``campaign.json`` — the campaign persists, so the record is an
        ordinary workspace-ledger admin edit rather than the lifecycle move beside it."""
        self._load_owned_campaign(call.payload.campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger, call, self._build_campaign_config_applier(call.payload)
        )

    def _build_campaign_config_applier(self, payload: CampaignPayload) -> Applier:
        cid = payload.campaign_id
        if isinstance(payload, SetCampaignLabelPayload):
            label = payload.label
            return Applier(lambda: self._apply_set_campaign_label(cid, label))
        raise PayloadInvalidError(  # pragma: no cover — the registry pairs every kind with a type
            f"no applier wired for campaign-config payload {type(payload).__name__}"
        )

    def _apply_set_campaign_label(self, campaign_id: str, label: str) -> dict[str, Any]:
        """The operator's name for the campaign — what ``campaignDisplayName`` prefers over the
        dataset name, and empty restores that fallback. Identity-neutral: ``label`` is not in
        ``root_content_hash``, so a rename cannot void a banked origin."""
        self._stores.campaigns.update_campaign(campaign_id, {"label": label})
        return {"campaign_id": campaign_id, "label": label}

    # ------------------------------------------------------------------
    # Cycle-scoped (migrated sanctioned POSTs)
    # ------------------------------------------------------------------
    async def dispatch_cycle_command(
        self, call: CommandCall[CyclePayload], *, expected_version: int | None
    ) -> CommandOutcome:
        """The payload arrives TYPED, which is the whole validation — the CLI and the API build the
        same model, so neither entry point can validate a field the other spells differently.

        ``Expected-Version`` is checked only when the header is present — the v0 relaxation of
        ADR-0001, which mandates it."""
        campaign_id, cycle_id = call.payload.campaign_id, call.payload.cycle_id
        campaign = self._load_owned_campaign(campaign_id)
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        cycle_dir = self._stores.campaigns.cycle_dir(hop)
        if not CycleLayout(cycle_dir).manifest.is_file():
            raise NotFoundError(
                f"cycle not found: {campaign_id}/{cycle_id}", code="command_target_not_found"
            )

        ledger = CycleEventLog.open(CycleDir(cycle_dir))
        if expected_version is not None and ledger.next_offset != expected_version:
            raise ConflictError(
                f"cycle {cycle_id} is at offset {ledger.next_offset}, "
                f"client expected {expected_version}",
                details={
                    "expected_version": expected_version,
                    "actual_version": ledger.next_offset,
                },
            )

        # delete-cycle's apply removes the dir AND its ledger, so the record + ack must be
        # built BEFORE it disappears — pre-emitted on the parent's root ledger.
        if isinstance(call.payload, DeleteCyclePayload):
            return await self._dispatch_delete_cycle(campaign=campaign, hop=hop, call=call)

        return await self._record_and_apply(
            ledger, call, self._build_cycle_applier(campaign, hop, call.payload)
        )

    async def _dispatch_delete_cycle(
        self,
        *,
        campaign: Campaign,
        hop: CycleHop,
        call: CommandCall[CyclePayload],
    ) -> CommandOutcome:
        """Liveness, not activeness — and gated HERE rather than in the store, because the runner
        calls the same helper as the OWNER, inside ``RUN_FRESH_S`` of its own index write."""
        if hop.cycle_id in self._stores.campaigns.live_cycle_ids(hop.campaign_id):
            raise ConflictError(
                f"refusing to delete {hop.cycle_id}: it has a live producer — pause or stop it first"
            )

        index = self._stores.campaigns.load(hop) or {}
        parent_cycle_id = str(index.get("parent_cycle_id") or campaign.root_cycle_id)

        root_dir = self._stores.campaigns.cycle_dir(campaign.root_hop)
        root_ledger = CycleEventLog.open(CycleDir(root_dir))

        def _apply() -> None:
            deleted, reason = cleanup_stub_fork_if_empty(
                campaign_store=self._stores.campaigns,
                hop=hop,
                parent_cycle_id=parent_cycle_id,
            )
            if not deleted:
                raise _DeleteCycleRejectedError(reason)

        return await self._record_and_apply(root_ledger, call, Applier(_apply))

    # ------------------------------------------------------------------
    # Workspace-scoped (no cycle target — backend registry mutations)
    # ------------------------------------------------------------------
    async def dispatch_workspace_command(self, call: CommandCall[CommandPayload]) -> CommandOutcome:
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        payload = call.payload
        applier: Applier
        if isinstance(payload, RegisterBackendPayload):
            backend = payload
            applier = Applier(lambda: self._apply_register_backend(backend))
        elif isinstance(payload, ReplaceDatasetPayload):
            slug = payload.slug
            # A deduped retry must not re-run the migration — it would version the slug a second
            # time — and the body echoes the subject, so it replays without touching disk.
            applier = Applier(
                lambda: self._apply_replace_dataset(slug), on_replay=lambda: {"slug": slug}
            )
        elif isinstance(payload, CompactArchivePayload):
            job = payload
            # A deduped retry must not re-run the pass: `purge-cold` would report a second deletion
            # of bytes already gone. It replays an EMPTY report — the same model the applier
            # answers with, because the route validates this body too, and a bespoke
            # `{"replayed": true}` shape 500s the retry that an Idempotency-Key exists to make safe.
            # All-zero is also the true answer: this attempt moved nothing.
            applier = Applier(
                lambda: self._apply_compact_archive(job),
                on_replay=lambda: ArchiveReport().model_dump(mode="json"),
            )
        elif isinstance(payload, CancelQueuedRunPayload):
            job_id = payload.job_id
            applier = Applier(lambda: self._apply_cancel_queued_run(job_id))
        elif isinstance(payload, MintCampaignPayload):
            mint = payload

            async def _mint() -> None:
                await self._apply_mint_campaign(mint)

            applier = Applier(_mint)
        else:  # pragma: no cover — the registry pairs every kind with a type
            raise PayloadInvalidError(
                f"no applier wired for workspace payload {type(payload).__name__}"
            )

        return await self._record_and_apply(ledger, call, applier)

    # ------------------------------------------------------------------
    # Check-in scoped (origin authoring — the draft-mutating commands)
    # ------------------------------------------------------------------
    async def dispatch_checkin_command[P: CheckinPayload](
        self, call: CommandCall[P], applier: Applier
    ) -> CommandOutcome:
        """The one family whose ``Applier`` is authored outside this class (``checkin_dispatch.py``).
        ``start-checkin`` alone sets ``dedupe=False`` — its ``job_id`` has no disk home."""
        campaign_id = call.payload.checkin_campaign_id
        campaign = self._load_owned_campaign(campaign_id)
        cycle_dir = self._stores.campaigns.cycle_dir(campaign.root_hop)
        if not CycleLayout(cycle_dir).manifest.is_file():
            raise NotFoundError(
                f"check-in cycle not found: {campaign_id}/{campaign.root_cycle_id}",
                code="command_target_not_found",
            )
        return await self._record_and_apply(CycleEventLog.open(CycleDir(cycle_dir)), call, applier)

    # ------------------------------------------------------------------
    # Shared record / apply / ack pipeline
    # ------------------------------------------------------------------
    async def _record_and_apply[P: CommandPayload](
        self, ledger: CycleEventLog, call: CommandCall[P], applier: Applier
    ) -> CommandOutcome:
        kind, idempotency_key = call.kind, call.idempotency_key
        self._require_capability_for(kind)
        if applier.dedupe:
            existing = _find_idempotent_command(ledger, idempotency_key)
            if existing is not None:
                return CommandOutcome(
                    accepted=CommandAcceptedBody(
                        command_id=existing.command_id,
                        correlation_id=idempotency_key,
                        ledger_sequence=existing.offset,
                    ),
                    result=applier.on_replay() if applier.on_replay is not None else None,
                )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        applied_value: Any = None
        try:
            offset = emit_command(
                command_id=command_id,
                kind=kind,
                payload=call.payload.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                issued_by_user_id=acting_principal_id(self._stores.identity),
            )
            ack_status: Literal["applied", "rejected"] = "applied"
            ack_detail = ""
            try:
                result = applier.run()
                if asyncio.iscoroutine(result):
                    result = await result
                applied_value = result
            except _DeleteCycleRejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except Exception as exc:
                # The rejected ack lands whatever the error, then it propagates: `main.py` maps a
                # `PotterError` to its own status and anything else to a logged 500.
                emit_command_ack(command_id=command_id, status="rejected", detail=str(exc))
                raise
            effect = (
                applier.effect_fn()
                if (applier.effect_fn is not None and ack_status == "applied")
                else None
            )
            emit_command_ack(
                command_id=command_id, status=ack_status, detail=ack_detail, effect=effect
            )
        finally:
            reset_cycle_ledger(token)

        if ack_status == "rejected":
            # Rejected by a domain guard: 409 with the guard's reason, while the audit trail
            # stays on the ledger.
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
            result=applied_value,
        )

    # ------------------------------------------------------------------
    # Per-kind appliers
    # ------------------------------------------------------------------
    def _build_cycle_applier(
        self,
        campaign: Campaign,
        hop: CycleHop,
        payload: CyclePayload,
    ) -> Applier:
        """Dispatched on the payload's TYPE, not on ``kind`` — every cycle-scoped verb owns one
        model, so the branch that reads a field is the branch its type reached. Nothing here
        re-validates: the model is the only validation, and a second lenient pass over an
        already-recorded payload can only disagree with the record."""
        # Every launch this dispatcher starts runs the campaign's own dataset; the queue entry has
        # to name it, and this is the one place the manifest is already open.
        dataset_name = campaign.dataset_name if campaign else ""
        if isinstance(payload, VerifyCandidatePayload):

            async def _apply_verify() -> None:
                # The one application function the CLI also calls, so both raise the same record.
                cand_round, cand_idx = parse_candidate_label(payload.label)
                await verify_candidate(
                    stores=self._stores,
                    identity=self._stores.identity,
                    hop=hop,
                    round_num=cand_round,
                    cand_idx=cand_idx,
                    label=payload.label,
                    samples=payload.samples,
                    seed=None,
                )

            return Applier(_apply_verify)
        if isinstance(payload, ForkCyclePayload):
            seed = _parse_cycle_seed(payload.seed)
            # Steering the model OUTSIDE what the node permits (nothing declared = nothing
            # sanctioned) is the ADR-0005 §4 babysit action, a distinct cap above the
            # `campaign.run` fork. A PERMITTED steer is a clean human fork.
            permitted = permitted_models_from_narrowing(
                campaign.config.get("optimizer_narrowing") if campaign else None
            )
            steers_disallowed_model = seed is not None and overlay_sets_model_outside_allowed(
                seed.pipeline_overlay, permitted
            )
            if steers_disallowed_model and not has_capability(
                self._stores.identity, CAMPAIGN_BABYSIT_CAP
            ):
                logger.warning(
                    "fork-cycle disallowed-model steer denied for principal %s (missing %s)",
                    acting_principal_id(self._stores.identity),
                    CAMPAIGN_BABYSIT_CAP,
                )
                raise NotFoundError("Not found", code="not_found")

            async def _apply_fork() -> None:
                # Mint THEN launch: minting alone is disk I/O, and the fork would sit
                # seeded-but-idle awaiting a CLI `resume` that never comes from the web. Declare
                # no limits — the seed's reconciled ones govern at the runner seam.
                new_cycle_id = mint_operator_fork(
                    stores=self._stores,
                    hop=hop,
                    from_round=payload.round,
                    from_candidate_id=payload.candidate_id,
                    seed=seed,
                    steered_by=payload.steered_by,
                    keep_rounds=payload.keep_rounds,
                )
                try:
                    await self._apply_start_run(
                        hop=CycleHop(campaign_id=hop.campaign_id, cycle_id=new_cycle_id),
                        kind="resume",
                        dataset_name=dataset_name,
                        limits=LaunchLimits(),
                    )
                except BaseException:
                    # ONE act, landing whole or not at all: a launch refused after the mint (full
                    # machine, empty wallet, dark backend) leaves no seeded fork that never starts.
                    # `_apply_start_run` raises only from BEFORE its background task exists, so the
                    # stub is provably idle and the shared cleanup's own emptiness test is the
                    # backstop.
                    self._cleanup_failed_fork(hop, new_cycle_id)
                    raise

            return Applier(_apply_fork)
        if isinstance(payload, StepCyclePayload):
            # Advance N rounds in place then auto-pause, on the resume machinery + RunMode's
            # run-scoped stop — the `campaign.step` capability for a delegate without run.
            steps = payload.rounds
            return Applier(
                lambda: self._apply_start_run(
                    hop=hop,
                    kind="resume",
                    dataset_name=dataset_name,
                    limits=LaunchLimits(),
                    stop_after_rounds=steps,
                )
            )
        if isinstance(payload, SkipSearchpointPayload):
            return Applier(lambda: self._apply_skip_searchpoint(hop))
        if isinstance(payload, CleanupEmptyCyclesPayload):
            return Applier(lambda: self._apply_cleanup_empty(hop))
        if isinstance(payload, PauseCyclePayload):
            return Applier(lambda: self._apply_pause_cycle(hop))
        if isinstance(payload, SetSampleLookaheadPayload):
            cells = payload.cells
            return Applier(lambda: self._apply_set_sample_lookahead(hop, cells=cells))
        if isinstance(payload, OriginGateDecisionPayload):
            decision = payload.decision
            return Applier(lambda: self._apply_origin_gate_decision(hop, decision))
        if isinstance(payload, ChangeSpendBudgetPayload):
            change = BudgetChange(payload.max_usd, payload.max_tokens)
            return Applier(lambda: self._apply_change_spend_budget(hop, change))
        if isinstance(payload, StartRunPayload):
            run = payload

            async def _apply() -> None:
                await self._apply_start_run(
                    hop=hop, kind=run.kind, dataset_name=dataset_name, limits=run
                )

            return Applier(_apply)
        raise PayloadInvalidError(  # pragma: no cover — the registry pairs every kind with a type
            f"no applier wired for cycle-scoped payload {type(payload).__name__}"
        )

    def _apply_lifecycle(self, payload: LifecyclePayload) -> None:
        changed_at = utcnow_iso()
        campaigns = self._stores.campaigns
        campaign_id, reason = payload.campaign_id, payload.reason
        if isinstance(payload, ArchiveCampaignPayload):
            campaigns.archive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif isinstance(payload, UnarchiveCampaignPayload):
            campaigns.unarchive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif isinstance(payload, DeleteCampaignPayload):
            # destructive (keepsake spared only with keep_results)
            campaigns.delete_campaign(
                campaign_id,
                keep_results=payload.keep_results,
                changed_at=changed_at,
                reason=reason,
                inner_sandbox_root=inner_sandboxes_dir(self._stores.shared_root),
            )
        else:
            assert_never(payload)

    def _apply_replace_dataset(self, slug: str) -> dict[str, str]:
        try:
            result = version_and_repoint(stores=self._stores, slug=slug)
        except NothingToReplaceError as exc:
            raise ConflictError(
                str(exc), code="nothing_to_replace", details={"slug": exc.slug}
            ) from exc
        return {"slug": result.slug}

    def _apply_compact_archive(self, payload: CompactArchivePayload) -> dict[str, Any]:
        """Three modes, one application-layer function each — this arm only picks and reports.

        A refusal is an OUTCOME, not an exception: ``archive_writers`` is on the response either
        way, so a client learns "a cycle is still appending" from the same shape as a success
        rather than from an error it has to special-case."""
        run = {
            "compact": compact_measurement_archive,
            "restore": restore_measurement_archive,
            "purge-cold": purge_cold_store,
        }[payload.mode]
        report = run(self._stores, dataset=payload.dataset, apply=payload.apply)
        return report.model_dump(mode="json")

    def _apply_register_backend(self, payload: RegisterBackendPayload) -> None:
        backend_id = payload.id or _slugify_backend_id(payload.name)
        if self._stores.backends.get(backend_id) is not None:
            raise ConflictError(
                f"Backend '{backend_id}' already exists", details={"backend_id": backend_id}
            )
        self._stores.backends.register(
            BackendConnection(
                id=backend_id,
                name=payload.name,
                backend_type=payload.backend_type,
                base_url=payload.base_url.rstrip("/"),
            )
        )

    def _apply_skip_searchpoint(self, hop: CycleHop) -> None:
        """``Session.skip_check`` consumes the flag at the next per-sample checkpoint and the cycle
        keeps running, marked ``human_intervened`` — no longer purely reproducible."""
        flag = CycleLayout(self._stores.campaigns.cycle_dir(hop)).skip_flag
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")
        self._stores.campaigns.mark_human_intervened(hop, kind="skip", at=utcnow_iso())

    def _apply_pause_cycle(self, hop: CycleHop) -> None:
        flag = CycleLayout(self._stores.campaigns.cycle_dir(hop)).pause_flag
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")

    def _apply_set_sample_lookahead(self, hop: CycleHop, *, cells: int) -> None:
        """Arm the walk to hold ``cells`` samples in flight; ``1`` disarms. Recorded UNCLAMPED —
        the walk clamps to the connector's ceiling, and clamping twice lets the two disagree.
        Pointedly does NOT ``mark_human_intervened`` as its neighbour above does — skip changes what
        was measured, this cannot, and a babysat stamp would assert a steer that did not happen."""
        write_sample_lookahead(self._stores.campaigns.cycle_dir(hop), cells)

    def _apply_origin_gate_decision(self, hop: CycleHop, decision: GateDecision) -> None:
        """The browser's half of the gate. The write itself is
        ``runner/origin_gate.py::submit_gate_decision`` — one writer, so an embedded host answers
        the gate through the same file this does rather than needing an HTTP client."""
        submit_gate_decision(self._stores.campaigns.cycle_dir(hop), decision)

    def _clamp_to_account_ceilings(
        self, hop: CycleHop, job_registry: JobRegistry, change: BudgetChange
    ) -> BudgetChange:
        """Only a SUPPLIED arm is clamped — composing an absent one would write a ceiling the
        caller asked to leave alone."""
        user = self._stores.users.get_or_create(
            user_id=str(self._stores.identity.user_id),
            tenant_id=str(self._stores.identity.tenant_id),
        )
        return clamp_budget_change(
            requested=change,
            user=user,
            stores=self._stores,
            job_registry=job_registry,
            hop=hop,
        )

    async def _apply_change_spend_budget(self, hop: CycleHop, change: BudgetChange) -> None:
        """The round loop's BudgetGate re-reads the moved ceiling every clean round. A ``None`` arm
        leaves that ceiling untouched; ``0`` halts at the next round boundary. Both arms compose
        against the account first, because ``entry.py::_usd_cap`` prefers this file over the cap the
        launch composed — unclamped, raising one here is the way around the host-wallet gate."""
        registry = self._job_registry
        if registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        clamped = await asyncio.to_thread(self._clamp_to_account_ceilings, hop, registry, change)
        hold_ceiling(
            job_registry=registry,
            hop=hop,
            cycle_dir=self._stores.campaigns.cycle_dir(hop),
            change=clamped,
        )

    async def _apply_mint_campaign(self, payload: MintCampaignPayload) -> None:
        """The 202 returns once the manifest + root cycle index are written — or, when the box is
        full, the moment the launch takes its place in line and the mint moves behind the wait. The
        webapp discovers the new ids by polling ``/api/v1/active`` either way."""

        registry = self._require_job_registry()
        # Campaign-from-origin rides the check-in path, not this workspace verb, so there is no
        # origin_override here. Its PotterErrors map centrally in `_record_and_apply`.
        await launch(
            stores=self._stores,
            job_registry=registry,
            dataset_name=payload.dataset_name,
            run=lambda job: mint_campaign_command(
                stores=self._stores,
                dataset_name=payload.dataset_name,
                job_registry=registry,
                job=job,
                limits=payload,
            ),
        )

    async def _apply_start_run(
        self,
        *,
        hop: CycleHop,
        kind: str,
        dataset_name: str,
        limits: LaunchLimits,
        stop_after_rounds: int | None = None,
    ) -> None:
        """``stop_after_rounds`` bounds the run in place — the ``step-round`` verb's mechanism.

        ``dataset_name`` comes from the campaign the dispatcher already loaded: the queue entry has
        to name what it will run from the moment it joins, and re-reading the manifest here would
        be a second answer to a question one caller up already has."""

        registry = self._require_job_registry()
        # Quota / Launch / BackendUnreachable are PotterErrors mapped centrally
        # by _record_and_apply — no per-applier arm here.
        await launch(
            stores=self._stores,
            job_registry=registry,
            dataset_name=dataset_name,
            hop=hop,
            # The launch-rate and daily-campaign arms bound a STRANGER spending the host's key;
            # starting a campaign that already exists is not a second campaign.
            rate_limited=False,
            run=lambda job: start_run_command(
                stores=self._stores,
                job_registry=registry,
                job=job,
                hop=hop,
                kind=kind,
                limits=limits,
                stop_after_rounds=stop_after_rounds,
            ),
        )

    def _apply_cancel_queued_run(self, job_id: str) -> None:
        """Withdraw a launch that is still waiting for a slot. A queue with no way out is a trap:
        `pause-cycle` cannot serve one, because a queued mint has no cycle to write a flag into.

        Whose launch it is decides, not which capability the caller holds — ``cancel_queued``
        refuses anyone else's, and an already-started or already-gone job answers 404 rather than
        silently doing nothing. Cancelling a queued FORK leaves its stub behind for
        ``cleanup-empty-cycles``; the fork was minted before it queued and deleting a cycle is that
        verb's authority, not this one's."""
        registry = self._require_job_registry()
        if not registry.cancel_queued(job_id, user_id=str(self._stores.identity.user_id)):
            raise NotFoundError("Not found", code="not_found")

    def _require_job_registry(self) -> JobRegistry:
        """The registry every launch verb needs, or the 503 that says why not."""
        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        return self._job_registry

    def _cleanup_failed_fork(self, parent_hop: CycleHop, new_cycle_id: str) -> None:
        """Undo a fork whose launch never started. Best-effort and never masks the launch failure —
        the operator has to be told why the fork was refused, not why the tidy-up went wrong."""

        try:
            deleted, reason = cleanup_stub_fork_if_empty(
                campaign_store=self._stores.campaigns,
                hop=CycleHop(campaign_id=parent_hop.campaign_id, cycle_id=new_cycle_id),
                parent_cycle_id=parent_hop.cycle_id,
            )
        except Exception:
            logger.exception(
                "fork %s: launch failed and its stub could not be cleaned", new_cycle_id
            )
            return
        if not deleted:
            logger.warning(
                "fork %s: launch failed and its stub was kept (%s)", new_cycle_id, reason
            )

    def _apply_cleanup_empty(self, hop: CycleHop) -> None:

        root_id = root_cycle_id(hop.cycle_id)
        _, active_cmp, active_cid = read_active_pointer(self._stores.base_dir)
        deleted_ids: list[str] = []
        for _pass in range(2):
            progress = False
            entries = self._stores.campaigns.enumerate_cycles()
            family_ids = [
                e["cycle_id"]
                for e in entries
                if e["campaign_id"] == hop.campaign_id
                and e["cycle_id"] != root_id
                and e["parent_cycle_id"] == root_id
            ]
            for cid in family_ids:
                if cid in deleted_ids:
                    continue
                if hop.campaign_id == active_cmp and cid == active_cid:
                    continue
                # THE stub-deletion path, the same one `delete-cycle` takes — pointer discipline
                # and the store's spend banking are not things a sweep may route around.
                deleted, _reason = cleanup_stub_fork_if_empty(
                    campaign_store=self._stores.campaigns,
                    hop=CycleHop(campaign_id=hop.campaign_id, cycle_id=cid),
                    parent_cycle_id=root_id,
                )
                if deleted:
                    deleted_ids.append(cid)
                    progress = True
            if not progress:
                break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_capability_for(self, kind: str) -> None:
        """Map the kind to its one capability, then defer to the shared denial. An UNMAPPED kind is
        unwritable — ``CAP_FOR_KIND`` is exhaustive over the dispatched set at import, so reaching
        here with no cap means a verb slipped past that raise, and refusing it is the safe read."""
        cap = CAP_FOR_KIND.get(kind)
        if cap is None:
            logger.warning("command %r has no capability and is unwritable", kind)
            raise NotFoundError("Not found", code="not_found")
        require_capability(self._stores.identity, cap, subject=f"command {kind!r}")

    def _load_owned_campaign(self, campaign_id: str) -> Any:
        campaign = self._stores.campaigns.load_owned(
            campaign_id, str(self._stores.identity.user_id)
        )
        if campaign is None:
            raise NotFoundError(
                f"Campaign not found: {campaign_id}", code="command_target_not_found"
            )
        return campaign
