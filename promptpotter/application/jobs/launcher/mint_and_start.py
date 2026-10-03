"""The server's launcher — admits, mints, then hands the run to its own process
(``run_job.py``); the 202 returns the moment the campaign exists on disk."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptpotter.application.campaign_config import (
    CampaignConfig,
    load_campaign_config,
    merge_config_layers,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.datasets.csv_ingest import Table, materialize_samples
from promptpotter.application.datasets.dataset_replace import recover_pending_replacements
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    committed_pipeline_json,
    split_overlay,
)
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    release_slot,
)
from promptpotter.application.jobs.launcher.draft_build import (
    _build_default_campaign_json,
    _build_task_context,
)
from promptpotter.application.jobs.launcher.run_job import JobSpec, record_launch_stop, spawn_job
from promptpotter.application.jobs.mint import fresh_campaign_id, mint_framed_cycle
from promptpotter.application.jobs.quota import QuotaExceededError
from promptpotter.application.jobs.registry import Job, JobRegistry
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    resolve_campaign_config,
)
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.domain.campaign import ArmRequest
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

logger = logging.getLogger(__name__)


class LaunchError(PayloadInvalidError):
    """Mint-time failure (missing dataset, malformed config) — a ``PayloadInvalidError``, so the
    central seam maps it to 422 and only routes ADDING context catch it explicitly."""


class OriginIncompleteError(PayloadInvalidError):
    """The origin-readiness checklist still has gaps at mint time (422). Carries the blocking
    :class:`FieldGap`s on ``details.gaps``; the draft is left intact for the operator to resolve."""

    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete — unresolved fields: {fields}",
            details={"gaps": [gap.to_wire() for gap in gaps]},
        )


def _assert_origin_ready(draft: DraftCampaign) -> None:
    """The one gate both mint paths run BEFORE anything irreversible — the checklist, not the
    operator, decides, so a false-ready never reaches mint."""
    readiness = origin_readiness(draft)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


def with_optimization(config: CampaignConfig, overrides: Mapping[str, Any]) -> CampaignConfig:
    """*config* under a sparse ``optimization`` override — a mint's, a check-in's, the terminal's —
    validated, with the manifest it then selects resolved through ``resolve_optimizer``."""
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
    dataset_root: Path, *, optimization: Mapping[str, Any]
) -> CampaignConfig:
    """The dataset's own campaign declaration under the ``optimization`` a launch chose — what
    admission reads the budget arms off, since neither that nor a node overlay moves one."""
    config = load_campaign_config(read_campaign_config_file(dataset_campaign_path(dataset_root)))
    return with_optimization(config, optimization) if optimization else config


def build_cycle_config(
    session: Session,
    dataset_root: Path,
    *,
    optimization: Mapping[str, Any],
    pipeline_overlay: dict[str, Any] | None = None,
    pipeline_steps: list[str] | None = None,
) -> CampaignConfig:
    """Load the campaign config for a launch, folding a reused-dataset overlay onto a per-campaign
    SNAPSHOT — one definition for all three launch paths, leaving the shared dataset immutable.

    *pipeline_steps* is the draft's own chain, and it rides ``exclude_nodes`` for the same reason
    the overlay rides a snapshot: on a REUSED dataset nothing writes a `pipeline.yaml`, so
    `pipelines.default` stays whatever the FIRST campaign on that slug committed. The check-in's
    LLM-only / Research+Match toggle reached nothing at all — the operator picked a pipeline and
    the campaign measured another. Excluding the complement is that choice expressed through the
    channel a campaign already has; a second `pipeline_steps` knob would be `exclude_nodes` spelled
    twice (measured: they have identical expressive power, and `filter_to_steps` preserves the
    schema's own order, so a step list cannot even reorder)."""
    campaign_config = dataset_campaign_config(dataset_root, optimization=optimization)
    if pipeline_overlay:
        overrides, narrowing = split_overlay(pipeline_overlay)
        campaign_config = campaign_config.model_copy(
            update={
                "pipeline_overlay": {**campaign_config.pipeline_overlay, **overrides},
                "optimizer_narrowing": {**campaign_config.optimizer_narrowing, **narrowing},
            }
        )
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


async def mint_campaign_command(
    *,
    stores: Stores,
    dataset_name: str,
    job_registry: JobRegistry,
    job: Job,
    limits: LaunchLimits,
    optimization: Mapping[str, Any],
    arm: ArmRequest | None,
    origin_override: dict[str, Any] | None = None,
    pipeline_overlay: dict[str, Any] | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> tuple[str, str, Job]:
    """Mint a fresh campaign + cycle, then start the run in its own process; the caller's 202 goes
    out the moment this returns. ``pipeline_overlay`` is ``None`` for a fresh upload, which commits its own.

    *job* is the accepted launch (``launcher.admission::request_launch``) — already holding a slot,
    or queued for one, in which case the first ``await`` below is the wait."""
    # A crashed version-and-repoint leaves a campaign pointing at a name whose data has moved
    # to `-vN`, so heal before resolving a pin. Cheap no-op when nothing is pending.
    recover_pending_replacements(stores=stores)
    try:
        dataset_root = readable_dataset_dir(stores, dataset_name)
    except DatasetAccessError:
        raise LaunchError(f"dataset not found: {dataset_name!r}") from None

    backend_type = _read_backend_type_from_dataset(dataset_root, dataset_name)

    held = await admit_and_hold(
        stores=stores,
        job_registry=job_registry,
        job=job,
        verb="mint",
        dataset_name=dataset_name,
        backend_type=backend_type,
        backend_url=backend_url,
        requested=limits,
        # The dataset's own declaration, read before the session exists: the overlay that
        # `build_cycle_config` folds on afterwards touches no budget arm. No hop — a fresh mint
        # has no seed and no standing ceiling.
        config=lambda: dataset_campaign_config(dataset_root, optimization=optimization),
        hop=None,
    )

    # SETUP — the ids bind only once the mint resolves; init them so the failure handler can tell
    # "crashed before a cycle existed" (nothing to mark) from "crashed after mint" (mark it).
    campaign_id = cycle_id = ""
    try:
        # The operator waits on this one synchronously (round-0 scoring is already backgrounded),
        # so it is the dominant pre-202 cost and lands on disk beside admission's own line.
        _t0 = time.perf_counter()
        session = await init_services(
            backend_url=backend_url,
            dataset_name=dataset_name,
            identity=stores.identity,
        )
        logger.info("mint[%s]: init_services=%.2fs", dataset_name, time.perf_counter() - _t0)

        # Reused-dataset setup edits ride the overlay onto a per-campaign snapshot;
        # prepare_fresh_cycle freezes the result into the Campaign manifest.
        campaign_config = build_cycle_config(
            session, dataset_root, optimization=optimization, pipeline_overlay=pipeline_overlay
        )

        # The one shared mint prologue — the same seam CLI ``new`` runs inline; the web path
        # adds only the gates + the run's own process.
        minted = await mint_framed_cycle(
            session,
            campaign_config,
            session.samples,
            campaign_id=fresh_campaign_id(session, campaign_config),
            task_text=None,
            arm=arm,
            limits=limits,
            origin_override=origin_override,
        )
        campaign_id, cycle_id = minted.campaign_id, minted.cycle_id
        # Resolve the reservation onto the cycle it now names. Every hop-keyed join reads this —
        # `running_job_for` (how `change-run-limits` reaches the held cap), `reap_cycle_by_id`,
        # the holder readout — and each answers nothing at all against `UNRESOLVED_HOP`.
        job_registry.update_target(
            job.job_id, hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        )

    except BaseException as exc:
        release_slot(job_registry, job.job_id, exc)
        if campaign_id and cycle_id:
            record_launch_stop(
                stores=stores,
                hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
                session_id="",
                exc=exc,
            )
        raise

    spawn_job(
        job_registry,
        JobSpec.of(
            stores=stores,
            job_registry=job_registry,
            job_id=job.job_id,
            hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
            session_id=session.session_id,
            limits=held,
            backend_url=backend_url,
        ),
    )

    logger.info(
        "mint-campaign: minted %s/%s for user %s (job %s)",
        campaign_id,
        cycle_id,
        stores.identity.user_id,
        job.job_id,
    )
    return campaign_id, cycle_id, job


def materialize_and_write_origin(
    stores: Stores, draft: DraftCampaign, *, bank_items: list[dict[str, Any]]
) -> None:
    """Materialize raw bank rows → Samples and write the committed dataset Origin files — the one
    commit body at check-in Start, shared by the CLI inline path and the web detach path."""
    table = Table(headers=draft.headers, rows=tuple(bank_items))
    # Seeded on the slug, so the permutation is re-derivable from the dataset's own name and a
    # DIFFERENT ordering can only exist under a different slug — which is what keeps `sample_id`
    # (a measurement cache key) pointing at the same question for the life of the dataset.
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
        campaign_json=_build_default_campaign_json(draft),
        task_description=draft.raw_task_description,
        prompt_default=draft.committed_prompt_fields(),
        task_context=_build_task_context(draft),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftCampaign) -> None:
    """Scoped to tenant datasets: a reopened repo benchmark is not ours to mutate, and its committed
    library already round-tripped through the draft. A draft with no library is a no-op."""
    if draft.candidate_library and stores.tenant_datasets.slug_exists(slug):
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


async def start_run_command(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    job: Job,
    hop: CycleHop,
    kind: str,
    limits: LaunchLimits,
    stop_after_rounds: int | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> Job:
    """Start a run of an existing cycle in its own process; ``kind`` ∈ ``{"new", "resume"}`` mirrors the two CLI
    verbs. ``stop_after_rounds`` bounds the run in place (``step-round``)."""
    if kind not in ("new", "resume"):
        raise LaunchError(f"start-run kind must be 'new' or 'resume', got {kind!r}")

    # Same guard as the mint path — a resumed cycle must not resolve a pin a crashed Replace
    # left dangling.
    recover_pending_replacements(stores=stores)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise LaunchError(f"campaign not found or not owned: {hop.campaign_id}")

    try:
        dataset_root = readable_dataset_dir(stores, campaign.dataset_name)
    except DatasetAccessError:
        raise LaunchError(f"dataset not found: {campaign.dataset_name!r}") from None
    backend_type = _read_backend_type_from_dataset(dataset_root, campaign.dataset_name)
    dataset_name = campaign.dataset_name

    held = await admit_and_hold(
        stores=stores,
        job_registry=job_registry,
        job=job,
        verb=kind,
        dataset_name=dataset_name,
        backend_type=backend_type,
        backend_url=backend_url,
        requested=limits,
        config=lambda: resolve_campaign_config(stores, campaign, hop),
        hop=hop,
    )

    spawn_job(
        job_registry,
        JobSpec.of(
            stores=stores,
            job_registry=job_registry,
            job_id=job.job_id,
            hop=hop,
            session_id=None,
            limits=held,
            backend_url=backend_url,
            stop_after_rounds=stop_after_rounds,
        ),
    )
    return job


def _read_backend_type_from_dataset(dataset_root: Path, dataset_name: str) -> str:
    """Resolve ``backend_type`` from the dataset's ``pipeline.yaml`` for the preflight. Raises
    :class:`LaunchError` when absent — the launch cannot proceed without it."""
    raw_path = dataset_pipeline_path(dataset_root)
    try:
        raw = read_yaml_optional(raw_path)
    except json.JSONDecodeError as exc:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.yaml is malformed: {exc}") from exc
    if raw is None:
        raise LaunchError(f"dataset {dataset_name!r} has no pipeline.yaml — cannot resolve backend")
    bt = raw.get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.yaml is missing 'backend_type'")
    return bt.lower()


__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "QuotaExceededError",
    "build_cycle_config",
    "dataset_campaign_config",
    "materialize_and_write_origin",
    "mint_campaign_command",
    "persist_origin_candidate_library",
    "start_run_command",
    "with_optimization",
]
