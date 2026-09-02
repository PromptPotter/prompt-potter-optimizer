"""Sole writer of ``CommandRecord`` at the API seam. One order, always: validate, dedupe by
``Idempotency-Key``, append the record, apply inline, append the ``CommandAckRecord``."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast, get_args

from pydantic import BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from promptpotter.application.archive_maintenance import (
    ArchiveReport,
    compact_measurement_archive,
    purge_cold_store,
    restore_measurement_archive,
)
from promptpotter.application.datasets.dataset_replace import (
    NothingToReplaceError,
    version_and_repoint,
)
from promptpotter.application.datasets.draft_patch import (
    EditDraftPatch,
    apply_draft_patch,
    plan_draft_patch,
)
from promptpotter.application.jobs.quota import clamp_budget_change, hold_ceiling
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.runner.origin_gate import GateDecision, submit_gate_decision
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.pipeline_overlay import overlay_sets_model_outside_allowed
from promptpotter.domain.run_records import CommandAckRecord, CommandRecord, CycleSeed
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.runtime_flags import write_armed_cells
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
    PotterError,
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

__all__ = ["CommandAcceptedBody", "CommandDispatcher", "CommandOutcome"]


LifecycleKind = Literal["archive-campaign", "delete-campaign", "unarchive-campaign"]

CycleScopedKind = Literal[
    "fork-cycle",
    "skip-searchpoint",
    "delete-cycle",
    "cleanup-empty-cycles",
    "pause-cycle",
    "set-sample-lookahead",
    "origin-gate-decision",
    "change-spend-budget",
    "start-run",
    "step-cycle",
]
WorkspaceScopedKind = Literal[
    "register-backend", "mint-campaign", "replace-dataset", "compact-archive"
]
CheckinScopedKind = Literal["edit-draft-campaign", "resolve-origin", "start-checkin"]
# Campaign-scoped IN-PLACE manifest edits (the campaign persists — distinct from
# `delete`, the one lifecycle verb that removes a tree). Rewrites `campaign.json`.
CampaignConfigKind = Literal["set-allowed-models", "set-campaign-label"]

Applier = Callable[[], Awaitable[Any]] | Callable[[], Any]

# The one cap→verb ladder (ADR-0005 §3): every command kind that funnels through
# `_record_and_apply` requires exactly one tier capability, checked at that single
# seam. A tenant owner holds every tier (OWNER_COMMAND_CAPABILITIES); a delegated
# sub-principal an attenuated subset. `fork-cycle` is RUN-tier — the babysit
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
    "start-run": CAMPAIGN_RUN_CAP,
    "fork-cycle": CAMPAIGN_RUN_CAP,
    "start-checkin": CAMPAIGN_RUN_CAP,
    "change-spend-budget": CAMPAIGN_BUDGET_CAP,
    "mint-campaign": CAMPAIGN_CREATE_CAP,
    "register-backend": CAMPAIGN_CREATE_CAP,
    "edit-draft-campaign": CAMPAIGN_CREATE_CAP,
    "resolve-origin": CAMPAIGN_CREATE_CAP,
    # Editing the allow-list DEFINES what a babysit steer may reach — strictly stronger
    # authority than `campaign.babysit`. The owner-held lifecycle tier is what stops a
    # babysit-delegate self-authorizing by adding their own model to it.
    "set-allowed-models": CAMPAIGN_LIFECYCLE_CAP,
    # Renaming is how every OTHER surface addresses the campaign to a human, so it sits
    # with the verbs that decide the campaign's existence rather than with the run tiers.
    "set-campaign-label": CAMPAIGN_LIFECYCLE_CAP,
    # A dataset slug is part of the measurement cache key, so repointing one re-addresses
    # every campaign that already measured against it — stronger authority than creating a
    # dataset, which is why it sits at the lifecycle tier rather than beside `mint-campaign`.
    "replace-dataset": CAMPAIGN_LIFECYCLE_CAP,
    # Rewrites rows every campaign measured against, and its purge step destroys paid spend
    # outright — the same authority `replace-dataset` sits at, for the same reason.
    "compact-archive": CAMPAIGN_LIFECYCLE_CAP,
    # Its own tier rather than a share of babysit: look-ahead spends the box's shared provider
    # rate bucket, which is the one thing a multi-tenant host may want to withhold from a
    # delegate, and it steers no measurement (the overshoot sample is discarded).
    "set-sample-lookahead": CAMPAIGN_LOOKAHEAD_CAP,
}

# Import-time exhaustiveness — a dispatched kind with no cap is a silent unguarded verb.
# Derived from the Literal types themselves, so the map cannot drift from the wire. Public
# because the router subtracts its typed routes from this to wire the generic one: a verb
# reachable over HTTP but absent HERE is gated by nothing, which is how `replace-dataset`
# ran unguarded — it was in no Literal, so this raise could not see it.
ALL_DISPATCHED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind)
    + get_args(CycleScopedKind)
    + get_args(WorkspaceScopedKind)
    + get_args(CheckinScopedKind)
    + get_args(CampaignConfigKind)
)
if set(CAP_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "CAP_FOR_KIND out of sync with the dispatched command set: "
        f"{ALL_DISPATCHED_KINDS.symmetric_difference(CAP_FOR_KIND)}"
    )


def _reject_bool(v: object) -> object:
    """``bool`` IS an ``int`` in Python and Pydantic coerces it, so a wire ``true`` arrives as 1 —
    which reads as "disarm" on a count and as a $1 ceiling on a budget."""
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


# The two wire scalar types. `strict` is what refuses `true` where an int is meant; `allow_inf_nan`
# is what refuses `+inf`, which PASSES a bare `ge=` bound and then disarms the `BudgetGate` whose
# probe is `spent >= cap`. Neither is a default — both were bought.
WireInt = Annotated[int, Field(strict=True)]
WireFloat = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]


class CommandPayload(StrictModel):
    """Base of every payload on the generic ``POST /commands/{kind}`` route. ``StrictModel`` forbids
    extras, so THE MODEL IS the accepted-key set — there is no list to fall out of step with it."""


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(min_length=1, max_length=128)


class CyclePayload(CampaignPayload):
    cycle_id: str = Field(min_length=1, max_length=128)


class DescendableCyclePayload(CyclePayload):
    """Carries an address that may descend into an inner sandbox. Declaring it by INHERITANCE is
    what makes "which kinds accept a descent" a type question — every other payload forbids the key
    already. Narrow on purpose: an inner cycle inherits the outer's pause
    (``runner/entry.py::_bind_run_controls``), so a second address for pause/skip would contradict a
    working channel; throughput is what an inner run answers for itself."""

    # Excluded from the dump: the router spends it resolving the leaf, after which `campaign_id` /
    # `cycle_id` ARE the inner cycle's, so recording the tail would address the record twice.
    descend: str | None = Field(default=None, max_length=512, exclude=True)


class ForkCyclePayload(CyclePayload):
    round: int = Field(default=0, ge=0)
    candidate_id: str = Field(default="", max_length=128)
    # Kept as a dict here and validated into a typed `CycleSeed` at the applier, which stamps the
    # lineage provenance the wire omits.
    seed: dict[str, Any]
    steered_by: str = Field(default="", max_length=256)
    keep_rounds: bool = Field(
        default=False,
        description=(
            "False (the default) is `operator_steered`: a clean offshoot from the origin, "
            "re-scoring the edited searchpoint. True is `operator_rewind` — rounds 0..round-1 "
            "are lifted and the fork continues at `round` under the seed's overrides, which is "
            "what an 'apply this from here' press means and what the terminal spells "
            "`resume --rewind N`."
        ),
    )


class SkipSearchpointPayload(CyclePayload):
    pass


class DeleteCyclePayload(CyclePayload):
    pass


class CleanupEmptyCyclesPayload(CyclePayload):
    pass


class PauseCyclePayload(CyclePayload):
    reason: str = Field(default="", max_length=512)


class SetSampleLookaheadPayload(DescendableCyclePayload):
    cells: WireInt = Field(ge=1, description="1 disarms.")


class OriginGateDecisionPayload(CyclePayload):
    # `GateDecision`, not a copy of its members: the wire vocabulary and the gate's own are one set.
    decision: GateDecision


class ChangeSpendBudgetPayload(CyclePayload):
    max_usd: WireFloat | None = Field(default=None, ge=0.0)
    max_tokens: WireInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one_ceiling(self) -> ChangeSpendBudgetPayload:
        """An ABSENT arm means "leave it untouched", so both absent is a command that asks for
        nothing and would ack ``applied`` having moved neither ceiling. Raised as the domain error
        rather than a ``ValueError``, which Pydantic would wrap — this one propagates unwrapped, so
        the CLI building the model directly gets the same 422-shaped refusal the route does."""
        if self.max_usd is None and self.max_tokens is None:
            raise PayloadInvalidError(
                "change-spend-budget requires at least one of max_usd / max_tokens."
            )
        return self


class RunLimitsPayload(CommandPayload):
    """The three launch ceilings, bounded ONCE. ``campaign_runner.main`` validates the CLI's
    ``--halt-at`` / ``--spend-budget`` / ``--token-budget`` through this before dispatch, so the
    terminal refuses what HTTP refuses — argparse types these and bounds none of them, and an
    unbounded ceiling is one that never fires or fires on round 0."""

    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    # Both budget arms ride: the USD one goes blind on a model with no rate on file and the token
    # one is what still holds, so serving only USD is a half-gate.
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)


class StartRunPayload(CyclePayload, RunLimitsPayload):
    kind: Literal["new", "resume"]


class StepCyclePayload(CyclePayload):
    rounds: WireInt = Field(default=1, ge=1, le=100)


class LifecyclePayload(CampaignPayload):
    reason: str = Field(default="", max_length=512)
    # Only meaningful for `delete-campaign`; harmless on the other two.
    keep_results: bool = False


class SetAllowedModelsPayload(CampaignPayload):
    allowed_models: list[str]


class SetCampaignLabelPayload(CampaignPayload):
    # Required, and `""` is the CLEAR — it restores the dataset-name fallback the display chain
    # already documents. Defaulting it too would give "clear" two spellings, omit and empty, and
    # the declared contract only ever named one.
    label: str = Field(max_length=200)


class RegisterBackendPayload(CommandPayload):
    name: str = Field(min_length=1, max_length=128)
    backend_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    # Auto-derived from `name` when omitted (`_slugify_backend_id`).
    id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")


class ReplaceDatasetPayload(CommandPayload):
    slug: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _slug_is_a_dataset_name(self) -> ReplaceDatasetPayload:
        from promptpotter.infrastructure.store.layout import validate_dataset_name

        validate_dataset_name(self.slug)
        return self


class CompactArchivePayload(CommandPayload):
    """``apply`` defaults to False on every mode, including the destructive one: a preview is what
    the operator consents on, so the write has to be asked for rather than defaulted into."""

    mode: Literal["compact", "restore", "purge-cold"]
    dataset: str | None = Field(default=None, min_length=1, max_length=64)
    apply: bool = False

    @model_validator(mode="after")
    def _dataset_is_a_dataset_name(self) -> CompactArchivePayload:
        from promptpotter.infrastructure.store.layout import validate_dataset_name

        if self.dataset is not None:
            validate_dataset_name(self.dataset)
        return self


class _CheckinPayload(CommandPayload):
    """``draft_id`` and ``campaign_id`` are the same id, re-keyed at ``create_checkin_campaign``;
    each check-in verb names it whichever way its wire schema does."""


class EditDraftCampaignPayload(_CheckinPayload):
    draft_id: str = Field(min_length=8, max_length=128)
    # TYPED, so `_validated_payload` is the whole of it — a `dict[str, Any]` defers validation into
    # the applier, past the capability gate. Required: an omitted patch is a no-op that still mints
    # a `CommandRecord` and an ack, so the ledger would carry an edit that edited nothing.
    patch: EditDraftPatch


class ResolveOriginPayload(_CheckinPayload):
    draft_id: str = Field(min_length=8, max_length=128)
    message: str = Field(default="", max_length=4000)


class StartCheckinPayload(_CheckinPayload):
    campaign_id: str = Field(min_length=8, max_length=128)


class MintCampaignPayload(CommandPayload):
    dataset_name: str = Field(min_length=1, max_length=64)
    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintCampaignPayload:
        """Deciding what a name IS belongs to ``validate_dataset_name`` — a pattern of its own here
        is a second rule that can disagree with the slug ingest mints off a filename."""
        from promptpotter.infrastructure.store.layout import validate_dataset_name

        validate_dataset_name(self.dataset_name)
        return self


# The generic route's kind → payload type. An enum-keyed dispatch table over TYPES, which is the
# form root `CLAUDE.md` sanctions — asking a type what it accepts, rather than asking a list of
# names, is what stops a key going silently unread when a verb grows one.
PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {
    "fork-cycle": ForkCyclePayload,
    "skip-searchpoint": SkipSearchpointPayload,
    "delete-cycle": DeleteCyclePayload,
    "cleanup-empty-cycles": CleanupEmptyCyclesPayload,
    "pause-cycle": PauseCyclePayload,
    "set-sample-lookahead": SetSampleLookaheadPayload,
    "origin-gate-decision": OriginGateDecisionPayload,
    "change-spend-budget": ChangeSpendBudgetPayload,
    "start-run": StartRunPayload,
    "step-cycle": StepCyclePayload,
    "archive-campaign": LifecyclePayload,
    "delete-campaign": LifecyclePayload,
    "unarchive-campaign": LifecyclePayload,
    "set-allowed-models": SetAllowedModelsPayload,
    "set-campaign-label": SetCampaignLabelPayload,
    "register-backend": RegisterBackendPayload,
    "mint-campaign": MintCampaignPayload,
    "replace-dataset": ReplaceDatasetPayload,
    "compact-archive": CompactArchivePayload,
    "edit-draft-campaign": EditDraftCampaignPayload,
    "resolve-origin": ResolveOriginPayload,
    "start-checkin": StartCheckinPayload,
}
# Total over the dispatched set, exactly as `CAP_FOR_KIND` is: a kind with no payload type is a
# kind whose wire shape nothing states, and a typed route is not an exemption from having one.
if set(PAYLOAD_MODEL_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "PAYLOAD_MODEL_FOR_KIND out of sync with the dispatched command set: "
        f"{ALL_DISPATCHED_KINDS.symmetric_difference(PAYLOAD_MODEL_FOR_KIND)}"
    )


class CommandAcceptedBody(StrictModel):
    """The 202 response shape declared in ``api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    correlation_id: str = Field(description="Echo of the request's `Idempotency-Key`.")
    ledger_sequence: int = Field(
        description="Offset at which the `CommandRecord` was appended.", ge=0
    )


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
    async def dispatch_lifecycle(
        self,
        *,
        kind: LifecycleKind,
        payload: LifecyclePayload,
        idempotency_key: str,
    ) -> CommandOutcome:
        """The ``CommandRecord`` lands on the WORKSPACE ledger because ``archive`` MOVES the
        campaign tree and ``delete`` REMOVES it — its own ledger cannot be the audit home."""
        self._load_owned_campaign(payload.campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            applier=lambda: self._apply_lifecycle(kind, payload),
        )

    async def dispatch_campaign_config(
        self,
        *,
        kind: CampaignConfigKind,
        payload: CampaignPayload,
        idempotency_key: str,
    ) -> CommandOutcome:
        """An in-place edit of ``campaign.json`` — the campaign persists, so the record is an
        ordinary workspace-ledger admin edit rather than the lifecycle move beside it."""
        self._load_owned_campaign(payload.campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            applier=self._build_campaign_config_applier(payload),
        )

    def _build_campaign_config_applier(self, payload: CampaignPayload) -> Applier:
        cid = payload.campaign_id
        if isinstance(payload, SetAllowedModelsPayload):
            models = list(payload.allowed_models)
            return lambda: self._apply_set_allowed_models(cid, models)
        if isinstance(payload, SetCampaignLabelPayload):
            label = payload.label
            return lambda: self._apply_set_campaign_label(cid, label)
        raise PayloadInvalidError(  # pragma: no cover — the registry pairs every kind with a type
            f"no applier wired for campaign-config payload {type(payload).__name__}"
        )

    def _apply_set_allowed_models(
        self, campaign_id: str, allowed_models: list[str]
    ) -> dict[str, Any]:
        """Rewrites the frozen ``allowed_models`` that both the fork cap-gate and the runner's
        grade-C stamp read."""
        self._stores.campaigns.set_allowed_models(campaign_id, allowed_models)
        return {"campaign_id": campaign_id, "allowed_models": list(allowed_models)}

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
        self,
        *,
        kind: CycleScopedKind,
        payload: CyclePayload,
        idempotency_key: str,
        expected_version: int | None,
    ) -> CommandOutcome:
        """The payload arrives TYPED, which is the whole validation — the CLI and the API build the
        same model, so neither entry point can validate a field the other spells differently.

        ``Expected-Version`` is checked only when the header is present — the v0 relaxation of
        ADR-0001, which mandates it."""
        campaign_id, cycle_id = payload.campaign_id, payload.cycle_id
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
        if kind == "delete-cycle":
            return await self._dispatch_delete_cycle(
                campaign=campaign, hop=hop, idempotency_key=idempotency_key
            )

        applier = self._build_cycle_applier(kind, campaign, hop, payload)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            applier=applier,
        )

    async def _dispatch_delete_cycle(
        self,
        *,
        campaign: Campaign,
        hop: CycleHop,
        idempotency_key: str,
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

        from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
            cleanup_stub_fork_if_empty,
        )

        def _apply() -> None:
            deleted, reason = cleanup_stub_fork_if_empty(
                campaign_store=self._stores.campaigns,
                hop=hop,
                parent_cycle_id=parent_cycle_id,
            )
            if not deleted:
                raise _DeleteCycleRejectedError(reason)

        return await self._record_and_apply(
            ledger=root_ledger,
            kind="delete-cycle",
            payload=DeleteCyclePayload(
                campaign_id=hop.campaign_id, cycle_id=hop.cycle_id
            ).model_dump(mode="json"),
            idempotency_key=idempotency_key,
            applier=_apply,
        )

    # ------------------------------------------------------------------
    # Workspace-scoped (no cycle target — backend registry mutations)
    # ------------------------------------------------------------------
    async def dispatch_workspace_command(
        self,
        *,
        kind: WorkspaceScopedKind,
        payload: CommandPayload,
        idempotency_key: str,
    ) -> CommandOutcome:
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        applier: Applier
        # A deduped retry must not re-run the migration — it would version the slug a second
        # time — and the body echoes the subject, so it replays without touching disk.
        on_replay: Callable[[], Any] | None = None
        if isinstance(payload, RegisterBackendPayload):
            backend = payload
            applier = lambda: self._apply_register_backend(backend)  # noqa: E731
        elif isinstance(payload, ReplaceDatasetPayload):
            slug = payload.slug
            applier = lambda: self._apply_replace_dataset(slug)  # noqa: E731
            on_replay = lambda: {"slug": slug}  # noqa: E731
        elif isinstance(payload, CompactArchivePayload):
            job = payload
            applier = lambda: self._apply_compact_archive(job)  # noqa: E731
            # A deduped retry must not re-run the pass: `purge-cold` would report a second deletion
            # of bytes already gone. It replays an EMPTY report — the same model the applier
            # answers with, because the route validates this body too, and a bespoke
            # `{"replayed": true}` shape 500s the retry that an Idempotency-Key exists to make safe.
            # All-zero is also the true answer: this attempt moved nothing.
            on_replay = lambda: ArchiveReport().model_dump(mode="json")  # noqa: E731
        elif isinstance(payload, MintCampaignPayload):
            mint = payload

            async def applier() -> None:
                await self._apply_mint_campaign(mint)
        else:  # pragma: no cover — the registry pairs every kind with a type
            raise PayloadInvalidError(
                f"no applier wired for workspace payload {type(payload).__name__}"
            )

        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            applier=applier,
            on_replay=on_replay,
        )

    # ------------------------------------------------------------------
    # Check-in scoped (origin authoring — the draft-mutating commands)
    # ------------------------------------------------------------------
    async def dispatch_checkin_command(
        self,
        *,
        kind: CheckinScopedKind,
        campaign_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        applier: Applier,
        on_replay: Callable[[], Any] | None = None,
        dedupe: bool = True,
        effect_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> CommandOutcome:
        """``on_replay`` rebuilds the 200 body from disk on a deduped retry. ``start-checkin``
        alone passes ``dedupe=False`` — its ``job_id`` has no disk home."""
        campaign = self._load_owned_campaign(campaign_id)
        cycle_dir = self._stores.campaigns.cycle_dir(campaign.root_hop)
        if not CycleLayout(cycle_dir).manifest.is_file():
            raise NotFoundError(
                f"check-in cycle not found: {campaign_id}/{campaign.root_cycle_id}",
                code="command_target_not_found",
            )
        return await self._record_and_apply(
            ledger=CycleEventLog.open(CycleDir(cycle_dir)),
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            applier=applier,
            on_replay=on_replay,
            dedupe=dedupe,
            effect_fn=effect_fn,
        )

    # ------------------------------------------------------------------
    # Shared record / apply / ack pipeline
    # ------------------------------------------------------------------
    async def _record_and_apply(
        self,
        *,
        ledger: CycleEventLog,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        applier: Applier,
        on_replay: Callable[[], Any] | None = None,
        dedupe: bool = True,
        effect_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> CommandOutcome:
        self._require_capability_for(kind)
        # A deduped retry never re-runs the applier, so a 200-body kind supplies
        # `on_replay` to rebuild its body from disk; one whose body has no disk
        # home passes `dedupe=False` and lets its own domain guard answer.
        if dedupe:
            existing = _find_idempotent_command(ledger, idempotency_key)
            if existing is not None:
                return CommandOutcome(
                    accepted=CommandAcceptedBody(
                        command_id=existing.command_id,
                        correlation_id=idempotency_key,
                        ledger_sequence=existing.offset,
                    ),
                    result=on_replay() if on_replay is not None else None,
                )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        applied_value: Any = None
        try:
            offset = emit_command(
                command_id=command_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                issued_by_user_id=acting_principal_id(self._stores.identity),
            )
            ack_status: Literal["applied", "rejected"] = "applied"
            ack_detail = ""
            try:
                result = applier()
                if asyncio.iscoroutine(result):
                    result = await result
                applied_value = result
            except _DeleteCycleRejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except PotterError as exc:
                # ONE central mapping seam for every applier error carrying an HTTP status.
                # Emit a rejected ack so the audit trail stays on the ledger, then re-raise for
                # `main.py`'s handler. One site, not one arm per applier; no ``HTTPException``.
                emit_command_ack(command_id=command_id, status="rejected", detail=str(exc))
                raise
            except Exception as exc:
                logger.exception("apply failed for %s", kind)
                ack_status = "rejected"
                ack_detail = str(exc)
            effect = effect_fn() if (effect_fn is not None and ack_status == "applied") else None
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
        kind: CycleScopedKind,
        campaign: Campaign,
        hop: CycleHop,
        payload: CyclePayload,
    ) -> Any:
        """Dispatched on the payload's TYPE, not on ``kind`` — every cycle-scoped verb owns one
        model, so the branch that reads a field is the branch its type reached. Nothing here
        re-validates: the model is the only validation, and a second lenient pass over an
        already-recorded payload can only disagree with the record."""
        if isinstance(payload, ForkCyclePayload):
            from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
                mint_operator_fork,
            )

            seed = _parse_cycle_seed(payload.seed)
            # Steering the model OUTSIDE `allowed_models` (empty = nothing sanctioned) is the
            # ADR-0005 §4 babysit action, a distinct cap above the RUN-tier fork. A steer to a
            # SANCTIONED model is a clean human fork.
            allowed_models = campaign.config.get("allowed_models") if campaign else None
            steers_disallowed_model = seed is not None and overlay_sets_model_outside_allowed(
                seed.pipeline_overlay, allowed_models
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
                # seeded-but-idle awaiting a CLI `resume` that never comes from the web. Pass no
                # spend/halt — the seed's reconciled limits govern at the runner seam.
                new_cycle_id = mint_operator_fork(
                    stores=self._stores,
                    hop=hop,
                    from_round=payload.round,
                    from_candidate_id=payload.candidate_id,
                    seed=seed,
                    steered_by=payload.steered_by,
                    keep_rounds=payload.keep_rounds,
                )
                await self._apply_start_run(
                    hop=CycleHop(campaign_id=hop.campaign_id, cycle_id=new_cycle_id),
                    kind="resume",
                    halt_at_accuracy=None,
                    spend_budget_usd=None,
                )

            return _apply_fork
        if isinstance(payload, StepCyclePayload):
            # Advance N rounds in place then auto-pause, on the resume machinery + RunMode's
            # run-scoped stop — the `campaign.step` tier for a delegate without run.
            steps = payload.rounds
            return lambda: self._apply_start_run(
                hop=hop,
                kind="resume",
                halt_at_accuracy=None,
                spend_budget_usd=None,
                stop_after_rounds=steps,
            )
        if isinstance(payload, SkipSearchpointPayload):
            return lambda: self._apply_skip_searchpoint(hop)
        if isinstance(payload, CleanupEmptyCyclesPayload):
            return lambda: self._apply_cleanup_empty(hop)
        if isinstance(payload, PauseCyclePayload):
            return lambda: self._apply_pause_cycle(hop)
        if isinstance(payload, SetSampleLookaheadPayload):
            cells = payload.cells
            return lambda: self._apply_set_sample_lookahead(hop, cells=cells)
        if isinstance(payload, OriginGateDecisionPayload):
            decision = payload.decision
            return lambda: self._apply_origin_gate_decision(hop, decision)
        if isinstance(payload, ChangeSpendBudgetPayload):
            usd_val, tok_val = payload.max_usd, payload.max_tokens

            async def _apply_budget() -> None:
                await self._apply_change_spend_budget(hop, max_usd=usd_val, max_tokens=tok_val)

            return _apply_budget
        if isinstance(payload, StartRunPayload):
            run = payload

            async def _apply() -> None:
                await self._apply_start_run(
                    hop=hop,
                    kind=run.kind,
                    halt_at_accuracy=run.halt_at_accuracy,
                    spend_budget_usd=run.spend_budget_usd,
                    token_budget=run.token_budget,
                )

            return _apply
        raise PayloadInvalidError(  # pragma: no cover — the registry pairs every kind with a type
            f"no applier wired for cycle-scoped kind {kind!r}"
        )

    def _apply_lifecycle(self, kind: LifecycleKind, payload: LifecyclePayload) -> None:
        changed_at = utcnow_iso()
        campaigns = self._stores.campaigns
        campaign_id, reason = payload.campaign_id, payload.reason
        if kind == "archive-campaign":
            campaigns.archive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif kind == "unarchive-campaign":
            campaigns.unarchive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        else:  # delete-campaign — destructive (keepsake spared only with keep_results)
            campaigns.delete_campaign(
                campaign_id,
                keep_results=payload.keep_results,
                changed_at=changed_at,
                reason=reason,
                inner_sandbox_root=inner_sandboxes_dir(self._stores.shared_root),
            )

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
        write_armed_cells(self._stores.campaigns.cycle_dir(hop), cells)

    def _apply_origin_gate_decision(self, hop: CycleHop, decision: GateDecision) -> None:
        """The browser's half of the gate. The write itself is
        ``runner/origin_gate.py::submit_gate_decision`` — one writer, so an embedded host answers
        the gate through the same file this does rather than needing an HTTP client."""
        submit_gate_decision(self._stores.campaigns.cycle_dir(hop), decision)

    def _clamp_to_account_ceilings(
        self,
        hop: CycleHop,
        job_registry: JobRegistry,
        max_usd: float | None,
        max_tokens: int | None,
    ) -> tuple[float | None, int | None]:
        """Only a SUPPLIED arm is clamped — composing an absent one would write a ceiling the
        caller asked to leave alone."""
        user = self._stores.users.get_or_create(
            user_id=str(self._stores.identity.user_id),
            tenant_id=str(self._stores.identity.tenant_id),
        )
        caps = clamp_budget_change(
            max_usd=max_usd,
            max_tokens=max_tokens,
            user=user,
            stores=self._stores,
            job_registry=job_registry,
            hop=hop,
        )
        return caps.usd, caps.tokens

    async def _apply_change_spend_budget(
        self,
        hop: CycleHop,
        *,
        max_usd: float | None,
        max_tokens: int | None,
    ) -> None:
        """The round loop's BudgetGate re-reads the moved ceiling every clean round. A ``None`` arg
        leaves that ceiling untouched; ``0`` halts at the next round boundary. Both arms compose
        against the account first, because ``entry.py::_usd_cap`` prefers this file over the cap the
        launch composed — unclamped, raising one here is the way around the host-wallet gate."""
        registry = self._job_registry
        if registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        max_usd, max_tokens = await asyncio.to_thread(
            self._clamp_to_account_ceilings, hop, registry, max_usd, max_tokens
        )
        hold_ceiling(
            job_registry=registry,
            hop=hop,
            cycle_dir=self._stores.campaigns.cycle_dir(hop),
            max_usd=max_usd,
            max_tokens=max_tokens,
        )

    async def _apply_mint_campaign(self, payload: MintCampaignPayload) -> None:
        """The 202 returns once the manifest + root cycle index are written; the run proceeds via
        JobRegistry and the webapp discovers the new ids by polling ``/api/v1/active``."""
        from promptpotter.application.jobs.launcher.mint_and_start import mint_campaign_command

        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        # Campaign-from-origin rides the check-in path, not this workspace verb, so there is no
        # origin_override here. Its PotterErrors map centrally in `_record_and_apply`.
        await mint_campaign_command(
            stores=self._stores,
            dataset_name=payload.dataset_name,
            job_registry=self._job_registry,
            halt_at_accuracy=payload.halt_at_accuracy,
            spend_budget_usd=payload.spend_budget_usd,
            token_budget=payload.token_budget,
        )

    async def _apply_start_run(
        self,
        *,
        hop: CycleHop,
        kind: str,
        halt_at_accuracy: float | None,
        spend_budget_usd: float | None,
        token_budget: int | None = None,
        stop_after_rounds: int | None = None,
    ) -> None:
        """``stop_after_rounds`` bounds the run in place — the ``step-round`` verb's mechanism."""
        from promptpotter.application.jobs.launcher.mint_and_start import start_run_command

        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        # Quota / Launch / BackendUnreachable are PotterErrors mapped centrally
        # by _record_and_apply — no per-applier arm here.
        await start_run_command(
            stores=self._stores,
            job_registry=self._job_registry,
            hop=hop,
            kind=kind,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
            token_budget=token_budget,
            stop_after_rounds=stop_after_rounds,
        )

    def _apply_cleanup_empty(self, hop: CycleHop) -> None:
        from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
            cleanup_stub_fork_if_empty,
        )

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
        """Map the kind to its one tier, then defer to the shared denial. An UNMAPPED kind is
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


# ----------------------------------------------------------------------------------
# The two origin-authoring write paths, module-level so EVERY adapter reaches them — a router
# module cannot be one, since importing it drags FastAPI into a CLI verb. The rules themselves
# are one layer down (`application/datasets/draft_patch.py`); what stays here is the
# record-and-apply order this module owns.
# ----------------------------------------------------------------------------------


def _reread_draft(stores: Stores, draft_id: str) -> Any:
    from promptpotter.application.jobs.launcher.checkin import load_checkin_draft

    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return draft


def reread_draft_wire(stores: Stores, draft_id: str) -> dict[str, Any]:
    """The post-mutation draft, re-read from ``draft.json`` — the response body for a deduped
    ``Idempotency-Key`` retry, whose first attempt already persisted it."""
    from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks

    return draft_wire_with_locks(_reread_draft(stores, draft_id))


def origin_effect(stores: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """What the applier MOVED in the origin, diffed against its pre-apply projection. Recorded on
    the ack because the command payload states only what was ASKED for."""
    from promptpotter.application.datasets.origin_readiness import origin_delta, origin_projection

    return origin_delta(before, origin_projection(_reread_draft(stores, draft_id)))


async def dispatch_draft_patch(
    stores: Stores,
    *,
    draft_id: str,
    patch: EditDraftPatch,
    idempotency_key: str,
) -> dict[str, Any]:
    """The single write path behind ``edit-draft-campaign``. The candidate-library ingresses and
    the CLI's ``--set`` derive their patch and then ride this, so an origin edit is a
    ``CommandRecord`` whatever the ingress looked like."""
    from promptpotter.application.datasets.origin_readiness import origin_projection
    from promptpotter.application.jobs.launcher.checkin import save_checkin_draft
    from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks

    draft = _reread_draft(stores, draft_id)
    plan = plan_draft_patch(stores, draft, patch)

    def _apply() -> dict[str, Any]:
        updated = apply_draft_patch(draft, plan)
        save_checkin_draft(stores, updated)
        return draft_wire_with_locks(updated)

    before = origin_projection(draft)
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(
        kind="edit-draft-campaign",
        campaign_id=draft_id,
        payload=EditDraftCampaignPayload(draft_id=draft_id, patch=patch).model_dump(mode="json"),
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=lambda: reread_draft_wire(stores, draft_id),
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


async def dispatch_origin_resolution(
    stores: Stores,
    *,
    draft_id: str,
    message: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """One origin-resolver turn, recorded. Calling ``resolve_origin_turn`` bare puts the turn on no
    ledger AND re-spends the LLM call that ``on_replay`` serves from ``cache.json``."""
    from promptpotter.application.datasets.origin_readiness import origin_projection
    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
    from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks

    draft = _reread_draft(stores, draft_id)

    async def _apply() -> dict[str, Any]:
        try:
            result = await resolve_origin_turn(stores=stores, draft=draft, message=message)
        except PotterError:
            raise
        except Exception as exc:
            # A PotterError, so the dispatcher's mapping seam emits a `rejected` ack and
            # re-raises as 502 rather than the generic 409 the bare-Exception arm produces.
            logger.exception("resolve-origin turn failed for draft %s", draft_id)
            raise ServiceUnavailableError(
                f"origin resolver turn failed: {exc}", code="resolver_failed"
            ) from exc
        return {"resolution": result.resolution, "draft": draft_wire_with_locks(result.draft)}

    def _on_replay() -> dict[str, Any]:
        # `cache.json::resolution` is byte-identical to the live turn's block, so a deduped
        # retry never re-spends the LLM call.
        bank = stores.checkin.load_bank(draft_id) or {}
        return {
            "resolution": bank.get("resolution") or {},
            "draft": reread_draft_wire(stores, draft_id),
        }

    before = origin_projection(draft)
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(
        kind="resolve-origin",
        campaign_id=draft_id,
        payload=ResolveOriginPayload(draft_id=draft_id, message=message).model_dump(mode="json"),
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=_on_replay,
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)
