from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.datasets.draft_build import draft_task_context
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.wiring import bind_cycle_session, init_services
from promptpotter.application.jobs.launcher.admission import campaign_sends
from promptpotter.application.jobs.mint import (
    CyclePlan,
    OpenedCycle,
    mint,
    mint_framed_cycle,
    resolve_resume,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.pipeline_resolve import cycle_config, laid_config, laid_session
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun, InnerRun
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    own_definition,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.pipeline_resolve import DraftPipeline


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


class LaunchRequest:
    """What every launch kind states, and all `launch.py::hold` reads: a kind states only its data,
    and the members it shares with the others are spelled here once. One with no ``at`` mints its
    cycle at its `open`; the rest open the cycle ``at`` names."""

    __slots__ = ()
    verb: ClassVar[str]
    budget: Budget

    @property
    def at(self) -> CampaignCycle | None:
        return None

    @property
    def step_rounds(self) -> int | None:
        return None

    @property
    def dataset_name(self) -> str:
        raise NotImplementedError

    @property
    def holder(self) -> CampaignLaunch | EmbeddedRun | InnerRun:
        """Slotted unless a kind runs where a host or an enclosing run schedules it."""
        return CampaignLaunch(self.dataset_name)

    def connector(self, stores: Stores) -> str:
        return runnable_definition(resolve_dataset(stores, self.dataset_name)).connector

    def endpoint(self, stores: Stores) -> str | None:
        """The definition's, as the run's session reads it."""
        return runnable_definition(resolve_dataset(stores, self.dataset_name)).endpoint

    def declared_budget(self, stores: Stores) -> Budget:
        raise NotImplementedError

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        raise NotImplementedError

    def open(self, stores: Stores) -> AbstractAsyncContextManager[OpenedCycle]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class FreshMint(LaunchRequest):
    verb: ClassVar[str] = "mint"

    # Resolved once, where the request is built (`commands/launching.py::mint_campaign`).
    dataset: ResolvedDataset
    budget: Budget
    layers: ConfigLayers
    task_text: str | None

    @property
    def dataset_name(self) -> str:
        return self.dataset.name

    def connector(self, _stores: Stores) -> str:
        return runnable_definition(self.dataset).connector

    def endpoint(self, _stores: Stores) -> str | None:
        return runnable_definition(self.dataset).endpoint

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        """Bound to no node: what the mint admits and sends reads no pipeline."""
        return laid_config(
            stores, self.layers, connector=self.connector(stores), schema=None
        ).config

    def declared_budget(self, stores: Stores) -> Budget:
        return self.campaign_config(stores).optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self.campaign_config(stores), self.connector(stores))

    @asynccontextmanager
    async def open(self, stores: Stores) -> AsyncIterator[OpenedCycle]:
        """The session, then the mint: a refused one writes nothing."""
        async with init_services(
            dataset_name=self.dataset_name, identity=stores.identity, stores=stores
        ) as session:
            session, config = laid_session(session, self.layers)
            yield await mint_framed_cycle(
                session,
                config,
                session.bank.samples,
                task_text=self.task_text,
            )


@dataclass(frozen=True, slots=True)
class ExistingCycle(LaunchRequest):
    verb: ClassVar[str] = "start-run"

    at: CampaignCycle = field()  # a field: the shape's inherited `at` is no default
    budget: Budget
    step_rounds: int | None = None

    @property
    def dataset_name(self) -> str:
        return self.at.campaign.dataset_name

    def connector(self, _stores: Stores) -> str:
        return self.at.campaign.connector

    def declared_budget(self, stores: Stores) -> Budget:
        return cycle_config(stores, self.at).config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return campaign_sends(stores, self.at)

    @asynccontextmanager
    async def open(self, stores: Stores) -> AsyncIterator[OpenedCycle]:
        """The cycle on disk, bound and drift-checked: in the admitting process, and again in a
        detached run's own."""
        async with bind_cycle_session(stores, self.at) as session:
            plan = resolve_resume(session, self.at.campaign, session.bank.samples)
            yield OpenedCycle(plan.cycle, plan.session, plan.optimizer)


@dataclass(frozen=True, slots=True)
class EmbeddedMint(LaunchRequest):
    """A host's fresh campaign, minted on the session the host laid and closes."""

    verb: ClassVar[str] = "mint"

    session: Session
    config: CampaignConfig
    budget: Budget
    step_rounds: int | None = None

    @property
    def dataset_name(self) -> str:
        assert self.session.dataset is not None, "a host's session resolved its dataset"
        return self.session.dataset.name

    @property
    def holder(self) -> EmbeddedRun:
        return EmbeddedRun(self.session.measured_dataset)

    def declared_budget(self, _stores: Stores) -> Budget:
        return self.config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self.config, self.connector(stores))

    @asynccontextmanager
    async def open(self, _stores: Stores) -> AsyncIterator[OpenedCycle]:
        yield await mint_framed_cycle(
            self.session, self.config, self.session.bank.samples, task_text=None
        )


@dataclass(frozen=True, slots=True)
class EmbeddedResume(ExistingCycle):
    """A host's resume of a cycle: no slot."""

    @property
    def holder(self) -> EmbeddedRun:
        return EmbeddedRun(self.dataset_name)


@dataclass(frozen=True, slots=True)
class InnerSeat(LaunchRequest):
    """A seat's inner campaign, planned before its admission: minted at its open where *at* names
    none, else resumed. A share of the enclosing run's book, never a slot."""

    verb: ClassVar[str] = "inner"

    plan: CyclePlan
    campaign_id: str
    dataset_name: str = field()  # a field: the shape's inherited `dataset_name` is no default
    at: CampaignCycle | None = field()
    budget: Budget = field(default_factory=Budget)

    @property
    def holder(self) -> InnerRun:
        return InnerRun(self.dataset_name)

    def declared_budget(self, _stores: Stores) -> Budget:
        return self.plan.cycle.config.optimization.budget

    def sends(self, _stores: Stores) -> tuple[Send, ...]:
        # Each send is admitted by its own book AND the ROOT's; its share bounds every checkpoint.
        return ()

    @asynccontextmanager
    async def open(self, _stores: Stores) -> AsyncIterator[OpenedCycle]:
        plan = self.plan
        session = (
            plan.session
            if self.at is not None
            else mint(plan, campaign_id=self.campaign_id, adopting=None).session
        )
        yield OpenedCycle(plan.cycle, session, plan.optimizer)


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
    persist_origin_answer_terms(stores, draft.slug, draft)


def persist_origin_answer_terms(stores: Stores, slug: str, draft: DraftFile) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.answer_terms and own_definition(stores, slug) is not None:
        stores.tenant_datasets.write_answer_terms(slug, draft.answer_terms)
