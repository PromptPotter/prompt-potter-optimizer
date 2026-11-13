from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.datasets.draft_build import draft_task_context
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.wiring import bind_cycle_session, init_services
from promptpotter.application.jobs.launcher.admission import campaign_sends
from promptpotter.application.jobs.mint import (
    OpenedCycle,
    declare,
    mint_framed_cycle,
    resolve_resume,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.pipeline_resolve import cycle_config, laid_config, laid_session
from promptpotter.domain.campaign import Campaign, Optimize
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    own_definition,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.pipeline_resolve import DraftPipeline

logger = logging.getLogger(__name__)


class OriginIncompleteError(PayloadInvalidError):
    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete, nothing minted — unresolved fields: {fields}",
            details={"gaps": [gap.model_dump() for gap in gaps]},
        )


def assert_origin_ready(draft: DraftFile, pipeline: DraftPipeline) -> None:
    readiness = origin_readiness(draft, pipeline)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


@dataclass(frozen=True, slots=True)
class FreshMint:
    verb: ClassVar[str] = "mint"

    # Resolved once, where the request is built (`commands/launching.py::mint_campaign`).
    dataset: ResolvedDataset
    budget: Budget
    layers: ConfigLayers
    task_text: str | None

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    @property
    def step_rounds(self) -> None:
        return None

    @property
    def dataset_name(self) -> str:
        return self.dataset.name

    @property
    def holder(self) -> CampaignLaunch:
        return CampaignLaunch(self.dataset_name)

    def backend_type(self, _stores: Stores) -> str:
        return runnable_definition(self.dataset).backend_type

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        """Bound to no node: what the mint admits and sends reads no pipeline."""
        return laid_config(
            stores, self.layers, connector=self.backend_type(stores), schema=None
        ).config

    def declared_budget(self, stores: Stores) -> Budget:
        return self.campaign_config(stores).optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self.campaign_config(stores), self.backend_type(stores))

    @asynccontextmanager
    async def open(self, stores: Stores) -> AsyncIterator[OpenedCycle]:
        """The session, then the mint: a refused one writes nothing."""
        t0 = time.perf_counter()
        async with init_services(
            dataset_name=self.dataset_name, identity=stores.identity, stores=stores
        ) as session:
            logger.info("mint[%s]: init=%.2fs", self.dataset_name, time.perf_counter() - t0)
            session, config = laid_session(session, self.layers)
            yield await mint_framed_cycle(
                session,
                declare(session, config, purpose=Optimize()),
                session.bank.samples,
                task_text=self.task_text,
            )


@dataclass(frozen=True, slots=True)
class ExistingCycle:
    verb: ClassVar[str] = "start-run"

    hop: CycleHop
    campaign: Campaign
    budget: Budget
    step_rounds: int | None = None

    @property
    def dataset_name(self) -> str:
        return self.campaign.dataset_name

    @property
    def holder(self) -> CampaignLaunch:
        return CampaignLaunch(self.dataset_name)

    def backend_type(self, _stores: Stores) -> str:
        return self.campaign.connector

    def declared_budget(self, stores: Stores) -> Budget:
        return cycle_config(stores, self.campaign, self.hop).config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return campaign_sends(stores, self.hop)

    @asynccontextmanager
    async def open(self, stores: Stores) -> AsyncIterator[OpenedCycle]:
        """The cycle on disk, bound and drift-checked: in the admitting process, and again in a
        detached run's own."""
        async with bind_cycle_session(stores, self.campaign, self.hop) as session:
            plan = resolve_resume(session, self.campaign, session.bank.samples)
            yield OpenedCycle(plan.cycle, plan.session, plan.optimizer)


def commit_upload_definition(
    stores: Stores,
    draft: DraftFile,
    *,
    source: dict[str, Any],
    pipeline: DraftPipeline,
) -> None:
    """Called once the cut *source* names passed every mint refusal: a refused Start takes no slug.
    Writes the definition, its ``campaign`` the draft's layers laid (`ConfigLayers.block`): the
    dataset's block from here on, as a shipped dataset's `dataset.yaml::campaign` is."""
    stores.write_own_definition(
        draft.slug,
        source=source,
        pipeline=pipeline.local,
        campaign=pipeline.layers.block(),
        description=draft.confirmed_task(),
        framing=draft_task_context(draft),
        origin=draft.committed_prompts(pipeline.chain),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftFile) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.candidate_library and own_definition(stores, slug) is not None:
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)
