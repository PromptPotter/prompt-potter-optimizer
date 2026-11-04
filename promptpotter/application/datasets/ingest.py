from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.application.account import record_upload
from promptpotter.application.datasets.authored import read_authored_dataset
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    draft_wire,
    overlay_from_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import (
    PREVIEW_ROWS,
    DraftCampaign,
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
from promptpotter.application.pipeline_resolve import cycle_config, draft_served
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.checkin_draft import DatasetBasis, UploadBasis
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.llm.capabilities import refresh_model_capabilities
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import ConflictError, NotFoundError
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


async def _kept(stores: Stores, draft: DraftCampaign) -> DraftCampaignWire:
    wire = draft_wire(stores, draft, await draft_served(stores, draft))
    save_checkin_draft(stores, draft)
    return wire


class SlugTakenError(ConflictError):
    def __init__(self, slug: str, suggested: str) -> None:
        self.slug = slug
        self.suggested = suggested
        super().__init__(
            f"A dataset named '{slug}' already exists. Suggested slug: {suggested}.",
            code="slug_collision",
            details={"slug": slug, "suggested_slug": suggested},
        )


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
    base_slug = (slug or default_slug_from_filename(filename or "upload")).lower()
    try:
        validate_dataset_name(base_slug)
    except ValueError as exc:
        raise IngestError(reason="bad_slug", message=str(exc)) from None
    # Raised before the draft is kept, so a collision leaves no orphan.
    if stores.tenant_datasets.slug_exists(base_slug):
        raise SlugTakenError(base_slug, stores.tenant_datasets.suggest_free_slug(base_slug))

    preview = [dict(row) for row in table.rows[:PREVIEW_ROWS]]
    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        owner_user_id=str(stores.identity.user_id),
        slug=base_slug,
        basis=UploadBasis(connector=DEFAULT_CONNECTOR, kept=kept.name),
        n_samples=len(table.rows),
        sample_preview=preview,
        headers=list(table.headers),
        source_file=filename or "",
        column_label_sets=_column_label_sets(list(table.headers), list(table.rows)),
        first_blank_row=_first_blank_rows(list(table.headers), list(table.rows)),
    )
    return await _kept(stores, draft)


async def draft_from_dataset(
    *,
    stores: Stores,
    dataset_name: str,
    overrides: dict[str, Any] | None = None,
    origin_campaign: Campaign | None = None,
) -> DraftCampaignWire:
    await _open_checkin(stores)
    dataset_dir = offered_dataset(stores, dataset_name).directory
    bank = checkin_bank(stores, dataset_name)
    rows = labelled_rows(bank.samples)

    authored = read_authored_dataset(dataset_dir)
    cc = authored.campaign_config
    task = authored.task_description or None
    matcher, dials = scoring_choice(cc.scoring)
    pipeline_overlay = authored.pipeline_nodes
    if origin_campaign is not None:
        # A merge, not a replacement: the origin's declaration names only what its layers set.
        pipeline_overlay = merge_node_blocks(
            pipeline_overlay,
            overlay_from_campaign_config(cycle_config(stores, origin_campaign, None).config),
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
        basis=DatasetBasis(dataset=dataset_name),
        n_samples=len(rows),
        sample_preview=preview[:PREVIEW_ROWS],
        headers=["query", "ground_truth"],
        column_label_sets=_column_label_sets(["query", "ground_truth"], rows),
    )
    draft = draft.apply_resolution(
        values={
            "raw_task_description": task,
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
            "candidate_library": authored.candidate_library,
        },
        provenance={} if task is None else {"task_description": Provenance.CONFIRMED},
    )
    if overrides:
        draft = draft.patch(**overrides)
    return await _kept(stores, draft)


async def draft_from_origin(*, stores: Stores, origin_id: str) -> DraftCampaignWire:
    campaign = canonical_origin_campaign(stores, origin_id)
    if campaign is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
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
