from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.datasets.authored import (
    load_dataset_campaign_config,
    with_optimization,
)
from promptpotter.application.datasets.draft_build import draft_task_context
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.wiring import bind_cycle_session, init_services
from promptpotter.application.jobs.coupon import Send
from promptpotter.application.jobs.launcher.admission import campaign_sends
from promptpotter.application.jobs.mint import (
    OpenedCycle,
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
    resolve_resume,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.domain.campaign import Campaign, Optimize
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch
from promptpotter.domain.spend import Budget
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
    optimization: Mapping[str, Any]
    declared: CampaignConfig | None
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

    def campaign_config(self) -> CampaignConfig:
        declared = self.declared or load_dataset_campaign_config(self.dataset)
        return with_optimization(declared, self.optimization)

    def declared_budget(self, _stores: Stores) -> Budget:
        return self.campaign_config().optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self.campaign_config(), self.backend_type(stores))

    async def open(self, stores: Stores) -> OpenedCycle:
        """The session, then the mint: a refused one writes nothing."""
        t0 = time.perf_counter()
        session = await init_services(
            dataset_name=self.dataset_name, identity=stores.identity, stores=stores
        )
        logger.info("mint[%s]: init_services=%.2fs", self.dataset_name, time.perf_counter() - t0)
        return await mint_framed_cycle(
            session,
            declare(session, self.campaign_config(), purpose=Optimize()),
            session.bank.samples,
            campaign_id=fresh_campaign_id(session),
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

    async def open(self, stores: Stores) -> OpenedCycle:
        """The cycle on disk, bound and drift-checked: in the admitting process, and again in a
        detached run's own."""
        session = await bind_cycle_session(stores, self.campaign, self.hop)
        plan = resolve_resume(session, self.campaign, session.bank.samples)
        return OpenedCycle(plan.cycle, plan.session, plan.optimizer)


def commit_upload_definition(
    stores: Stores,
    draft: DraftFile,
    *,
    source: dict[str, Any],
    pipeline: DraftPipeline,
) -> None:
    """Called once the cut *source* names passed every mint refusal: a refused Start takes no slug.
    Writes the two halves its check-in read off *pipeline*."""
    stores.write_own_definition(
        draft.slug,
        source=source,
        pipeline=pipeline.local,
        campaign=pipeline.campaign.model_dump(mode="json", exclude_defaults=True),
        description=draft.confirmed_task(),
        framing=draft_task_context(draft),
        origin=draft.committed_prompts(pipeline.chain),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftFile) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.candidate_library and own_definition(stores, slug) is not None:
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)
