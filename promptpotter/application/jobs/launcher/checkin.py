from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import JsonValue

from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.loaders import cut_bank, definition_source
from promptpotter.application.datasets.startability import upload_source
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.mint_and_start import (
    LaunchRequest,
    commit_upload_definition,
    persist_origin_answer_terms,
)
from promptpotter.application.jobs.mint import (
    OpenedCycle,
    fresh_campaign_id,
    mint,
    refuse_unrunnable,
    resolve_cycle_plan,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    DraftPipeline,
    ServedPipeline,
    draft_config,
    draft_pipeline,
    draft_sends,
    laid_config,
    laid_session,
)
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile
from promptpotter.domain.dataset_source import Source
from promptpotter.domain.job import CampaignLaunch
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.dataset_access import claim_own_definition, own_definition
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from promptpotter.application.datasets.authored import ConfigLayers
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.launcher.opening import Opening


@dataclass(frozen=True, slots=True)
class CheckinStart(LaunchRequest):
    """Carries the claim, never the draft: the claim refuses every edit until the mint closes."""

    claim: DraftClaim
    # The queue's label alone (`DraftFile.dataset_name`); every decision reads the draft.
    dataset_name: str = field()  # a field: the shape's inherited name is no default
    budget: Budget
    # The Start's one backend read, taken where its origin's readiness was checked.
    served: ServedPipeline

    @property
    def holder(self) -> CampaignLaunch:
        return CampaignLaunch(self.dataset_name, draft_id=self.claim.draft_id)

    def connector(self, _stores: Stores) -> Connector:
        return self.served.connector

    def endpoint(self, _stores: Stores) -> str | None:
        return self.served.endpoint

    def declared_budget(self, stores: Stores) -> Budget:
        return draft_config(stores, owned_draft(stores, self.claim.draft_id)).optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return draft_sends(stores, owned_draft(stores, self.claim.draft_id))

    @asynccontextmanager
    async def open(self, opening: Opening) -> AsyncIterator[OpenedCycle]:
        """Nothing before the slot is HELD touches the draft: it lands, mints and closes here."""
        stores, draft_id = opening.stores, self.claim.draft_id
        async with open_checkin(stores, draft_id, self.served, overlays=[{}]) as opened:
            session, config = laid_session(opened.session, opened.layers)
            plan = resolve_cycle_plan(
                session,
                config,
                Optimize(),
                session.bank.samples,
                origin_override=opened.origin_override,
            )
            campaign_id = fresh_campaign_id(session)
            yield await asyncio.to_thread(
                mint, plan, campaign_id=campaign_id, adopting=draft_id, opening=opening
            )


def _land_draft(
    stores: Stores,
    draft: DraftFile,
    pipeline: DraftPipeline,
    overlays: Sequence[Mapping[str, JsonValue]],
) -> dict[str, NodePrompt] | None:
    """An upload's definition lands once its cut passed every mint refusal of each config the
    Start runs (the draft's, under each overlay), so a refused Start takes no slug. One its own
    upload landed is a Start refused after landing: the retry lands the draft again. Answers the
    origin an authored check-in over an existing dataset overrides."""
    params, chain = pipeline.params, pipeline.chain
    basis = draft.basis
    if isinstance(basis, DatasetBasis):
        persist_origin_answer_terms(stores, basis.dataset, draft)
        # An origin authored over an existing slug writes no dataset: only the override carries it.
        # `committed_prompts` rather than the raw map, or the label enumeration is lost.
        return (
            {step: NodePrompt(fields=f) for step, f in draft.committed_prompts(chain).items()}
            if draft.authored_prompts()
            else None
        )
    source = upload_source(draft, basis)
    # The writer's own claim, asked before the mint's refusals: a held name is refused as itself,
    # and one the account holds is new to this upload unless its own earlier landing wrote it.
    own = own_definition(stores, draft.slug)
    claim_own_definition(
        stores,
        draft.slug,
        new=own is not None and definition_source(own) != Source.of(source),
    )
    rows = cut_bank(stores, source)
    for overlay in overlays:
        config = laid_config(
            stores,
            pipeline.config_layers.over(overlay),
            connector=pipeline.connector,
            schema=pipeline.declared,
        ).config
        refuse_unrunnable(
            config,
            select_optimizer(config.optimization),
            rows,
            params,
            chain,
            stores=stores,
            connector=pipeline.connector,
        )
    commit_upload_definition(stores, draft, source=source, pipeline=pipeline)
    return None


@dataclass(frozen=True, slots=True)
class OpenedCheckin:
    """A Start's draft past its landing, with the session and layers every arm of it declares on."""

    draft: DraftFile
    session: Session
    layers: ConfigLayers
    origin_override: dict[str, NodePrompt] | None


@asynccontextmanager
async def open_checkin(
    stores: Stores,
    draft_id: str,
    served: ServedPipeline,
    *,
    overlays: Sequence[Mapping[str, JsonValue]],
) -> AsyncIterator[OpenedCheckin]:
    """*overlays* are what each campaign the Start mints lays over the draft's `optimization`;
    *served* is the Start's one backend read, its readiness checked at the admission."""
    draft = owned_draft(stores, draft_id)
    pipeline = draft_pipeline(stores, draft, served)
    # Off the loop: the landing re-reads the kept upload and writes the draft's definition.
    origin_override = await asyncio.to_thread(_land_draft, stores, draft, pipeline, overlays)
    async with init_services(
        dataset_name=draft.dataset_name, identity=stores.identity, stores=stores, served=served
    ) as session:
        yield OpenedCheckin(draft, session, pipeline.config_layers, origin_override)
