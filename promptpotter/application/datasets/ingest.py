from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.datasets.authored import read_authored_dataset
from promptpotter.application.datasets.csv_ingest import (
    IngestError,
    closed_label_set,
    format_from_filename,
    read_tabular,
)
from promptpotter.application.datasets.draft_build import overlay_from_campaign_config
from promptpotter.application.datasets.draft_campaign import (
    DEFAULT_MAX_ROUNDS,
    DEFAULT_SCORING_MATCHER,
    PREVIEW_ROWS,
    DraftCampaign,
    OptimizationOverrides,
    default_slug_from_filename,
    new_draft,
)
from promptpotter.application.datasets.loaders import bank_samples, resolve_dataset_items
from promptpotter.application.datasets.prompts import (
    list_dataset_prompts,
    load_dataset_prompt,
)
from promptpotter.application.jobs.launcher.checkin import create_checkin_campaign
from promptpotter.application.origin import canonical_origin_campaign
from promptpotter.application.scoring.formula import (
    DIALS_KEY,
    parse_dials,
    spell_dials,
    split_scoring_block,
)
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.connectors.protocol import PROBE_WORKLOAD
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.infrastructure.backend import build_backend_client
from promptpotter.infrastructure.llm.capabilities import refresh_model_capabilities
from promptpotter.infrastructure.store.dataset_access import (
    backend_type_of_dataset,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError

# Enforced by the web boundary only; the CLI relies on the per-row cap in ``read_tabular``.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

logger = logging.getLogger(__name__)


async def fetch_backend_nodes(
    connector_name: str, *, backend_url: str = DEFAULT_BACKEND_URL
) -> dict[str, Any]:
    connector = connectors.get(connector_name)
    if connector.execution == "in_process":
        return {}
    client = build_backend_client(connector, backend_url, workload=PROBE_WORKLOAD)
    try:
        resp = await client.fetch_pipeline()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("check-in could not read %s pipeline schema: %s", connector_name, exc)
        return {}
    finally:
        await client.aclose()
    nodes = (resp.get("data") or resp).get("nodes")
    return dict(nodes) if isinstance(nodes, dict) else {}


async def refresh_capabilities(stores: Stores) -> None:
    await refresh_model_capabilities(Path(stores.base_dir))


def _column_label_sets(
    headers: list[str], rows: list[dict[str, str]]
) -> dict[str, tuple[str, ...]]:
    n = len(rows)
    return {
        header: labels
        for header in headers
        if (labels := closed_label_set((row.get(header, "") for row in rows), n_rows=n))
    }


class SlugTakenError(Exception):
    def __init__(self, slug: str, suggested: str) -> None:
        self.slug = slug
        self.suggested = suggested
        super().__init__(f"slug {slug!r} already exists in this tenant's collection")


async def ingest_draft(
    *,
    stores: Stores,
    blob: bytes,
    filename: str,
    slug: str | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> DraftCampaign:
    table = read_tabular(blob, fmt=format_from_filename(filename or "upload.csv"))
    base_slug = (slug or default_slug_from_filename(filename or "upload")).lower()
    validate_dataset_name(base_slug)
    # Raised before the check-in campaign is minted, so a collision leaves no orphan.
    if stores.tenant_datasets.slug_exists(base_slug):
        raise SlugTakenError(base_slug, stores.tenant_datasets.suggest_free_slug(base_slug))
    await refresh_capabilities(stores)
    backend_nodes = await fetch_backend_nodes(DEFAULT_CONNECTOR, backend_url=backend_url)

    preview = [dict(row) for row in table.rows[:PREVIEW_ROWS]]
    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        slug=base_slug,
        n_samples=len(table.rows),
        sample_preview=preview,
        headers=list(table.headers),
        source_file=filename or "",
        column_label_sets=_column_label_sets(list(table.headers), list(table.rows)),
    )
    draft = draft.patch(backend_nodes=backend_nodes)
    _campaign_id, _cycle_id, keyed = create_checkin_campaign(
        stores,
        draft=draft,
        bank_items=list(table.rows),
        source_file=filename or "",
        headers=tuple(table.headers),
    )
    return keyed


async def draft_from_dataset(
    *,
    stores: Stores,
    dataset_dir: Path,
    dataset_name: str,
    overrides: dict[str, Any] | None = None,
    origin_campaign: Campaign | None = None,
) -> DraftCampaign:
    items = resolve_dataset_items(stores, dataset_name)
    rows: list[dict[str, str]] = [
        {"query": str(it["query"]), "ground_truth": str(it["ground_truth"])}
        for it in items
        if it.get("query") and it.get("ground_truth")
    ]
    if not rows:
        raise IngestError(
            reason="empty",
            message=(
                f"Dataset {dataset_name!r} has no usable (query, ground_truth) rows. A draft is "
                f"built from a labelled sample table; a VERIFIER-GRADED dataset (backend_type "
                f"`harbor` or `promptpotter`) has none — its cells come from the connector's "
                f"`experiment_file` and carry no label — so it cannot be drafted through this "
                f"path yet and is launched with `python -m promptpotter new {dataset_name}`."
            ),
        )

    await refresh_capabilities(stores)
    # An origin reuse reads the node declaration off ITS connector, not the shared file's.
    backend_nodes = await fetch_backend_nodes(
        origin_campaign.backend_type
        if origin_campaign is not None
        else backend_type_of_dataset(stores, dataset_name)
    )

    authored = read_authored_dataset(dataset_dir)
    cc = authored.campaign_config
    task = authored.task_description
    spec = split_scoring_block(cc.scoring, judge_instrument=None)
    matcher = (spec.per_sample or "").split("(", 1)[0].strip() or DEFAULT_SCORING_MATCHER
    declared = parse_dials(cc.scoring.get(DIALS_KEY, "")) if isinstance(cc.scoring, dict) else {}
    pinned = anchored_criterion_dials(spec.per_cell) if spec.per_cell else None
    dials = spell_dials(declared or {name: d.weight for name, d in (pinned or {}).items()})
    # `is not None`, never `or`: 0 means "measure the origin and stop".
    authored_rounds = cc.optimization.max_rounds
    max_rounds = authored_rounds if authored_rounds is not None else DEFAULT_MAX_ROUNDS
    connector = authored.backend_type or DEFAULT_CONNECTOR
    pipeline_overlay = authored.pipeline_nodes
    if origin_campaign is not None:
        # A merge, not a replacement: the origin's frozen config is sparse.
        pipeline_overlay = merge_node_blocks(
            pipeline_overlay,
            overlay_from_campaign_config(load_campaign_config(origin_campaign.config)),
        )

    origin_prompt_fields: dict[str, Any] = {}
    prompt_names = list_dataset_prompts(dataset_dir)
    if prompt_names:
        name = "default" if "default" in prompt_names else prompt_names[0]
        try:
            origin_prompt_fields = load_dataset_prompt(dataset_dir, name).prompt_fields()
        except FileNotFoundError:
            origin_prompt_fields = {}

    slug = dataset_name.lower()
    # The check-in model reads the preview's labels, so no bench row may reach it.
    preview = [
        {"query": s.query, "ground_truth": str(s.ground_truth)}
        for s in partition_bank(bank_samples(items), cc.dataset_split).search
    ]

    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        slug=slug,
        n_samples=len(rows),
        sample_preview=preview[:PREVIEW_ROWS],
        headers=["query", "ground_truth"],
        source_file=f"dataset:{dataset_name}",
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
                "max_rounds": max_rounds,
                "optimizer": cc.optimization.optimizer,
                "nodes": {n: o.model_dump(mode="json") for n, o in cc.optimization.nodes.items()},
            },
            "pipeline_overlay": pipeline_overlay,
            "origin_prompt_fields": origin_prompt_fields,
            "pipeline_steps": authored.active_steps,
            "backend_nodes": backend_nodes,
            "candidate_library": authored.candidate_library,
        },
        provenance={"task_description": Provenance.CONFIRMED},
    )
    if overrides:
        draft = draft.patch(**overrides)
    _campaign_id, _cycle_id, keyed = create_checkin_campaign(
        stores,
        draft=draft,
        bank_items=rows,
        source_file=f"dataset:{dataset_name}",
        headers=("query", "ground_truth"),
    )
    return keyed


async def draft_from_dataset_name(*, stores: Stores, dataset_name: str) -> DraftCampaign:
    return await draft_from_dataset(
        stores=stores,
        dataset_dir=readable_dataset_dir(stores, dataset_name),
        dataset_name=dataset_name,
    )


async def draft_from_origin(*, stores: Stores, origin_id: str) -> DraftCampaign:
    campaign = canonical_origin_campaign(stores, origin_id)
    if campaign is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
    seed = stores.campaigns.read_cycle_seed(campaign.root_hop)
    if seed is not None and seed.origin_prompt_fields:
        overrides["origin_prompt_fields"] = dict(seed.origin_prompt_fields)
    return await draft_from_dataset(
        stores=stores,
        dataset_dir=readable_dataset_dir(stores, campaign.dataset_name),
        dataset_name=campaign.dataset_name,
        overrides=overrides,
        origin_campaign=campaign,
    )


__all__ = [
    "MAX_UPLOAD_BYTES",
    "SlugTakenError",
    "draft_from_dataset",
    "draft_from_dataset_name",
    "draft_from_origin",
    "fetch_backend_nodes",
    "ingest_draft",
    "refresh_capabilities",
]
