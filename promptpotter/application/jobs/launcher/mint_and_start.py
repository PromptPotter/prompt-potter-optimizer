from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError

from promptpotter.application.campaign_config import (
    CampaignConfig,
    load_campaign_config,
    merge_config_layers,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.csv_ingest import Table, materialize_samples
from promptpotter.application.datasets.draft_build import (
    default_campaign_json,
    draft_task_context,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    committed_pipeline_json,
    split_overlay,
)
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import bind_cycle_session, init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    claim_cycle,
    release_slot,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    fresh_campaign_id,
    mint_framed_cycle,
    refuse_drifted_resume,
    under_declared_record,
)
from promptpotter.application.jobs.quota import QuotaExceededError
from promptpotter.application.jobs.registry import UNRESOLVED_HOP, Job, JobRegistry
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    resolve_campaign_config,
)
from promptpotter.domain.campaign import ArmRequest, Campaign
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    declared_backend_type,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

logger = logging.getLogger(__name__)


class LaunchError(PayloadInvalidError):
    """A mint-time failure: missing dataset, malformed config."""


class OriginIncompleteError(PayloadInvalidError):
    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete — unresolved fields: {fields}",
            details={"gaps": [gap.model_dump() for gap in gaps]},
        )


def assert_origin_ready(draft: DraftCampaign) -> None:
    readiness = origin_readiness(draft)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


def with_optimization(config: CampaignConfig, overrides: Mapping[str, Any]) -> CampaignConfig:
    merged = merge_config_layers(config.model_dump(mode="json"), {"optimization": dict(overrides)})
    try:
        out = load_campaign_config(merged)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise PayloadInvalidError(f"{where}: {first['msg']}", code="optimization_invalid") from exc
    select_optimizer(out.optimization)
    return out


def dataset_campaign_config(
    dataset_root: Path, *, optimization: Mapping[str, Any], declared: CampaignConfig | None = None
) -> CampaignConfig:
    config = declared or load_dataset_campaign_config(dataset_campaign_path(dataset_root))
    return with_optimization(config, optimization) if optimization else config


def build_cycle_config(
    session: Session,
    dataset_root: Path,
    *,
    optimization: Mapping[str, Any],
    declared: CampaignConfig | None = None,
    pipeline_overlay: dict[str, Any] | None = None,
    pipeline_steps: list[str] | None = None,
) -> CampaignConfig:
    campaign_config = dataset_campaign_config(
        dataset_root, optimization=optimization, declared=declared
    )
    if pipeline_overlay:
        overrides, narrowing = split_overlay(pipeline_overlay)
        campaign_config = campaign_config.model_copy(
            update={
                "pipeline_overlay": {**campaign_config.pipeline_overlay, **overrides},
                "optimizer_narrowing": {**campaign_config.optimizer_narrowing, **narrowing},
            }
        )
    # Via `exclude_nodes`: a reused dataset's `pipelines.default` stays the first campaign's.
    if pipeline_steps:
        chosen = set(pipeline_steps)
        campaign_config = campaign_config.model_copy(
            update={
                "exclude_nodes": [
                    n.name for n in session.pipeline_schema.nodes if n.name not in chosen
                ]
            }
        )
    return campaign_config


def _declared_dataset(stores: Stores, dataset_name: str) -> tuple[Path, str]:
    try:
        dataset_root = readable_dataset_dir(stores, dataset_name)
    except DatasetAccessError:
        raise LaunchError(f"dataset not found: {dataset_name!r}") from None
    return dataset_root, declared_backend_type(dataset_root)


@dataclass(frozen=True, slots=True)
class FreshMint:
    verb: ClassVar[str] = "mint"

    dataset_name: str
    limits: LaunchLimits
    optimization: Mapping[str, Any]
    arm: ArmRequest | None
    declared: CampaignConfig | None
    task_text: str | None
    backend_url: str
    backend_id: str

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    def backend(self, stores: Stores) -> tuple[str, str]:
        return _declared_dataset(stores, self.dataset_name)[1], self.backend_url

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        dataset_root, _ = _declared_dataset(stores, self.dataset_name)
        return under_declared_record(
            stores,
            dataset_campaign_config(
                dataset_root, optimization=self.optimization, declared=self.declared
            ),
            self.arm,
        )


@dataclass(frozen=True, slots=True)
class ExistingCycle:
    verb: ClassVar[str] = "start-run"

    hop: CycleHop
    campaign: Campaign
    limits: LaunchLimits

    @property
    def dataset_name(self) -> str:
        return self.campaign.dataset_name

    def backend(self, stores: Stores) -> tuple[str, str]:
        return _declared_dataset(stores, self.dataset_name)[1], self.campaign.backend_url

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        return resolve_campaign_config(stores, self.campaign, self.hop)


async def hold_fresh_mint(
    request: FreshMint, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    """Admit, then session, then mint: nothing is written before admission, so a refusal leaves none."""
    dataset_name = request.dataset_name
    # Bound only after the mint, so the failure path knows whether a cycle exists to stamp.
    hop: CycleHop | None = None
    try:
        dataset_root, _ = _declared_dataset(stores, dataset_name)
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)

        _t0 = time.perf_counter()
        session = await init_services(
            backend_url=request.backend_url,
            backend_id=request.backend_id,
            dataset_name=dataset_name,
            identity=stores.identity,
            stores=stores,
        )
        logger.info("mint[%s]: init_services=%.2fs", dataset_name, time.perf_counter() - _t0)

        campaign_config = build_cycle_config(
            session, dataset_root, optimization=request.optimization, declared=request.declared
        )

        minted = await mint_framed_cycle(
            session,
            campaign_config,
            session.samples,
            campaign_id=fresh_campaign_id(session, campaign_config),
            task_text=request.task_text,
            arm=request.arm,
            limits=request.limits,
        )
        hop = CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
        job_registry.update_target(job.job_id, hop=hop)
        claim_cycle(stores, job_registry, job, hop)

    except BaseException as exc:
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=hop)
        raise

    logger.info(
        "mint: minted %s/%s for user %s (job %s)",
        minted.campaign_id,
        minted.cycle_id,
        stores.identity.user_id,
        job.job_id,
    )
    return HeldRun.of(job_registry, job.job_id, session, held)


def materialize_and_write_origin(
    stores: Stores, draft: DraftCampaign, *, bank_items: list[dict[str, Any]]
) -> None:
    table = Table(headers=draft.headers, rows=tuple(bank_items))
    # Seeded on the slug: `sample_id` is a measurement cache key and must name one question for life.
    order_seed = draft.slug
    samples = materialize_samples(
        table,
        query_col=draft.column_query,
        ground_truth_col=draft.column_ground_truth,
        order_seed=order_seed,
    )
    stores.tenant_datasets.write_committed_dataset(
        draft.slug,
        samples=samples,
        sample_order_seed=order_seed,
        source_file=draft.source_file,
        headers=draft.headers,
        pipeline_json=committed_pipeline_json(draft),
        campaign_json=default_campaign_json(draft),
        task_description=draft.raw_task_description,
        prompt_default=draft.committed_prompt_fields(),
        task_context=draft_task_context(draft),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftCampaign) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.candidate_library and stores.tenant_datasets.slug_exists(slug):
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


async def hold_existing_cycle(
    request: ExistingCycle, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    campaign, hop = request.campaign, request.hop
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)
        session, campaign_config = await bind_cycle_session(stores, campaign, hop)
        refuse_drifted_resume(session, campaign_config, campaign, hop)
    except BaseException as exc:
        # `hop=None`: a refusal stamps nothing on the cycle, so it stays as resumable as it was.
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=None)
        raise
    return HeldRun(
        job_registry=job_registry,
        job_id=job.job_id,
        session=session,
        campaign_config=campaign_config,
        limits=held,
    )


__all__ = [
    "ExistingCycle",
    "FreshMint",
    "LaunchError",
    "OriginIncompleteError",
    "QuotaExceededError",
    "assert_origin_ready",
    "build_cycle_config",
    "dataset_campaign_config",
    "hold_existing_cycle",
    "hold_fresh_mint",
    "materialize_and_write_origin",
    "persist_origin_candidate_library",
    "with_optimization",
]
