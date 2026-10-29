"""A head-to-head minted whole, then its arms launched one by one as the cycles they now are."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.account import account_of
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.application.jobs.launcher.checkin import commit_checkin, start_ready_draft
from promptpotter.application.jobs.launcher.launch import Inline, Launched, launch
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.mint import MintedCycle, arm_declarations, mint_head_to_head
from promptpotter.application.jobs.quota import refuse_fresh_campaigns
from promptpotter.domain.campaign import Arm, Campaign, Compare
from promptpotter.domain.command_kinds import ArmStarted, HeadToHeadStarted
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.dataset_access import declared_backend_type
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True, slots=True)
class MintedHeadToHead:
    """Every arm minted, none launched: ``launches`` admits each in turn, so an ``inline`` caller
    runs one arm before the next is asked for, and a refused launch leaves its arm minted."""

    head_to_head_id: str
    arms: tuple[tuple[str, Campaign], ...]
    stores: Stores
    job_registry: JobRegistry
    inline: Inline | None

    @classmethod
    def of(
        cls,
        minted: Sequence[MintedCycle],
        *,
        stores: Stores,
        job_registry: JobRegistry,
        inline: Inline | None,
    ) -> MintedHeadToHead:
        arms: list[tuple[Arm, Campaign]] = []
        for cycle in minted:
            campaign = stores.campaigns.load_campaign(cycle.campaign_id)
            assert campaign is not None, "an arm is a minted campaign"
            purpose = campaign.declaration.purpose
            assert isinstance(purpose, Compare), "an arm declares its head-to-head"
            arms.append((purpose.arm, campaign))
        return cls(
            arms[0][0].head_to_head_id,
            tuple((arm.arm_key, campaign) for arm, campaign in arms),
            stores,
            job_registry,
            inline,
        )

    async def launches(self) -> AsyncIterator[tuple[str, Launched]]:
        for i, (arm_key, campaign) in enumerate(self.arms):
            try:
                launched = await launch(
                    ExistingCycle(campaign.root_hop, campaign, Budget()),
                    RunMode(),
                    stores=self.stores,
                    job_registry=self.job_registry,
                    inline=self.inline,
                )
            except PotterError as exc:
                # Minted and resumable: what the refusal leaves unrun, named where it is answered.
                exc.details["unlaunched_arms"] = {k: c.campaign_id for k, c in self.arms[i:]}
                raise
            yield arm_key, launched

    async def started(self) -> HeadToHeadStarted:
        """Every arm launched as a server launches one: detached, or queued for a slot."""
        assert self.inline is None, "a terminal runs each arm before the next launches"
        hops = {arm_key: campaign.root_hop for arm_key, campaign in self.arms}
        return HeadToHeadStarted(
            head_to_head_id=self.head_to_head_id,
            arms=[
                ArmStarted(
                    arm_key=arm_key,
                    hop=hops[arm_key],
                    job_id=launched.job.job_id,
                    queued=launched.job.queued,
                )
                async for arm_key, launched in self.launches()
            ],
        )


@dataclass(frozen=True, slots=True)
class CheckinHeadToHead:
    """A Start declaring a head-to-head, acked before its arms mint, as every Start is: the mint
    moves the draft's ledger, the ack's home, into the first arm. Holds the draft's claim until
    its arms are minted; each arm's launch asks for its own slot."""

    claim: DraftClaim
    request: HeadToHeadRequest
    stores: Stores
    job_registry: JobRegistry

    async def minted(self) -> MintedHeadToHead:
        try:
            arms = await commit_checkin(self.stores, self.claim.draft_id, self.request)
        finally:
            self.claim.release()
        # One session minted every arm: its client closes once, and each launch opens its own.
        await arms[0].session.backend_client.aclose()
        return MintedHeadToHead.of(
            arms, stores=self.stores, job_registry=self.job_registry, inline=None
        )


def checkin_head_to_head(
    stores: Stores, job_registry: JobRegistry, claim: DraftClaim, request: HeadToHeadRequest
) -> CheckinHeadToHead:
    """Refused before the ack, so a draft not ready, or arms past the account's ceilings, leaves
    the check-in open and its Start retryable."""
    start_ready_draft(stores, claim.draft_id)
    refuse_minting_arms(stores, job_registry, len(request.arms))
    return CheckinHeadToHead(claim, request, stores, job_registry)


def refuse_minting_arms(stores: Stores, job_registry: JobRegistry, n: int) -> None:
    """Every arm counted against the rate and daily ceilings before any arm is written."""
    with job_registry.admission_gate():
        refuse_fresh_campaigns(
            user=account_of(stores), stores=stores, job_registry=job_registry, campaigns=n
        )


async def mint_dataset_head_to_head(
    dataset: ResolvedDataset,
    declared: CampaignConfig,
    request: HeadToHeadRequest,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
) -> MintedHeadToHead:
    """No slot is held while the arms mint: each launch asks for its own."""
    if (down := await probe_backend(declared_backend_type(dataset.directory))) is not None:
        raise down
    refuse_minting_arms(stores, job_registry, len(request.arms))
    session = await init_services(
        dataset_name=dataset.name, identity=stores.identity, stores=stores
    )
    try:
        minted = await mint_head_to_head(
            session, arm_declarations(session, declared, request), session.bank.samples
        )
    finally:
        await session.backend_client.aclose()
    return MintedHeadToHead.of(minted, stores=stores, job_registry=job_registry, inline=inline)
