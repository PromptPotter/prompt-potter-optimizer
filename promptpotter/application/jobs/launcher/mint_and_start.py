from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    cut_operator_fork,
    fork_config,
    operator_start,
)
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
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.campaign import Campaign, CampaignCycle
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.command_kinds import ForkCyclePayload
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun, InnerRun
from promptpotter.domain.records.run_records import CycleStart
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.spend import Budget, HeldBudget
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
    from promptpotter.application.jobs.launcher.opening import Opening
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


@dataclass(frozen=True, slots=True)
class Hosted:
    """A host program's own run (`embedded_run.py`), held by the process that filed it: no slot,
    as the host schedules itself; a mint's session on its *program*, its bank *rows*."""

    program: object | None = None
    rows: Sequence[Sample] | None = None


class LaunchRequest:
    """What every launch kind states, and all `launch.py::hold` reads: a kind states only its data,
    and the members it shares with the others are spelled here once. One with no ``at`` mints its
    cycle at its `open`; the rest open the cycle ``at`` names, a fork the one its `stand` cut."""

    __slots__ = ()
    budget: Budget

    @property
    def at(self) -> CampaignCycle | None:
        return None

    @property
    def step_rounds(self) -> int | None:
        return None

    @property
    def campaign(self) -> Campaign | None:
        """What it spends on: its cycle's campaign, none where its open mints one."""
        return None if self.at is None else self.at.campaign

    def armed_budget(self, stores: Stores) -> Budget:
        """The axes an operator named: its campaign's money and its cycle's own."""
        return Budget() if self.at is None else stores.campaigns.armed_budget(self.at)

    @property
    def hosted(self) -> Hosted | None:
        return None

    @property
    def dataset_name(self) -> str:
        raise NotImplementedError

    @property
    def holder(self) -> CampaignLaunch | EmbeddedRun | InnerRun:
        """Slotted unless a host or an enclosing run schedules it."""
        if self.hosted is not None:
            return EmbeddedRun(self.dataset_name)
        return CampaignLaunch(self.dataset_name)

    def connector(self, stores: Stores) -> Connector:
        """Its campaign's, as minted; else its definition's."""
        if self.campaign is not None:
            return connectors.CONNECTORS.get(self.campaign.connector)
        return runnable_definition(resolve_dataset(stores, self.dataset_name)).owner

    def endpoint(self, stores: Stores) -> str | None:
        """The definition's, as the run's session reads it."""
        return runnable_definition(resolve_dataset(stores, self.dataset_name)).endpoint

    def declared_budget(self, stores: Stores) -> Budget:
        raise NotImplementedError

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        raise NotImplementedError

    def open(self, opening: Opening) -> AbstractAsyncContextManager[OpenedCycle]:
        """A mint's job names the cycle before the mint writes any of it (`Opening.name`)."""
        raise NotImplementedError

    def stand(self, _opening: Opening, _held: HeldBudget) -> LaunchRequest:
        """What it opens once admitted, under its admission's gate: itself, unless a kind cuts."""
        return self


@dataclass(frozen=True, slots=True)
class FreshMint(LaunchRequest):
    # Resolved once, where the request is built (`commands/launching.py::mint_campaign`).
    dataset: ResolvedDataset
    budget: Budget
    layers: ConfigLayers
    task_text: str | None
    step_rounds: int | None = None
    hosted: Hosted | None = field(default=None)  # a field: the shape's inherited one is no default

    @property
    def dataset_name(self) -> str:
        return self.dataset.name

    def connector(self, _stores: Stores) -> Connector:
        return runnable_definition(self.dataset).owner

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
    async def open(self, opening: Opening) -> AsyncIterator[OpenedCycle]:
        """The session, past the probe, then the mint: a refused one writes nothing. A host's
        *rows* replace the definition's bank, which the mint labels."""
        stores, hosted = opening.stores, self.hosted or Hosted()
        async with init_services(
            dataset_name=self.dataset_name,
            identity=stores.identity,
            stores=stores,
            program=hosted.program,
        ) as session:
            if hosted.rows is not None:
                bank = Bank.of(hosted.rows)
                stores.tenant_datasets.put_bank(bank)
                session = replace(session, bank=bank)
            session, config = laid_session(session, self.layers)
            yield await mint_framed_cycle(
                session, config, session.bank.samples, task_text=self.task_text, opening=opening
            )


@dataclass(frozen=True, slots=True)
class ExistingCycle(LaunchRequest):
    at: CampaignCycle = field()  # a field: the shape's inherited `at` is no default
    budget: Budget
    step_rounds: int | None = None
    hosted: Hosted | None = field(default=None)

    @property
    def dataset_name(self) -> str:
        return self.at.campaign.dataset_name

    def declared_budget(self, stores: Stores) -> Budget:
        return cycle_config(stores, self.at).config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return campaign_sends(stores, self.at)

    @asynccontextmanager
    async def open(self, opening: Opening) -> AsyncIterator[OpenedCycle]:
        """The cycle on disk, bound and drift-checked, in the process its run runs in."""
        async with bind_cycle_session(opening.stores, self.at) as session:
            plan = resolve_resume(session, self.at.campaign, session.bank.samples)
            yield OpenedCycle(plan.cycle, plan.session, plan.optimizer)


@dataclass(frozen=True, slots=True)
class ForkLaunch(LaunchRequest):
    """An operator's fork of *parent*, admitted on its parent's campaign before any cut: its
    `stand` cuts the child, so a refused fork writes nothing."""

    parent: CampaignCycle
    payload: ForkCyclePayload
    start: CycleStart  # as `parse_cycle_start` froze it; `operator_start` lays it for the cut
    budget: Budget

    @property
    def dataset_name(self) -> str:
        return self.parent.campaign.dataset_name

    @property
    def holder(self) -> CampaignLaunch:
        return CampaignLaunch(self.dataset_name, campaign_id=self.parent.campaign.campaign_id)

    @property
    def campaign(self) -> Campaign:
        return self.parent.campaign

    def armed_budget(self, stores: Stores) -> Budget:
        # A fork inherits no standing budget of its parent's cycle.
        return stores.campaigns.read_campaign_ceiling(self.parent.campaign).budget

    def _config(self, stores: Stores) -> CycleConfig:
        return fork_config(stores, self.parent, operator_start(stores, self.payload, self.start))

    def declared_budget(self, stores: Stores) -> Budget:
        return self._config(stores).config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self._config(stores).config, self.connector(stores))

    def stand(self, opening: Opening, held: HeldBudget) -> ExistingCycle:
        """Cut under its admission's gate, its share landed on the child: no sibling admitted after
        it reads the campaign without that share, and a refusal before here cut nothing."""
        cut = cut_operator_fork(opening.stores, self.payload, self.start)
        opening.land_cut(cut, held)
        return ExistingCycle(self.parent._replace(cycle_id=cut.child.cycle_id), self.budget)


@dataclass(frozen=True, slots=True)
class InnerSeat(LaunchRequest):
    """A seat's inner campaign, planned before its admission: minted at its open where *at* names
    none, else resumed. A share of the enclosing run's book, never a slot."""

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
    async def open(self, opening: Opening) -> AsyncIterator[OpenedCycle]:
        plan, session = self.plan, self.plan.session
        if self.at is None:
            minted = await asyncio.to_thread(
                mint, plan, campaign_id=self.campaign_id, adopting=None, opening=opening
            )
            session = minted.session
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
        campaign=pipeline.config_layers.block(),
        description=draft.confirmed_task(),
        framing=draft_task_context(draft),
        origin=draft.committed_prompts(pipeline.chain),
    )
    persist_origin_answer_terms(stores, draft.slug, draft)


def persist_origin_answer_terms(stores: Stores, slug: str, draft: DraftFile) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.answer_terms and own_definition(stores, slug) is not None:
        stores.tenant_datasets.write_answer_terms(slug, draft.answer_terms)
