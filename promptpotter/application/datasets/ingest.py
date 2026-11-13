from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.application.account import record_upload
from promptpotter.application.datasets.authored import read_authored_dataset
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.draft_campaign import (
    PREVIEW_ROWS,
    default_slug_from_filename,
    new_draft,
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
from promptpotter.application.pipeline_resolve import (
    ServedPipeline,
    cycle_config,
    draft_pipeline,
    draft_served,
)
from promptpotter.connectors import upload_connector
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile, UploadBasis
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.llm.capabilities import refresh_model_capabilities
from promptpotter.infrastructure.store.dataset_access import claim_own_definition
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import CAMPAIGN_CREATE_CAP, require_capability


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


def _first_blank_rows(headers: list[str], rows: list[dict[str, str]]) -> dict[str, int]:
    blank: dict[str, int] = {}
    for ordinal, row in enumerate(rows, start=1):
        for header in headers:
            if header not in blank and not row.get(header, ""):
                blank[header] = ordinal
    return blank


def _kept(stores: Stores, draft: DraftFile, served: ServedPipeline) -> DraftCampaignWire:
    wire = draft_wire(stores, draft, served)
    save_checkin_draft(stores, draft)
    return wire


async def ingest_draft(
    *,
    stores: Stores,
    blob: bytes,
    filename: str,
    slug: str | None = None,
) -> DraftCampaignWire:
    await _open_checkin(stores)
    kept = stores.tenant_datasets.keep_upload(blob, file_extension(filename))
    record_upload(stores, filename=filename, kept=kept)
    table = read_tabular(blob, filename)
    base_slug = slug or default_slug_from_filename(filename or "upload")
    try:
        validate_dataset_name(base_slug)
    except ValueError as exc:
        raise IngestError(reason="bad_slug", message=str(exc)) from None
    # Raised before the draft is kept, so a taken name leaves no orphan.
    claim_own_definition(stores, base_slug, new=True)

    preview = [dict(row) for row in table.rows[:PREVIEW_ROWS]]
    draft = new_draft(
        slug=base_slug,
        basis=UploadBasis(connector=upload_connector().name, kept=kept.name),
        n_samples=len(table.rows),
        sample_preview=preview,
        headers=list(table.headers),
        source_file=filename or "",
        column_label_sets=_column_label_sets(list(table.headers), list(table.rows)),
        first_blank_row=_first_blank_rows(list(table.headers), list(table.rows)),
    )
    return _kept(stores, draft, await draft_served(stores, draft))


async def draft_from_dataset(
    *,
    stores: Stores,
    dataset_name: str,
    overrides: dict[str, Any] | None = None,
    origin_campaign: Campaign | None = None,
) -> DraftCampaignWire:
    await _open_checkin(stores)
    dataset = offered_dataset(stores, dataset_name)
    bank = checkin_bank(stores, dataset_name)
    rows = labelled_rows(bank.samples)

    authored = read_authored_dataset(dataset)
    cc = authored.campaign_config
    task = authored.task_description or None
    # The dataset's block is the draft's basis: the draft sets only what the origin's declaration
    # laid on its nodes.
    config: dict[str, Any] = {}
    if origin_campaign is not None:
        declared = cycle_config(stores, origin_campaign, None).config
        config = declared.model_dump(
            mode="json",
            exclude_unset=True,
            include={"pipeline_overlay", "optimizer_narrowing"},
        )

    slug = dataset_name.lower()
    # The check-in model reads the preview's labels, so no bench row may reach it.
    preview = [
        {"query": s.query, "ground_truth": str(s.ground_truth)}
        for s in partition_bank(bank.samples, cc.dataset_split, seed=cc.run_seed).search
    ]

    draft = new_draft(
        slug=slug,
        basis=DatasetBasis(dataset=dataset_name),
        n_samples=len(rows),
        sample_preview=preview[:PREVIEW_ROWS],
        headers=["query", "ground_truth"],
        column_label_sets=_column_label_sets(["query", "ground_truth"], rows),
    )
    served = await draft_served(stores, draft)
    running = draft_pipeline(stores, draft, served).chain.running
    draft = draft.apply_resolution(
        values={
            "raw_task_description": task,
            "config": config,
            "origin_prompts": {
                node: dict(prompt.fields)
                for node, prompt in origin_prompts(dataset, running).items()
            },
            "candidate_library": authored.candidate_library,
        },
        provenance={} if task is None else {"task_description": Provenance.CONFIRMED},
    )
    if overrides:
        draft = draft.patch(**overrides)
    return _kept(stores, draft, served)


async def draft_from_origin(*, stores: Stores, origin_id: str) -> DraftCampaignWire:
    campaign = canonical_origin_campaign(stores, origin_id)
    if campaign is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    overrides: dict[str, Any] = {}
    seed = cycle_config(stores, campaign, campaign.root_hop).origin
    if seed is not None:
        overrides["origin_prompts"] = {
            node: dict(prompt.fields) for node, prompt in seed.origin_prompts.items()
        }
    return await draft_from_dataset(
        stores=stores,
        dataset_name=campaign.dataset_name,
        overrides=overrides,
        origin_campaign=campaign,
    )
