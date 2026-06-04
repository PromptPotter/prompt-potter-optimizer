"""``CommandDispatcher`` — sole writer of ``CommandRecord`` at the API seam.

Validates the inbound HTTP command, opens the target ledger, dedupes by
``Idempotency-Key``, appends one ``CommandRecord`` through
``emit_command``, applies inline, then appends ``CommandAckRecord``
through ``emit_command_ack`` once applied.

Three dispatch shapes:

- ``dispatch_lifecycle`` — campaign lifecycle commands (archive / delete /
  unarchive). Target is a campaign; ``CommandRecord`` lands on the
  campaign's root cycle ledger. No ``Expected-Version``.

- ``dispatch_cycle_command`` — cycle-scoped sanctioned-POST migrations
  (fork-cycle, stop-cycle, delete-cycle, cleanup-empty-cycles). Target is
  a specific cycle; ``CommandRecord`` lands on that cycle's ledger.
  ``Expected-Version`` validated when present (v0 relaxation per the
  ``ExpectedVersion`` parameter component note in
  ``docs/specs/m12-api-openapi.yaml``).

- ``dispatch_workspace_command`` — workspace-scoped backend commands
  (register-backend, sync-backend-experiments). Target is the tenant
  workspace, not any cycle; ``CommandRecord`` lands on the workspace
  ledger at ``projects/{tenant}/.workspace/events.jsonl`` per the §0
  Persistence sibling amendment. No ``Expected-Version``.

Closed inbound set: ``docs/specs/m12-api-openapi.yaml``. Permanent contract:
``docs/adr/0001-m12-control-plane.md``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from promptpotter import connectors
from promptpotter.config.settings import settings
from promptpotter.connectors import BackendUnreachableError
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import CommandRecord, ForkSeed
from promptpotter.infrastructure.backend import BackendClient
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
from promptpotter.infrastructure.store.base import write_json
from promptpotter.infrastructure.store.paths import root_cycle_id
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

__all__ = ["CommandAcceptedBody", "CommandDispatcher"]


LifecycleKind = Literal["archive-campaign", "delete-campaign", "unarchive-campaign"]
_LIFECYCLE_STATUS: dict[LifecycleKind, str] = {
    "archive-campaign": "archived",
    "delete-campaign": "deleted",
    "unarchive-campaign": "active",
}

CycleScopedKind = Literal[
    "fork-cycle",
    "stop-cycle",
    "delete-cycle",
    "cleanup-empty-cycles",
    "pause-cycle",
    "resume-cycle",
    "change-spend-budget",
    "start-run",
]
WorkspaceBackendKind = Literal["register-backend", "sync-backend-experiments", "mint-campaign"]

Applier = Callable[[], Awaitable[None]] | Callable[[], None]


class CommandAcceptedBody(BaseModel):
    """The 202 response shape declared in ``m12-api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    correlation_id: str = Field(description="Echo of the request's `Idempotency-Key`.")
    ledger_sequence: int = Field(
        description="Offset at which the `CommandRecord` was appended.", ge=0
    )


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
        client_metadata: dict[str, Any] | None = None,
    ) -> CommandAcceptedBody:
        """Workspace-scoped inline-apply dispatch for campaign lifecycle commands.

        Owner-gated by ``Stores.identity``; cross-user reads return 404, not
        403 (existence-leak gate)."""
        campaign = self._load_owned_campaign(campaign_id)
        root_dir = self._store.campaigns.cycle_dir(campaign_id, campaign.root_cycle_id)
        if not (root_dir / "index.json").is_file():
            raise HTTPException(
                404,
                {
                    "error": "command_target_not_found",
                    "message": f"Root cycle dir missing for campaign {campaign_id}",
                },
            )

        ledger = CycleEventLog.open(CycleDir(root_dir))
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, "reason": reason},
            idempotency_key=idempotency_key,
            client_metadata=client_metadata,
            applier=lambda: self._apply_lifecycle(kind, campaign_id, reason),
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
        client_metadata: dict[str, Any] | None = None,
    ) -> CommandAcceptedBody:
        """Cycle-scoped inline-apply dispatch. ``Expected-Version`` validated
        when present; absent header skips the check (v0 relaxation)."""
        campaign = self._load_owned_campaign(campaign_id)
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        if not (cycle_dir / "index.json").is_file():
            raise HTTPException(
                404,
                {
                    "error": "command_target_not_found",
                    "message": f"cycle not found: {campaign_id}/{cycle_id}",
                },
            )

        ledger = CycleEventLog.open(CycleDir(cycle_dir))
        if expected_version is not None and ledger.next_offset != expected_version:
            raise HTTPException(
                409,
                {
                    "error": "version_conflict",
                    "message": (
                        f"cycle {cycle_id} is at offset {ledger.next_offset}, "
                        f"client expected {expected_version}"
                    ),
                    "details": {
                        "expected_version": expected_version,
                        "actual_version": ledger.next_offset,
                    },
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
                client_metadata=client_metadata,
            )

        applier = self._build_cycle_applier(kind, campaign, campaign_id, cycle_id, payload_extras)
        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload={"campaign_id": campaign_id, "cycle_id": cycle_id, **payload_extras},
            idempotency_key=idempotency_key,
            client_metadata=client_metadata,
            applier=applier,
        )

    async def _dispatch_delete_cycle(
        self,
        *,
        campaign: Any,
        campaign_id: str,
        cycle_id: str,
        idempotency_key: str,
        client_metadata: dict[str, Any] | None,
    ) -> CommandAcceptedBody:
        """delete-cycle writes its audit trail on the campaign's root cycle
        ledger — the target cycle's own ledger goes away as part of the apply."""
        _, active_campaign, active_cycle = read_active_pointer(
            self._store.tenant_id, projects_root=self._store.projects_root
        )
        if active_campaign == campaign_id and active_cycle == cycle_id:
            raise HTTPException(
                409,
                {
                    "error": "version_conflict",
                    "message": f"refusing to delete {cycle_id}: active cycle — switch first",
                },
            )

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
            client_metadata=client_metadata,
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
        client_metadata: dict[str, Any] | None = None,
    ) -> CommandAcceptedBody:
        """Tenant-scoped inline-apply dispatch — backend-registry mutations
        that have no cycle target. ``CommandRecord`` lands on the workspace
        ledger (``projects/{tenant}/.workspace/events.jsonl``) per the §0
        Persistence sibling. No ``Expected-Version``."""
        ledger = CycleEventLog.open_workspace(WorkspaceDir(self._store.base_dir))
        applier: Applier
        if kind == "register-backend":
            applier = lambda: self._apply_register_backend(payload)  # noqa: E731
        elif kind == "sync-backend-experiments":

            async def applier() -> None:
                await self._apply_sync_backend_experiments(payload)
        else:
            assert kind == "mint-campaign"

            async def applier() -> None:
                await self._apply_mint_campaign(payload)

        return await self._record_and_apply(
            ledger=ledger,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
            client_metadata=client_metadata,
            applier=applier,
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
        client_metadata: dict[str, Any] | None,
        applier: Applier,
    ) -> CommandAcceptedBody:
        existing = _find_idempotent_command(ledger, idempotency_key)
        if existing is not None:
            return CommandAcceptedBody(
                command_id=existing.command_id,
                correlation_id=idempotency_key,
                ledger_sequence=existing.offset,
            )

        command_id = str(uuid.uuid4())
        token = set_cycle_ledger(ledger)
        try:
            offset = emit_command(
                command_id=command_id,
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
                issued_by_user_id=str(self._store.identity.user_id),
                client_metadata=client_metadata or {},
            )
            ack_status: Literal["applied", "rejected"] = "applied"
            ack_detail = ""
            try:
                result = applier()
                if asyncio.iscoroutine(result):
                    await result
            except _DeleteCycleRejectedError as exc:
                ack_status = "rejected"
                ack_detail = exc.reason
            except BackendUnreachableError as exc:
                # Central 503 mapping for any applier whose preflight reported
                # the backend down. Per CLAUDE.md root-fix: one site, not one
                # per applier. The ``ErrorEnvelope`` shape is declared in
                # ``docs/specs/m12-api-openapi.yaml::components.responses.BackendUnreachable``.
                emit_command_ack(command_id=command_id, status="rejected", detail=str(exc))
                raise HTTPException(
                    503,
                    {
                        "error": "backend_unreachable",
                        "message": str(exc),
                        "details": {
                            "backend_type": exc.backend_type,
                            "backend_url": exc.backend_url,
                        },
                    },
                ) from exc
            except HTTPException:
                # Apply-time guards that should bubble (e.g. fork dir conflict,
                # backend-already-exists, backend-not-found, upstream 502)
                # re-raise to the router; emit ack as rejected first.
                emit_command_ack(command_id=command_id, status="rejected", detail="")
                raise
            except Exception as exc:
                logger.exception("apply failed for %s", kind)
                ack_status = "rejected"
                ack_detail = str(exc)
            emit_command_ack(command_id=command_id, status=ack_status, detail=ack_detail)
        finally:
            reset_cycle_ledger(token)

        if ack_status == "rejected":
            # Apply rejected by a domain guard (e.g. stub-delete refused);
            # surface to the caller as 409 with the guard's reason while the
            # audit trail (CommandRecord + rejected ack) stays on the ledger.
            raise HTTPException(
                409,
                {
                    "error": "version_conflict",
                    "message": f"command {kind} rejected: {ack_detail}",
                    "details": {"command_id": command_id, "reason": ack_detail},
                },
            )

        return CommandAcceptedBody(
            command_id=command_id,
            correlation_id=idempotency_key,
            ledger_sequence=offset if offset is not None else 0,
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

            seed = _parse_fork_seed(payload_extras.get("seed"))

            async def _apply_fork() -> None:
                # Mint the operator-steered fork (writes the cycle + seed,
                # retargets the active pointer), THEN launch it. Minting alone is
                # just disk I/O — without the launch the fork sits seeded-but-idle
                # (the old design assumed a manual CLI `resume`, which never comes
                # when steering from the web). One gesture = stop parent → mint →
                # continue optimizing from the edited searchpoint. Pass no
                # spend/halt: the seed's reconciled limits govern at the runner
                # seam (runner/entry.py::_apply_limit_overrides).
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
        if kind == "stop-cycle":
            return lambda: self._apply_stop_cycle(campaign_id, cycle_id)
        if kind == "cleanup-empty-cycles":
            return lambda: self._apply_cleanup_empty(campaign_id, cycle_id)
        if kind == "pause-cycle":
            return lambda: self._apply_pause_cycle(campaign_id, cycle_id)
        if kind == "resume-cycle":
            return lambda: self._apply_resume_cycle(campaign_id, cycle_id)
        if kind == "change-spend-budget":
            max_usd = payload_extras.get("max_usd")
            if not isinstance(max_usd, int | float) or max_usd < 0:
                raise HTTPException(
                    422,
                    {
                        "error": "payload_invalid",
                        "message": "payload.max_usd must be a non-negative number.",
                    },
                )
            return lambda: self._apply_change_spend_budget(campaign_id, cycle_id, float(max_usd))
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
        raise HTTPException(  # pragma: no cover — caller validates kind
            422,
            {
                "error": "payload_invalid",
                "message": f"no applier wired for cycle-scoped kind {kind!r}",
            },
        )

    def _apply_lifecycle(self, kind: LifecycleKind, campaign_id: str, reason: str) -> None:
        self._store.campaigns.mark_campaign_lifecycle(
            campaign_id,
            lifecycle_status=_LIFECYCLE_STATUS[kind],
            lifecycle_changed_at=utcnow_iso(),
            lifecycle_reason=reason,
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
            raise HTTPException(
                409,
                {
                    "error": "version_conflict",
                    "message": f"Backend '{backend_id}' already exists",
                    "details": {"backend_id": backend_id},
                },
            )
        self._store.backends.register(
            BackendConnection(
                id=backend_id,
                name=name,
                backend_type=backend_type,
                base_url=base_url.rstrip("/"),
            )
        )

    async def _apply_sync_backend_experiments(self, payload: dict[str, Any]) -> None:
        """Pull experiments from the backend's API into the local store; bump
        ``last_synced_at`` on success. Unknown backend ⇒ 404; upstream
        failures ⇒ 502 (both ride the ``HTTPException`` rejection path)."""
        backend_id = str(payload["backend_id"])
        backend = self._store.backends.get(backend_id)
        if backend is None:
            raise HTTPException(
                404,
                {
                    "error": "command_target_not_found",
                    "message": f"Backend '{backend_id}' not registered",
                },
            )
        connector = connectors.get(backend.backend_type)
        client = BackendClient(
            backend.base_url,
            wire_adapter=connector.wire_adapter,
            session=connector.session_factory(),
            execution=connector.execution,
            auth_token=settings.TERMNORM_TOKEN or None,
        )
        try:
            await client.sync_experiments(self._store, backend_id)
        except Exception as exc:
            raise HTTPException(
                502,
                {
                    "error": "payload_invalid",
                    "message": f"Failed to sync from {backend.base_url}: {exc}",
                },
            ) from exc
        backend.last_synced_at = utcnow_iso()
        self._store.backends.update(backend)

    def _apply_stop_cycle(self, campaign_id: str, cycle_id: str) -> None:
        """Write ``.runtime/stop.flag``; ``Session.stop_check`` polls at the
        next checkpoint and exits cleanly. Idempotent."""
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        runtime_dir = cycle_dir / ".runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        flag = runtime_dir / "stop.flag"
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")

    def _apply_pause_cycle(self, campaign_id: str, cycle_id: str) -> None:
        """Write ``.runtime/pause.flag``; ``Session.pause_check`` polls at
        each round boundary and blocks while present. ``stop.flag`` overrides
        during a pause (operator-requested exit always wins). Idempotent."""
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        runtime_dir = cycle_dir / ".runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        flag = runtime_dir / "pause.flag"
        flag.write_text(f"requested_at={utcnow_iso()}\n", encoding="utf-8")

    def _apply_resume_cycle(self, campaign_id: str, cycle_id: str) -> None:
        """Remove ``.runtime/pause.flag``; the round loop's pause wait-loop
        exits on the next 2 s tick. Idempotent (missing flag is a no-op)."""
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        flag = cycle_dir / ".runtime" / "pause.flag"
        flag.unlink(missing_ok=True)

    def _apply_change_spend_budget(self, campaign_id: str, cycle_id: str, max_usd: float) -> None:
        """Write ``.runtime/spend_cap.json``; the round loop's
        ``spend_cap_probe`` re-reads it every clean round. Setting ``0`` halts
        at the next round boundary; raising above current spend releases."""
        cycle_dir = self._store.campaigns.cycle_dir(campaign_id, cycle_id)
        runtime_dir = cycle_dir / ".runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        write_json(runtime_dir / "spend_cap.json", {"max_usd": max_usd})

    async def _apply_mint_campaign(self, payload: dict[str, Any]) -> None:
        """Mint a fresh campaign + cycle and spawn the runner in background.

        The 202 returns as soon as ``auto_mint_session`` writes the campaign
        manifest + root cycle index; the run proceeds via JobRegistry. The
        webapp discovers the new ids by polling ``/api/v1/active``.

        ``BackendUnreachableError`` bubbles uncaught to ``_record_and_apply``
        for the central 503 mapping (R2).
        """
        from promptpotter.application.jobs import (
            LaunchError,
            QuotaExceededError,
            mint_campaign_command,
        )

        if self._job_registry is None:
            raise HTTPException(503, {"error": "job_registry_unavailable"})
        dataset_name = str(payload.get("dataset_name", ""))
        try:
            await mint_campaign_command(
                stores=self._store,
                dataset_name=dataset_name,
                job_registry=self._job_registry,
                halt_at_accuracy=_optional_float(payload.get("halt_at_accuracy")),
                spend_budget_usd=_optional_float(payload.get("spend_budget_usd")),
            )
        except QuotaExceededError as exc:
            raise HTTPException(429, {"error": exc.code, "message": str(exc)}) from exc
        except LaunchError as exc:
            raise HTTPException(422, {"error": "payload_invalid", "message": str(exc)}) from exc

    async def _apply_start_run(
        self,
        *,
        campaign_id: str,
        cycle_id: str,
        kind: str,
        halt_at_accuracy: float | None,
        spend_budget_usd: float | None,
    ) -> None:
        """Spawn the runner against an existing cycle. ``kind`` ∈ {new, resume}.

        ``BackendUnreachableError`` bubbles uncaught to ``_record_and_apply``
        for the central 503 mapping (R2).
        """
        from promptpotter.application.jobs import (
            LaunchError,
            QuotaExceededError,
            start_run_command,
        )

        if self._job_registry is None:
            raise HTTPException(503, {"error": "job_registry_unavailable"})
        try:
            await start_run_command(
                stores=self._store,
                job_registry=self._job_registry,
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                kind=kind,
                halt_at_accuracy=halt_at_accuracy,
                spend_budget_usd=spend_budget_usd,
            )
        except QuotaExceededError as exc:
            raise HTTPException(429, {"error": exc.code, "message": str(exc)}) from exc
        except LaunchError as exc:
            raise HTTPException(422, {"error": "payload_invalid", "message": str(exc)}) from exc

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
    def _load_owned_campaign(self, campaign_id: str) -> Any:
        campaign = self._store.campaigns.load_campaign(campaign_id)
        if campaign is None or campaign.owner_user_id != str(self._store.identity.user_id):
            raise HTTPException(
                404,
                {
                    "error": "command_target_not_found",
                    "message": f"Campaign not found: {campaign_id}",
                },
            )
        return campaign


class _DeleteCycleRejectedError(Exception):
    """Internal signal: ``try_delete_stub_cycle`` returned ``(False, reason)``.

    ``_record_and_apply`` translates this into a rejected ack + 409 response
    so the audit trail stays on the ledger and the caller gets the guard's
    reason in the error envelope."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _IdempotentMatch(BaseModel):
    model_config = {"frozen": True}
    command_id: str
    offset: int


def _optional_float(raw: object) -> float | None:
    """Best-effort cast for optional `mint-campaign` / `start-run` knobs."""
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _parse_fork_seed(raw: object) -> ForkSeed:
    """Validate the required ``fork-cycle`` seed into a typed :class:`ForkSeed`.

    Every operator fork is `operator_steered` and carries a seed (the edited
    searchpoint + reconciled limits). A missing or malformed seed is a 422 (the
    typed schema is the contract; `LimitOverrides` bounds ride
    `m12-api-openapi.yaml`)."""
    if not isinstance(raw, dict):
        raise HTTPException(
            422, {"error": "payload_invalid", "message": "payload.seed (object) is required."}
        )
    try:
        return ForkSeed.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            422, {"error": "payload_invalid", "message": f"payload.seed invalid: {exc}"}
        ) from exc


def _slugify_backend_id(name: str) -> str:
    """Lowercase + collapse non-alphanumerics into hyphens; strip ends.

    Mirrors the auto-derivation that ``POST /backends`` used pre-migration;
    documented in ``RegisterBackendPayload.id`` ("auto-derived from `name`
    when omitted")."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def _find_idempotent_command(
    ledger: CycleEventLog, idempotency_key: str
) -> _IdempotentMatch | None:
    """Scan the ledger for a prior ``CommandRecord`` with the same key.

    O(n) over the cycle ledger; acceptable for the lifecycle + sanctioned-POST
    paths (a cycle ledger holds thousands of records, not millions, and
    commands are operator-paced)."""
    for offset, record in enumerate(ledger.iter()):
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            return _IdempotentMatch(command_id=record.command_id, offset=offset)
    return None
