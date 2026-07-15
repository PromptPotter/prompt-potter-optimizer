"""``CommandDispatcher`` — sole writer of ``CommandRecord`` at the API seam.

Validates the inbound HTTP command, opens the target ledger, dedupes by
``Idempotency-Key``, appends one ``CommandRecord`` through
``emit_command``, applies inline, then appends ``CommandAckRecord``
through ``emit_command_ack`` once applied.

Three dispatch shapes:

- ``dispatch_lifecycle`` — campaign lifecycle commands (archive / delete /
  unarchive). Target is a campaign; ``CommandRecord`` lands on the
  campaign's root cycle ledger. No ``Expected-Version``.

- ``dispatch_cycle_command`` — cycle-scoped sanctioned-POST commands (the
  ``CycleScopedKind`` set: fork-cycle, pause-cycle, skip-searchpoint,
  delete-cycle, cleanup-empty-cycles, origin-gate-decision,
  change-spend-budget, start-run). Target is a specific cycle;
  ``CommandRecord`` lands on that cycle's ledger. ``Expected-Version``
  validated when present (v0 relaxation per the ``ExpectedVersion``
  parameter component note in ``docs/specs/m12-api-openapi.yaml``).

- ``dispatch_workspace_command`` — workspace-scoped commands (the
  ``WorkspaceBackendKind`` set: register-backend,
  mint-campaign). Target is the tenant workspace, not any cycle;
  ``CommandRecord`` lands on the workspace ledger at
  ``projects/{tenant}/.workspace/events.jsonl`` per the §0 Persistence
  sibling amendment. No ``Expected-Version``.

- ``dispatch_checkin_command`` — the origin-authoring commands (the
  ``CheckinScopedKind`` set: edit-draft-campaign, resolve-origin,
  start-checkin). Target is the check-in campaign's root cycle
  (``cycle_chk_*``); the caller supplies the applier because each answers a
  domain object, carried back on ``CommandOutcome.result``. No
  ``Expected-Version``.

Closed inbound set: ``docs/specs/m12-api-openapi.yaml``. Permanent contract:
``docs/adr/0001-m12-control-plane.md``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.opt_search_point import overlay_sets_model_outside_allowed
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.models import (
    emit_command,
    emit_command_ack,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.store import (
    Stores,
    read_active_pointer,
)
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json
from promptpotter.infrastructure.store.layout import CycleLayout, root_cycle_id
from promptpotter.presentation.api.middleware.command_dispatcher.helpers import (
    _DeleteCycleRejectedError,
    _find_idempotent_command,
    _optional_float,
    _parse_cycle_seed,
    _slugify_backend_id,
)
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
    has_capability,
)

logger = logging.getLogger(__name__)

__all__ = ["CommandAcceptedBody", "CommandDispatcher", "CommandOutcome"]


LifecycleKind = Literal["archive-campaign", "delete-campaign", "unarchive-campaign"]

CycleScopedKind = Literal[
    "fork-cycle",
    "skip-searchpoint",
    "delete-cycle",
    "cleanup-empty-cycles",
    "pause-cycle",
    "origin-gate-decision",
    "change-spend-budget",
    "start-run",
    "step-cycle",
]
WorkspaceBackendKind = Literal["register-backend", "mint-campaign"]
CheckinScopedKind = Literal["edit-draft-campaign", "resolve-origin", "start-checkin"]

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
}

# Import-time exhaustiveness — a new dispatched kind with no cap is a silent
# unguarded verb (the membership-over-NAMES bug class). Derive the closed set
# from the Literal types themselves so the map can never drift from the wire.
_ALL_DISPATCHED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind)
    + get_args(CycleScopedKind)
    + get_args(WorkspaceBackendKind)
    + get_args(CheckinScopedKind)
)
assert set(CAP_FOR_KIND) == _ALL_DISPATCHED_KINDS, (
    "CAP_FOR_KIND out of sync with the dispatched command set: "
    f"{_ALL_DISPATCHED_KINDS.symmetric_difference(CAP_FOR_KIND)}"
)


class CommandAcceptedBody(BaseModel):
    """The 202 response shape declared in ``m12-api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    correlation_id: str = Field(description="Echo of the request's `Idempotency-Key`.")
    ledger_sequence: int = Field(
        description="Offset at which the `CommandRecord` was appended.", ge=0
    )


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """A dispatched command's audit body plus the applier's value.

    ``result`` is ``None`` for the 202 kinds and a domain object for the check-in
    kinds, whose 200 bodies are declared in ``m12-api-openapi.yaml``. Carrying it
    here is what let those three stop bypassing the dispatcher — each applied
    inline purely so it could return something.
    """

    accepted: CommandAcceptedBody
    result: Any = None


class CommandDispatcher:
    """One per request. Carries the request-scoped ``Stores`` (identity-bound).

    ``job_registry`` is the process-wide singleton (stashed on
    ``app.state.job_registry`` at startup); required when dispatching
    the launcher commands ``mint-campaign`` / ``start-run``.
    """

    def __init__(self, store: Stores, job_registry: Any | None = None) -> None:
        self._store = store
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
        """Workspace-scoped inline-apply dispatch for campaign lifecycle commands.

        The ``CommandRecord`` lands on the WORKSPACE ledger
        (``projects/{tenant}/.workspace/events.jsonl``), not the campaign's own —
        ``archive`` MOVES that tree into the recycle bin and ``delete`` REMOVES it,
        so the campaign's own ledger can't be the audit home. Owner-gated by
        ``Stores.identity`` (cross-user reads 404, not 403); archiving or deleting
        the active-session campaign is refused (409) before anything is recorded."""
        self._load_owned_campaign(campaign_id)
        if kind in ("archive-campaign", "delete-campaign"):
            _, active_campaign, _ = read_active_pointer(
                self._store.tenant_id, projects_root=self._store.projects_root
            )
            if active_campaign == campaign_id:
                raise ConflictError(
                    f"refusing to {kind} {campaign_id}: active campaign — switch first"
                )

        ledger = CycleEventLog.open_workspace(WorkspaceDir(self._store.base_dir))
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, "reason": reason, "keep_results": keep_results},
            idempotency_key=idempotency_key,
            applier=lambda: self._apply_lifecycle(kind, campaign_id, reason, keep_results),
        )

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
        """Cycle-scoped inline-apply dispatch. ``Expected-Version`` validated
        when present; absent header skips the check (v0 relaxation)."""
        campaign = self._load_owned_campaign(campaign_id)
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        if not (cycle_dir / "index.json").is_file():
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

        # delete-cycle's apply step removes the dir AND its ledger file. We
        # must build the CommandRecord + ack BEFORE the dir disappears; pre-
        # emit on the parent's root ledger as the audit destination.
        if kind == "delete-cycle":
            return await self._dispatch_delete_cycle(
                campaign=campaign,
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                idempotency_key=idempotency_key,
            )

        applier = self._build_cycle_applier(kind, campaign, campaign_id, cycle_id, payload_extras)
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
        campaign: Any,
        campaign_id: str,
        cycle_id: str,
        idempotency_key: str,
    ) -> CommandOutcome:
        """delete-cycle writes its audit trail on the campaign's root cycle
        ledger — the target cycle's own ledger goes away as part of the apply."""
        _, active_campaign, active_cycle = read_active_pointer(
            self._store.tenant_id, projects_root=self._store.projects_root
        )
        if active_campaign == campaign_id and active_cycle == cycle_id:
            raise ConflictError(f"refusing to delete {cycle_id}: active cycle — switch first")

        root_dir = self._store.campaigns.cycle_dir(campaign_id, campaign.root_cycle_id)
        root_ledger = CycleEventLog.open(CycleDir(root_dir))

        def _apply() -> None:
            deleted, reason = self._store.campaigns.try_delete_stub_cycle(campaign_id, cycle_id)
            if not deleted:
                raise _DeleteCycleRejectedError(reason)

        return await self._record_and_apply(
            ledger=root_ledger,
            kind="delete-cycle",
            payload={"campaign_id": campaign_id, "cycle_id": cycle_id},
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
        """Tenant-scoped inline-apply dispatch — backend-registry mutations
        that have no cycle target. ``CommandRecord`` lands on the workspace
        ledger (``projects/{tenant}/.workspace/events.jsonl``) per the §0
        Persistence sibling. No ``Expected-Version``."""
        ledger = CycleEventLog.open_workspace(WorkspaceDir(self._store.base_dir))
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
        """Inline-apply dispatch for the three commands that author an origin.

        Target is the check-in campaign's root cycle — ``cycle_chk_*``, minted with
        its own ``.runtime/`` by ``mint_checkin_skeleton`` and retained across the
        flip to ``active``, so an origin edit lands on the same ledger the run later
        appends its rounds to and a fork inherits it via ``inherit_from``. No
        ``Expected-Version`` — a draft is authored, not raced.

        ``on_replay`` rebuilds the 200 body from disk on a deduped
        ``Idempotency-Key`` retry (``draft.json`` + ``cache.json::resolution``
        reconstruct both draft verbs exactly). ``start-checkin`` alone passes
        ``dedupe=False``: its ``job_id`` has no disk home, and the ``checkin →
        active`` lifecycle is already the retry guard.
        """
        campaign = self._load_owned_campaign(campaign_id)
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, campaign.root_cycle_id)
        if not (cycle_dir / "index.json").is_file():
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
                # ONE central mapping seam for any applier error that carries an
                # HTTP status — backend-unreachable (503), quota (429), launch
                # (422), conflicts, not-founds. Emit a rejected ack so the audit
                # trail stays on the ledger, then re-raise: main.py's PotterError
                # handler serializes the flat ``ErrorEnvelope``
                # (``docs/specs/m12-api-openapi.yaml``). Per CLAUDE.md root-fix:
                # one site, not one arm per applier; no ``HTTPException`` here.
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
            # Apply rejected by a domain guard (e.g. stub-delete refused);
            # surface to the caller as 409 with the guard's reason while the
            # audit trail (CommandRecord + rejected ack) stays on the ledger.
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
        campaign: Any,
        campaign_id: str,
        cycle_id: str,
        payload_extras: dict[str, Any],
    ) -> Any:
        if kind == "fork-cycle":
            from promptpotter.application.optimization.resume_and_fork import mint_operator_fork

            seed = _parse_cycle_seed(payload_extras.get("seed"))
            # A seed that steers the inner-optimizer model OUTSIDE the origin's
            # allow-list (`allowed_models`; empty = nothing sanctioned = any model
            # steer counts) is the ADR-0005 §4 babysit action — a distinct cap above
            # the RUN-tier fork. A steer to a SANCTIONED model is a clean human fork.
            allowed_models = campaign.config.get("allowed_models") if campaign else None
            steers_disallowed_model = seed is not None and overlay_sets_model_outside_allowed(
                seed.pipeline_overlay, allowed_models
            )
            if steers_disallowed_model and not has_capability(
                self._store.identity, CAMPAIGN_BABYSIT_CAP
            ):
                logger.warning(
                    "fork-cycle disallowed-model steer denied for principal %s (missing %s)",
                    self._acting_principal_id(),
                    CAMPAIGN_BABYSIT_CAP,
                )
                raise NotFoundError("Not found", code="not_found")

            async def _apply_fork() -> None:
                # Mint the operator-steered fork (writes the cycle + seed,
                # retargets the active pointer), THEN launch it. Minting alone is
                # just disk I/O — without the launch the fork sits seeded-but-idle
                # (the old design assumed a manual CLI `resume`, which never comes
                # when steering from the web). One gesture = stop parent → mint →
                # continue optimizing from the edited searchpoint. Pass no
                # spend/halt: the seed's reconciled limits govern at the runner
                # seam (runner/entry.py::_apply_config_overrides).
                new_cycle_id = mint_operator_fork(
                    stores=self._store,
                    campaign_id=campaign_id,
                    cycle_id=cycle_id,
                    from_round=int(payload_extras.get("round", 0)),
                    from_candidate_id=str(payload_extras.get("candidate_id", "")),
                    seed=seed,
                    steered_by=str(payload_extras.get("steered_by", "")),
                )
                await self._apply_start_run(
                    campaign_id=campaign_id,
                    cycle_id=new_cycle_id,
                    kind="resume",
                    halt_at_accuracy=None,
                    spend_budget_usd=None,
                )

            return _apply_fork
        if kind == "step-cycle":
            # Advance N rounds in place then auto-pause — reuses the resume machinery +
            # RunMode's run-scoped stop; the `campaign.step` tier for a delegate without
            # run. `rounds` defaults to 1 (StepCyclePayload).
            rounds = payload_extras.get("rounds", 1)
            steps = int(rounds) if isinstance(rounds, int | float) else 1
            return lambda: self._apply_start_run(
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                kind="resume",
                halt_at_accuracy=None,
                spend_budget_usd=None,
                stop_after_rounds=max(1, steps),
            )
        if kind == "skip-searchpoint":
            return lambda: self._apply_skip_searchpoint(campaign_id, cycle_id)
        if kind == "cleanup-empty-cycles":
            return lambda: self._apply_cleanup_empty(campaign_id, cycle_id)
        if kind == "pause-cycle":
            return lambda: self._apply_pause_cycle(campaign_id, cycle_id)
        if kind == "origin-gate-decision":
            decision = str(payload_extras.get("decision", ""))
            if decision not in ("rescore", "proceed", "abort"):
                raise PayloadInvalidError(
                    "origin-gate-decision requires decision ∈ {rescore, proceed, abort}."
                )
            return lambda: self._apply_origin_gate_decision(campaign_id, cycle_id, decision)
        if kind == "change-spend-budget":
            max_usd = payload_extras.get("max_usd")
            max_tokens = payload_extras.get("max_tokens")
            # An ABSENT ceiling means "leave it untouched"; a PRESENT one that is
            # non-numeric or negative is a typo, not a no-op. Reject it loud — a
            # negative cap halts the run at the next checkpoint, and coercing it to
            # None would drop that ceiling while the other one applies, so the
            # operator believes both landed when only one did.
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
            return lambda: self._apply_change_spend_budget(
                campaign_id, cycle_id, max_usd=usd_val, max_tokens=tok_val
            )
        if kind == "start-run":
            run_kind = str(payload_extras.get("kind", ""))
            halt = payload_extras.get("halt_at_accuracy")
            spend = payload_extras.get("spend_budget_usd")

            async def _apply() -> None:
                await self._apply_start_run(
                    campaign_id=campaign_id,
                    cycle_id=cycle_id,
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
        campaigns = self._store.campaigns
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
                inner_sandbox_root=self._store.projects_root.parent / ".inner",
            )

    def _apply_register_backend(self, payload: dict[str, Any]) -> None:
        """Mint a ``BackendConnection`` from the request payload and persist
        it via ``BackendStore.register``. Duplicate id surfaces as 409."""
        name = str(payload["name"])
        backend_type = str(payload["backend_type"])
        base_url = str(payload["base_url"])
        explicit_id = payload.get("id")
        backend_id = (
            str(explicit_id)
            if isinstance(explicit_id, str) and explicit_id
            else _slugify_backend_id(name)
        )
        if self._store.backends.get(backend_id) is not None:
            raise ConflictError(
                f"Backend '{backend_id}' already exists", details={"backend_id": backend_id}
            )
        self._store.backends.register(
            BackendConnection(
                id=backend_id,
                name=name,
                backend_type=backend_type,
                base_url=base_url.rstrip("/"),
            )
        )

    def _apply_skip_searchpoint(self, campaign_id: str, cycle_id: str) -> None:
        """Write a one-shot ``.runtime/skip.flag``; ``Session.skip_check`` polls it at
        the next per-sample checkpoint, accepts the partial searchpoint, consumes the
        flag, and the cycle keeps running. A manual skip is a human intervention — the
        cycle is marked ``human_intervened`` (no longer purely reproducible)."""
        flag = CycleLayout(self._store.campaigns.cycle_dir(campaign_id, cycle_id)).skip_flag
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")
        self._store.campaigns.mark_human_intervened(
            campaign_id, cycle_id, kind="skip", at=utcnow_iso()
        )

    def _apply_pause_cycle(self, campaign_id: str, cycle_id: str) -> None:
        """Write ``.runtime/pause.flag`` — the single operator-interrupt flag.
        ``Session.pause_check`` polls it at the next checkpoint; the worker then
        exits cleanly and the cycle stays resumable (``_finalize_run`` skips
        terminal marking on ``StopReason.PAUSED``). Resuming is the ``start-run``
        / ``resume`` launcher relaunching from the last completed round — not an
        in-place flag delete, since the worker is gone. Idempotent."""
        flag = CycleLayout(self._store.campaigns.cycle_dir(campaign_id, cycle_id)).pause_flag
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")

    def _apply_origin_gate_decision(self, campaign_id: str, cycle_id: str, decision: str) -> None:
        """Write ``.runtime/gate_decision.json`` (``{decision}``); the runner's
        origin-gate wait-loop polls it (``run_origin_gate``) and acts: ``rescore``
        re-scores force-fresh and re-evaluates the gate in place, ``proceed`` enters
        L1, ``abort`` ends the cycle with ``StopReason.ORIGIN_GATE``. The one
        decision channel all three surfaces write; the runner clears the file after
        consuming it. Last write wins."""
        gate_path = CycleLayout(
            self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        ).gate_decision
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(gate_path, {"decision": decision})

    def _apply_change_spend_budget(
        self,
        campaign_id: str,
        cycle_id: str,
        *,
        max_usd: float | None,
        max_tokens: int | None,
    ) -> None:
        """Write ``.runtime/spend_cap.json`` (``{max_usd, max_tokens}``); the
        round loop's BudgetGate re-reads it every clean round. A ``None`` arg
        leaves that ceiling untouched (merge into the existing file). Setting a
        ceiling to ``0`` halts at the next round boundary; raising above current
        usage releases."""
        cap_path = CycleLayout(self._store.campaigns.cycle_dir(campaign_id, cycle_id)).spend_cap
        cap_path.parent.mkdir(parents=True, exist_ok=True)
        caps: dict[str, float | int] = {}
        existing = read_json_tolerant(cap_path, {})  # missing/malformed → start clean
        if isinstance(existing, dict):
            caps.update(existing)
        if max_usd is not None:
            caps["max_usd"] = max_usd
        if max_tokens is not None:
            caps["max_tokens"] = max_tokens
        write_json(cap_path, caps)

    async def _apply_mint_campaign(self, payload: dict[str, Any]) -> None:
        """Mint a fresh campaign + cycle and spawn the runner in background.

        The 202 returns as soon as ``auto_mint_session`` writes the campaign
        manifest + root cycle index; the run proceeds via JobRegistry. The
        webapp discovers the new ids by polling ``/api/v1/active``.

        ``BackendUnreachableError`` bubbles uncaught to ``_record_and_apply``
        for the central 503 mapping (R2).
        """
        from promptpotter.application.jobs import mint_campaign_command

        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        dataset_name = str(payload.get("dataset_name", ""))
        # Campaign-from-origin rides the check-in path (start-checkin + the
        # draft's reused_origin_id), not this workspace verb — so no
        # origin_override here. Quota (429) / Launch (422) / BackendUnreachable
        # (503) are PotterErrors the central catch in _record_and_apply maps.
        await mint_campaign_command(
            stores=self._store,
            dataset_name=dataset_name,
            job_registry=self._job_registry,
            halt_at_accuracy=_optional_float(payload.get("halt_at_accuracy")),
            spend_budget_usd=_optional_float(payload.get("spend_budget_usd")),
        )

    async def _apply_start_run(
        self,
        *,
        campaign_id: str,
        cycle_id: str,
        kind: str,
        halt_at_accuracy: float | None,
        spend_budget_usd: float | None,
        stop_after_rounds: int | None = None,
    ) -> None:
        """Spawn the runner against an existing cycle. ``kind`` ∈ {new, resume}.
        ``stop_after_rounds`` bounds it to that many rounds in place (``step-round``).

        ``BackendUnreachableError`` bubbles uncaught to ``_record_and_apply``
        for the central 503 mapping (R2).
        """
        from promptpotter.application.jobs import start_run_command

        if self._job_registry is None:
            raise ServiceUnavailableError(
                "job registry not initialised", code="job_registry_unavailable"
            )
        # Quota / Launch / BackendUnreachable are PotterErrors mapped centrally
        # by _record_and_apply — no per-applier arm here.
        await start_run_command(
            stores=self._store,
            job_registry=self._job_registry,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            kind=kind,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
            stop_after_rounds=stop_after_rounds,
        )

    def _apply_cleanup_empty(self, campaign_id: str, cycle_id: str) -> None:
        """Batch-delete every empty-stub sibling under ``root_cycle_id(cycle_id)``;
        leaves-first via two passes. Reasons for skipped entries surface via
        the audit ack detail string."""
        root_id = root_cycle_id(cycle_id)
        _, active_cmp, active_cid = read_active_pointer(
            self._store.tenant_id, projects_root=self._store.projects_root
        )
        deleted_ids: list[str] = []
        for _pass in range(2):
            progress = False
            entries = self._store.campaigns.enumerate_cycles()
            family_ids = [
                e["cycle_id"]
                for e in entries
                if e["campaign_id"] == campaign_id
                and e["cycle_id"] != root_id
                and e["parent_cycle_id"] == root_id
            ]
            for cid in family_ids:
                if cid in deleted_ids:
                    continue
                if campaign_id == active_cmp and cid == active_cid:
                    continue
                deleted, _reason = self._store.campaigns.try_delete_stub_cycle(campaign_id, cid)
                if deleted:
                    deleted_ids.append(cid)
                    progress = True
            if not progress:
                break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _acting_principal_id(self) -> str:
        """The id recorded as a command's issuer. For a delegated sub-principal
        (ADR-0005) that is its own `claims["principal"]`, not the delegator whose
        tenant it acts in — so the audit trail names the real actor."""
        principal = self._store.identity.claims.get("principal")
        if isinstance(principal, str) and principal:
            return principal
        return str(self._store.identity.user_id)

    def _require_capability_for(self, kind: str) -> None:
        """Per-verb capability gate — the one seam every command funnels through.

        Maps the closed command *kind* to its tier capability (``CAP_FOR_KIND``)
        and checks the request identity holds it. Absence raises 404
        (existence-hiding, matching ``deps.require_capability`` — a principal
        without the cap is told the verb does not exist, never 403). A tenant
        owner holds every tier; a delegated sub-principal an attenuated subset.
        """
        cap = CAP_FOR_KIND.get(kind)
        if cap is None or not has_capability(self._store.identity, cap):
            logger.warning(
                "command %r denied for principal %s (missing %s)",
                kind,
                self._acting_principal_id(),
                cap,
            )
            raise NotFoundError("Not found", code="not_found")

    def _load_owned_campaign(self, campaign_id: str) -> Any:
        campaign = self._store.campaigns.load_owned(campaign_id, str(self._store.identity.user_id))
        if campaign is None:
            raise NotFoundError(
                f"Campaign not found: {campaign_id}", code="command_target_not_found"
            )
        return campaign
