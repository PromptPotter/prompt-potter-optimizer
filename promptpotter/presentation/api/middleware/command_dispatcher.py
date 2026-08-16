"""Sole writer of ``CommandRecord`` at the API seam. One order, always: validate, dedupe by
``Idempotency-Key``, append the record, apply inline, append the ``CommandAckRecord``."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field, ValidationError

from promptpotter.application.jobs.quota import clamp_budget_change, hold_ceiling
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.pipeline_overlay import overlay_sets_model_outside_allowed
from promptpotter.domain.run_records import CommandRecord, CycleSeed
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.store.io import write_json
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
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
    SCORING_SAMPLE_LOOKAHEAD_CAP,
    has_capability,
)


class _DeleteCycleRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _IdempotentMatch(StrictModel):
    model_config = ConfigDict(frozen=True)
    command_id: str
    offset: int


def _optional_float(raw: object) -> float | None:
    if isinstance(raw, int | float):
        return float(raw)
    return None


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
    """O(n) over the cycle ledger: a ledger holds thousands of records, not millions, and
    commands are operator-paced."""
    for offset, record in enumerate(ledger.iter()):
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            return _IdempotentMatch(command_id=record.command_id, offset=offset)
    return None


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
WorkspaceBackendKind = Literal["register-backend", "mint-campaign"]
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
    # The one HOST-ADMIN entry in this map. Look-ahead spends the box's shared provider rate
    # bucket rather than the campaign's own budget, so no tenant tier carries it — see
    # `shared/identity.py::SCORING_SAMPLE_LOOKAHEAD_CAP`.
    "set-sample-lookahead": SCORING_SAMPLE_LOOKAHEAD_CAP,
}

# Import-time exhaustiveness — a dispatched kind with no cap is a silent unguarded verb.
# Derived from the Literal types themselves, so the map cannot drift from the wire.
_ALL_DISPATCHED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind)
    + get_args(CycleScopedKind)
    + get_args(WorkspaceBackendKind)
    + get_args(CheckinScopedKind)
    + get_args(CampaignConfigKind)
)
if set(CAP_FOR_KIND) != _ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "CAP_FOR_KIND out of sync with the dispatched command set: "
        f"{_ALL_DISPATCHED_KINDS.symmetric_difference(CAP_FOR_KIND)}"
    )


class CommandAcceptedBody(StrictModel):
    """The 202 response shape declared in ``m12-api-openapi.yaml``."""

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

    def __init__(self, stores: Stores, job_registry: Any | None = None) -> None:
        self._stores = stores
        self._job_registry = job_registry

    # ------------------------------------------------------------------
    # Lifecycle (campaign-scoped, workspace-style)
    # ------------------------------------------------------------------
    async def dispatch_lifecycle(
        self,
        *,
        kind: LifecycleKind,
        campaign_id: str,
        reason: str,
        idempotency_key: str,
        keep_results: bool = False,
    ) -> CommandOutcome:
        """The ``CommandRecord`` lands on the WORKSPACE ledger because ``archive`` MOVES the
        campaign tree and ``delete`` REMOVES it — its own ledger cannot be the audit home."""
        self._load_owned_campaign(campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, "reason": reason, "keep_results": keep_results},
            idempotency_key=idempotency_key,
            applier=lambda: self._apply_lifecycle(kind, campaign_id, reason, keep_results),
        )

    async def dispatch_campaign_config(
        self,
        *,
        kind: CampaignConfigKind,
        campaign_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> CommandOutcome:
        """An in-place edit of ``campaign.json`` — the campaign persists, so the record is an
        ordinary workspace-ledger admin edit rather than the lifecycle move beside it."""
        self._load_owned_campaign(campaign_id)
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, **payload},
            idempotency_key=idempotency_key,
            applier=self._build_campaign_config_applier(kind, campaign_id, payload),
        )

    def _build_campaign_config_applier(
        self, kind: CampaignConfigKind, campaign_id: str, payload: dict[str, Any]
    ) -> Applier:
        if kind == "set-allowed-models":
            raw = payload.get("allowed_models")
            models = [str(m) for m in raw] if isinstance(raw, list) else []
            return lambda: self._apply_set_allowed_models(campaign_id, models)
        return lambda: self._apply_set_campaign_label(campaign_id, str(payload.get("label", "")))

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
        campaign_id: str,
        cycle_id: str,
        payload_extras: dict[str, Any],
        idempotency_key: str,
        expected_version: int | None,
    ) -> CommandOutcome:
        """``Expected-Version`` is checked only when the header is present — the v0 relaxation of
        ADR-0001, which mandates it."""
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

        applier = self._build_cycle_applier(kind, campaign, hop, payload_extras)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, "cycle_id": cycle_id, **payload_extras},
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
            payload={"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id},
            idempotency_key=idempotency_key,
            applier=_apply,
        )

    # ------------------------------------------------------------------
    # Workspace-scoped (no cycle target — backend registry mutations)
    # ------------------------------------------------------------------
    async def dispatch_workspace_command(
        self,
        *,
        kind: WorkspaceBackendKind,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> CommandOutcome:
        ledger = CycleEventLog.open_workspace(self._stores.base_dir)
        applier: Applier
        if kind == "register-backend":
            applier = lambda: self._apply_register_backend(payload)  # noqa: E731
        else:
            assert kind == "mint-campaign"

            async def applier() -> None:
                await self._apply_mint_campaign(payload)

        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            applier=applier,
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
                issued_by_user_id=self._acting_principal_id(),
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
        payload_extras: dict[str, Any],
    ) -> Any:
        if kind == "fork-cycle":
            from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
                mint_operator_fork,
            )

            seed = _parse_cycle_seed(payload_extras.get("seed"))
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
                    self._acting_principal_id(),
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
                    from_round=int(payload_extras.get("round", 0)),
                    from_candidate_id=str(payload_extras.get("candidate_id", "")),
                    seed=seed,
                    steered_by=str(payload_extras.get("steered_by", "")),
                )
                await self._apply_start_run(
                    hop=CycleHop(campaign_id=hop.campaign_id, cycle_id=new_cycle_id),
                    kind="resume",
                    halt_at_accuracy=None,
                    spend_budget_usd=None,
                )

            return _apply_fork
        if kind == "step-cycle":
            # Advance N rounds in place then auto-pause, on the resume machinery + RunMode's
            # run-scoped stop — the `campaign.step` tier for a delegate without run.
            rounds = payload_extras.get("rounds", 1)
            steps = int(rounds) if isinstance(rounds, int | float) else 1
            return lambda: self._apply_start_run(
                hop=hop,
                kind="resume",
                halt_at_accuracy=None,
                spend_budget_usd=None,
                stop_after_rounds=max(1, steps),
            )
        if kind == "skip-searchpoint":
            return lambda: self._apply_skip_searchpoint(hop)
        if kind == "cleanup-empty-cycles":
            return lambda: self._apply_cleanup_empty(hop)
        if kind == "pause-cycle":
            return lambda: self._apply_pause_cycle(hop)
        if kind == "set-sample-lookahead":
            enabled = payload_extras.get("enabled")
            # Strictly boolean: this verb both arms and cancels, so a coerced value picks one of
            # two opposite acts.
            if not isinstance(enabled, bool):
                raise PayloadInvalidError("set-sample-lookahead requires a boolean `enabled`.")
            return lambda: self._apply_set_sample_lookahead(hop, enabled=enabled)
        if kind == "origin-gate-decision":
            decision = str(payload_extras.get("decision", ""))
            if decision not in ("rescore", "proceed", "abort"):
                raise PayloadInvalidError(
                    "origin-gate-decision requires decision ∈ {rescore, proceed, abort}."
                )
            return lambda: self._apply_origin_gate_decision(hop, decision)
        if kind == "change-spend-budget":
            max_usd = payload_extras.get("max_usd")
            max_tokens = payload_extras.get("max_tokens")
            # An ABSENT ceiling means "leave it untouched"; a PRESENT one that is non-numeric
            # or negative is a typo, not a no-op. Coercing it to None drops that ceiling while
            # the other applies, so the operator believes both landed when only one did.
            if max_usd is not None and (
                not isinstance(max_usd, int | float) or isinstance(max_usd, bool) or max_usd < 0
            ):
                raise PayloadInvalidError("change-spend-budget max_usd must be a number >= 0.")
            if max_tokens is not None and (
                not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0
            ):
                raise PayloadInvalidError("change-spend-budget max_tokens must be an int >= 0.")
            if max_usd is None and max_tokens is None:
                raise PayloadInvalidError(
                    "change-spend-budget requires at least one of max_usd / max_tokens."
                )
            usd_val = float(max_usd) if max_usd is not None else None
            tok_val = int(max_tokens) if max_tokens is not None else None

            async def _apply_budget() -> None:
                await self._apply_change_spend_budget(hop, max_usd=usd_val, max_tokens=tok_val)

            return _apply_budget
        if kind == "start-run":
            run_kind = str(payload_extras.get("kind", ""))
            halt = payload_extras.get("halt_at_accuracy")
            spend = payload_extras.get("spend_budget_usd")

            async def _apply() -> None:
                await self._apply_start_run(
                    hop=hop,
                    kind=run_kind,
                    halt_at_accuracy=float(halt) if isinstance(halt, int | float) else None,
                    spend_budget_usd=float(spend) if isinstance(spend, int | float) else None,
                )

            return _apply
        raise PayloadInvalidError(  # pragma: no cover — caller validates kind
            f"no applier wired for cycle-scoped kind {kind!r}"
        )

    def _apply_lifecycle(
        self, kind: LifecycleKind, campaign_id: str, reason: str, keep_results: bool
    ) -> None:
        changed_at = utcnow_iso()
        campaigns = self._stores.campaigns
        if kind == "archive-campaign":
            campaigns.archive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif kind == "unarchive-campaign":
            campaigns.unarchive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        else:  # delete-campaign — destructive (keepsake spared only with keep_results)
            campaigns.delete_campaign(
                campaign_id,
                keep_results=keep_results,
                changed_at=changed_at,
                reason=reason,
                inner_sandbox_root=inner_sandboxes_dir(self._stores.shared_root),
            )

    def _apply_register_backend(self, payload: dict[str, Any]) -> None:
        name = str(payload["name"])
        backend_type = str(payload["backend_type"])
        base_url = str(payload["base_url"])
        explicit_id = payload.get("id")
        backend_id = (
            str(explicit_id)
            if isinstance(explicit_id, str) and explicit_id
            else _slugify_backend_id(name)
        )
        if self._stores.backends.get(backend_id) is not None:
            raise ConflictError(
                f"Backend '{backend_id}' already exists", details={"backend_id": backend_id}
            )
        self._stores.backends.register(
            BackendConnection(
                id=backend_id,
                name=name,
                backend_type=backend_type,
                base_url=base_url.rstrip("/"),
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

    def _apply_set_sample_lookahead(self, hop: CycleHop, *, enabled: bool) -> None:
        """Arm/disarm the walk's second in-flight sample; the next round that scores spends it.
        Pointedly does NOT ``mark_human_intervened`` as its neighbour above does — skip changes what
        was measured, this cannot, and a babysat stamp would assert a steer that did not happen."""
        flag = CycleLayout(self._stores.campaigns.cycle_dir(hop)).sample_lookahead_flag
        if not enabled:
            flag.unlink(missing_ok=True)
            return
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")

    def _apply_origin_gate_decision(self, hop: CycleHop, decision: str) -> None:
        """The one decision channel all three surfaces write; ``run_origin_gate`` polls it, acts
        (rescore / proceed / abort) and clears the file. Last write wins."""
        gate_path = CycleLayout(self._stores.campaigns.cycle_dir(hop)).gate_decision
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(gate_path, {"decision": decision})

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

    async def _apply_mint_campaign(self, payload: dict[str, Any]) -> None:
        """The 202 returns once the manifest + root cycle index are written; the run proceeds via
        JobRegistry and the webapp discovers the new ids by polling ``/api/v1/active``."""
        from promptpotter.application.jobs.launcher.mint_and_start import mint_campaign_command

        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        dataset_name = str(payload.get("dataset_name", ""))
        # Campaign-from-origin rides the check-in path, not this workspace verb, so there is no
        # origin_override here. Its PotterErrors map centrally in `_record_and_apply`.
        await mint_campaign_command(
            stores=self._stores,
            dataset_name=dataset_name,
            job_registry=self._job_registry,
            halt_at_accuracy=_optional_float(payload.get("halt_at_accuracy")),
            spend_budget_usd=_optional_float(payload.get("spend_budget_usd")),
        )

    async def _apply_start_run(
        self,
        *,
        hop: CycleHop,
        kind: str,
        halt_at_accuracy: float | None,
        spend_budget_usd: float | None,
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
            stop_after_rounds=stop_after_rounds,
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
                deleted, _reason = self._stores.campaigns.try_delete_stub_cycle(
                    CycleHop(campaign_id=hop.campaign_id, cycle_id=cid)
                )
                if deleted:
                    deleted_ids.append(cid)
                    progress = True
            if not progress:
                break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _acting_principal_id(self) -> str:
        """For a delegated sub-principal (ADR-0005) this is its own ``claims["principal"]``, not
        the delegator whose tenant it acts in — so the audit trail names the real actor."""
        principal = self._stores.identity.claims.get("principal")
        if isinstance(principal, str) and principal:
            return principal
        return str(self._stores.identity.user_id)

    def _require_capability_for(self, kind: str) -> None:
        """Absence raises 404, never 403 — the existence-hiding posture cross-user reads use.
        A tenant owner holds every tier; a delegated sub-principal an attenuated subset."""
        cap = CAP_FOR_KIND.get(kind)
        if cap is None or not has_capability(self._stores.identity, cap):
            logger.warning(
                "command %r denied for principal %s (missing %s)",
                kind,
                self._acting_principal_id(),
                cap,
            )
            raise NotFoundError("Not found", code="not_found")

    def _load_owned_campaign(self, campaign_id: str) -> Any:
        campaign = self._stores.campaigns.load_owned(
            campaign_id, str(self._stores.identity.user_id)
        )
        if campaign is None:
            raise NotFoundError(
                f"Campaign not found: {campaign_id}", code="command_target_not_found"
            )
        return campaign
