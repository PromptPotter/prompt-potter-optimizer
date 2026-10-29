from __future__ import annotations

import inspect

from fastapi import APIRouter
from fastapi.routing import APIRoute

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.commands.origin_resolving import (
    ResolveOriginResponse,
    dispatch_origin_resolution,
)
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.jobs.launcher.checkin import StartCheckinResponse
from promptpotter.application.jobs.launcher.head_to_head import MintedHeadToHead
from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandAcceptedBody,
    CommandKind,
    CommandPayload,
    CompactArchivePayload,
    EditDraftCampaignPayload,
    HeadToHeadStarted,
    MintHeadToHeadPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.presentation.api.deps import IdempotencyKeyDep, JobRegistryDep, StoresDep

commands_router = APIRouter(prefix="/commands", tags=["Commands"])


@commands_router.post(f"/{CommandKind.EDIT_DRAFT_CAMPAIGN}", response_model=DraftCampaignWire)
async def edit_draft_campaign(
    stores: StoresDep, payload: EditDraftCampaignPayload, key: IdempotencyKeyDep
) -> DraftCampaignWire:
    """Sparse-patch a `DraftCampaign` and return its full post-mutation shape."""
    return await dispatch_draft_patch(stores, CommandCall(payload, key))


@commands_router.post(f"/{CommandKind.RESOLVE_ORIGIN}", response_model=ResolveOriginResponse)
async def resolve_origin(
    stores: StoresDep, payload: ResolveOriginPayload, key: IdempotencyKeyDep
) -> ResolveOriginResponse:
    """Run one origin-resolver turn against a draft, synchronously, and return ``{resolution, draft}``."""
    return await dispatch_origin_resolution(stores, CommandCall(payload, key))


@commands_router.post(f"/{CommandKind.START_CHECKIN}", response_model=StartCheckinResponse)
async def start_checkin(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    payload: StartCheckinPayload,
    key: IdempotencyKeyDep,
) -> StartCheckinResponse:
    """Mint the draft's campaign and root cycle, close the draft and detach the loop.

    An incomplete origin answers 422 and the draft stays open.
    """
    started = await dispatch_start_checkin(
        stores, CommandCall(payload, key), job_registry=job_registry
    )
    if isinstance(started, HeadToHeadStarted):
        first = started.arms[0]
        return StartCheckinResponse(job_id=first.job_id, hop=first.hop, head_to_head=started)
    return StartCheckinResponse(job_id=started.job.job_id, hop=started.hop, head_to_head=None)


@commands_router.post(f"/{CommandKind.MINT_HEAD_TO_HEAD}", response_model=HeadToHeadStarted)
async def mint_head_to_head(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    payload: MintHeadToHeadPayload,
    key: IdempotencyKeyDep,
) -> HeadToHeadStarted:
    """Mint every arm, then launch each detached, or queued for a slot. A launch refused after the
    mint names the arms it left unrun under `details.unlaunched_arms`; each resumes by `start-run`."""
    outcome = await CommandDispatcher(stores, job_registry=job_registry).dispatch(
        CommandCall(payload, key)
    )
    minted: MintedHeadToHead = outcome.result
    return await minted.started()


@commands_router.post(f"/{CommandKind.COMPACT_ARCHIVE}", response_model=ArchiveReport)
async def compact_archive(
    stores: StoresDep, payload: CompactArchivePayload, key: IdempotencyKeyDep
) -> ArchiveReport:
    """Compact, restore or purge the measurement archive's cold store; a preview and its apply answer in one shape."""
    outcome = await CommandDispatcher(stores).dispatch(CommandCall(payload, key))
    report: ArchiveReport = outcome.result
    return report


class _Accepting:
    """A kind whose applied form answers nothing: its route's body is the kind's payload model."""

    def __init__(self, model: type[CommandPayload]) -> None:
        keyword = inspect.Parameter.KEYWORD_ONLY
        params = [
            inspect.Parameter("job_registry", keyword, annotation=JobRegistryDep),
            inspect.Parameter("stores", keyword, annotation=StoresDep),
            inspect.Parameter("payload", keyword, annotation=model),
            inspect.Parameter("key", keyword, annotation=IdempotencyKeyDep),
        ]
        self.__signature__ = inspect.Signature(params, return_annotation=CommandAcceptedBody)

    async def __call__(
        self,
        *,
        job_registry: JobRegistryDep,
        stores: StoresDep,
        payload: CommandPayload,
        key: IdempotencyKeyDep,
    ) -> CommandAcceptedBody:
        dispatcher = CommandDispatcher(stores, job_registry=job_registry)
        outcome = await dispatcher.dispatch(CommandCall(payload, key))
        return outcome.accepted


# Each kind is PATHED by its `CommandKind`, so no route names a kind no capability gates and no ledger records.
_TYPED = {route.path for route in commands_router.routes if isinstance(route, APIRoute)}
for _kind in CommandKind:
    if f"/commands/{_kind}" not in _TYPED:
        commands_router.add_api_route(
            f"/{_kind}",
            _Accepting(PAYLOAD_MODEL_FOR_KIND[_kind]),
            methods=["POST"],
            name=_kind.name.lower(),
            response_model=CommandAcceptedBody,
            status_code=202,
        )
