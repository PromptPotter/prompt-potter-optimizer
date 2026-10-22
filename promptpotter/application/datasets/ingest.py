from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.account import record_upload
from promptpotter.application.datasets.authored import read_authored_dataset
from promptpotter.application.datasets.draft_build import overlay_from_campaign_config
from promptpotter.application.datasets.draft_campaign import (
    PREVIEW_ROWS,
    DraftCampaign,
    OptimizationOverrides,
    default_slug_from_filename,
    new_draft,
    scoring_choice,
)
from promptpotter.application.datasets.origin_readiness import save_checkin_draft
from promptpotter.application.datasets.prompts import origin_prompts
from promptpotter.application.datasets.startability import (
    checkin_bank,
    labelled_rows,
    offered_dataset,
)
from promptpotter.application.datasets.tabular_ingest import (
    IngestError,
    closed_label_set,
    file_extension,
    read_tabular,
)
from promptpotter.application.origin_listing import canonical_origin_campaign
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.connectors.protocol import PROBE_WORKLOAD
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import refresh_model_capabilities
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import ConflictError, NotFoundError
from promptpotter.shared.identity import CAMPAIGN_CREATE_CAP, require_capability

# Enforced by the web boundary only; the CLI relies on the per-row cap in ``read_tabular``.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

logger = logging.getLogger(__name__)


async def fetch_backend_nodes(connector_name: str) -> dict[str, Any]:
    connector = connectors.get(connector_name)
    if connector.execution == "in_process":
        return {}
    client = BackendClient(connector, workload=PROBE_WORKLOAD)
    try:
        declared = await client.fetch_pipeline()
    except BackendUnreachableError as exc:
        # Served as `schema_source: unreachable`; a backend answering no schema raises past it.
        logger.info("check-in could not read %s pipeline schema: %s", connector_name, exc)
        return {}
    finally:
        await client.aclose()
    return dict(declared["nodes"])


async def refresh_capabilities(stores: Stores) -> None:
    await refresh_model_capabilities(Path(stores.base_dir))


async def _open_checkin(stores: Stores) -> None:
    """Every check-in ingress starts here: the gate is asked before anything is kept or fetched."""
    require_capability(stores.identity, CAMPAIGN_CREATE_CAP, subject="check-in mint")
    await refresh_capabilities(stores)


def _column_label_sets(
    headers: list[str], rows: list[dict[str, str]]
) -> dict[str, tuple[str, ...]]:
    n = len(rows)
    return {
        header: labels
        for header in headers
        if (labels := closed_label_set((row.get(header, "") for row in rows), n_rows=n))
    }


class SlugTakenError(ConflictError):
    def __init__(self, slug: str, suggested: str) -> None:
        self.slug = slug
        self.suggested = suggested
        super().__init__(
            f"A dataset named '{slug}' already exists.",
            code="slug_collision",
            details={"slug": slug, "suggested_slug": suggested},
        )


async def ingest_draft(
    *,
    stores: Stores,
    blob: bytes,
    filename: str,
    slug: str | None = None,
) -> DraftCampaign:
    await _open_checkin(stores)
    kept = stores.tenant_datasets.keep_upload(blob, file_extension(filename))
    record_upload(stores, filename=filename, kept=kept)
    table = read_tabular(blob, filename)
    base_slug = (slug or default_slug_from_filename(filename or "upload")).lower()
    try:
        validate_dataset_name(base_slug)
    except ValueError as exc:
        raise IngestError(reason="bad_slug", message=str(exc)) from None
    # Raised before the draft is kept, so a collision leaves no orphan.
    if stores.tenant_datasets.slug_exists(base_slug):
        raise SlugTakenError(base_slug, stores.tenant_datasets.suggest_free_slug(base_slug))
    backend_nodes = await fetch_backend_nodes(DEFAULT_CONNECTOR)

    preview = [dict(row) for row in table.rows[:PREVIEW_ROWS]]
    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        owner_user_id=str(stores.identity.user_id),
        slug=base_slug,
        n_samples=len(table.rows),
        sample_preview=preview,
        headers=list(table.headers),
        source_file=filename or "",
        column_label_sets=_column_label_sets(list(table.headers), list(table.rows)),
    )
    stores.drafts.write_upload(draft.draft_id, kept.name)
    return save_checkin_draft(stores, draft.patch(backend_nodes=backend_nodes))


async def draft_from_dataset(
    *,
    stores: Stores,
    dataset_name: str,
    overrides: dict[str, Any] | None = None,
    origin_campaign: Campaign | None = None,
) -> DraftCampaign:
    await _open_checkin(stores)
    dataset_dir = offered_dataset(stores, dataset_name).directory
    bank = checkin_bank(stores, dataset_name)
    rows = labelled_rows(bank.samples)

    declared = backend_type_of_dataset(stores, dataset_name)
    # An origin reuse reads the node declaration off ITS connector, not the shared file's.
    backend_nodes = await fetch_backend_nodes(
        origin_campaign.connector if origin_campaign is not None else declared
    )

    authored = read_authored_dataset(dataset_dir)
    cc = authored.campaign_config
    task = authored.task_description
    matcher, dials = scoring_choice(cc.scoring)
    connector = declared or DEFAULT_CONNECTOR
    pipeline_overlay = authored.pipeline_nodes
    if origin_campaign is not None:
        # A merge, not a replacement: the origin's declaration names only what its layers set.
        pipeline_overlay = merge_node_blocks(
            pipeline_overlay,
            overlay_from_campaign_config(resolve_campaign_config(stores, origin_campaign, None)),
        )

    authored_prompts = {
        node: dict(prompt.fields)
        for node, prompt in origin_prompts(dataset_dir, authored.active_steps).items()
    }

    slug = dataset_name.lower()
    # The check-in model reads the preview's labels, so no bench row may reach it.
    preview = [
        {"query": s.query, "ground_truth": str(s.ground_truth)}
        for s in partition_bank(bank.samples, cc.dataset_split, seed=cc.run_seed).search
    ]

    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        owner_user_id=str(stores.identity.user_id),
        slug=slug,
        n_samples=len(rows),
        sample_preview=preview[:PREVIEW_ROWS],
        headers=["query", "ground_truth"],
        source_dataset=dataset_name,
        column_label_sets=_column_label_sets(["query", "ground_truth"], rows),
    )
    draft = draft.apply_resolution(
        values={
            "raw_task_description": task,
            "connector": connector,
            "scoring_matcher": matcher,
            "scoring_dials": dials,
            # Not validated: a dataset's ceiling may exceed the bound on the operator edit path.
            "optimization_overrides": {
                **OptimizationOverrides().model_dump(mode="json"),
                "budget": Budget(rounds=cc.optimization.budget.rounds).model_dump(mode="json"),
                "halt_at_accuracy": cc.optimization.halt_at_accuracy,
                "optimizer": cc.optimization.optimizer,
                "arm_gate": cc.optimization.arm_gate,
                "nodes": {n: o.model_dump(mode="json") for n, o in cc.optimization.nodes.items()},
            },
            "pipeline_overlay": pipeline_overlay,
            "origin_prompts": authored_prompts,
            "pipeline_steps": authored.active_steps,
            "backend_nodes": backend_nodes,
            "candidate_library": authored.candidate_library,
        },
        provenance={"task_description": Provenance.CONFIRMED},
    )
    if overrides:
        draft = draft.patch(**overrides)
    return save_checkin_draft(stores, draft)


async def draft_from_dataset_name(*, stores: Stores, dataset_name: str) -> DraftCampaign:
    return await draft_from_dataset(stores=stores, dataset_name=dataset_name)


async def draft_from_origin(*, stores: Stores, origin_id: str) -> DraftCampaign:
    campaign = canonical_origin_campaign(stores, origin_id)
    if campaign is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
    seed = stores.campaigns.read_cycle_seed(campaign.root_hop)
    if seed is not None and seed.origin_prompts:
        overrides["origin_prompts"] = {
            node: dict(prompt.fields) for node, prompt in seed.origin_prompts.items()
        }
    return await draft_from_dataset(
        stores=stores,
        dataset_name=campaign.dataset_name,
        overrides=overrides,
        origin_campaign=campaign,
    )
