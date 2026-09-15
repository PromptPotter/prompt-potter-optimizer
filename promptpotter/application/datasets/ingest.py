"""``ingest_draft`` — the single orchestration seam both ingest surfaces call, which is what makes
CLI/web parity real. The first action mints a durable check-in, so ``draft_id`` IS its ``campaign_id``."""

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
from promptpotter.application.datasets.draft_campaign import (
    DEFAULT_MAX_ROUNDS,
    DEFAULT_SCORING_COMPOSITE,
    PREVIEW_ROWS,
    DraftCampaign,
    OptimizationOverrides,
    default_slug_from_filename,
    new_draft,
)
from promptpotter.application.datasets.loaders import resolve_dataset_items
from promptpotter.application.datasets.prompts import (
    list_dataset_prompts,
    load_dataset_prompt,
)
from promptpotter.application.jobs.launcher.checkin import create_checkin_campaign
from promptpotter.application.jobs.launcher.draft_build import overlay_from_campaign_config
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.connectors.protocol import PROBE_WORKLOAD
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.infrastructure.backend import build_backend_client
from promptpotter.infrastructure.llm.capabilities import refresh_model_capabilities
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores

# Per-file upload cap. 25 MB comfortably holds ``MAX_SAMPLES`` 500-byte rows
# plus headroom; rejects the obvious DOS shapes (multi-hundred-MB blobs) before
# UTF-8 decode. The web boundary enforces it on the wire; the CLI reads a local
# file the operator already chose, so it relies on the per-row cap in
# ``read_tabular`` instead.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

logger = logging.getLogger(__name__)


async def fetch_backend_nodes(
    connector_name: str, *, backend_url: str = DEFAULT_BACKEND_URL
) -> dict[str, Any]:
    """The backend's own ``GET /pipeline::nodes``, read ONCE per check-in and stored on the draft.

    This is the half a draft cannot derive. ``optimizer.param_keys`` — which params are search
    AXES — is declared by the service and by nothing else, so a setup screen built from the
    connector seed alone concludes that nothing is movable and draws a lock the run does not
    enforce. Fetched here, at the surface that already owns backend wiring, rather than inside
    the pure projection that renders it.

    An in-process connector has no service to ask and its manifest IS the declaration; a probe
    that fails returns ``{}``, which the wire reports as ``schema_source: unreachable`` instead
    of letting an empty answer read as a locked one.
    """
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
    """Refresh this tenant's model-capability snapshot on the same beat as the pipeline probe.

    Best-effort by construction: the refresh keeps any prior snapshot when the catalogue cannot be
    read, and a missing snapshot resolves to UNKNOWN rather than to "unsupported". So a check-in
    never blocks on a third party's uptime, and never narrows an axis because of it either.
    """
    await refresh_model_capabilities(Path(stores.base_dir))


def _column_label_sets(
    headers: list[str], rows: list[dict[str, str]]
) -> dict[str, tuple[str, ...]]:
    """Per-column closed label set over the FULL upload, not the truncated preview — the answer space the
    origin gate needs. Only a column reading as a fixed taxonomy carries an entry."""
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


def ingest_draft(
    *,
    stores: Stores,
    blob: bytes,
    filename: str,
    slug: str | None = None,
    backend_nodes: dict[str, Any] | None = None,
) -> DraftCampaign:
    """Format is detected from ``filename``. ``SlugTakenError`` is raised BEFORE the check-in campaign is
    minted, so a collision leaves no orphan. Byte-size capping belongs to the wire boundary, not here."""

    table = read_tabular(blob, fmt=format_from_filename(filename or "upload.csv"))
    base_slug = (slug or default_slug_from_filename(filename or "upload")).lower()
    validate_dataset_name(base_slug)  # raises ValueError on a bad slug
    if stores.tenant_datasets.slug_exists(base_slug):
        raise SlugTakenError(base_slug, stores.tenant_datasets.suggest_free_slug(base_slug))

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
    draft = draft.patch(backend_nodes=dict(backend_nodes or {}))
    # Mint the check-in campaign + stash the raw rows + headers under it;
    # materialization to Samples waits until the column mapping is confirmed (at
    # Start). The resolution block lets an operator open checkin/cache.json and
    # see what still blocks mint.
    _campaign_id, _cycle_id, keyed = create_checkin_campaign(
        stores,
        draft=draft,
        bank_items=list(table.rows),
        source_file=filename or "",
        headers=tuple(table.headers),
    )
    return keyed


def draft_from_dataset(
    *,
    stores: Stores,
    dataset_dir: Path,
    dataset_name: str,
    overrides: dict[str, Any] | None = None,
    backend_nodes: dict[str, Any] | None = None,
    origin_campaign: Campaign | None = None,
) -> DraftCampaign:
    """Build a fully-confirmed draft straight from an authored dataset's files, then mint a check-in. The
    node config rides through as ``pipeline_overlay``, PRESERVING the backend model/provider.
    ``origin_campaign`` anchors an origin REUSE: its frozen config layers over the dataset file's
    nodes, the order a run resolves in, so the draft opens on what that origin ran."""

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

    # One validated parse of the dataset's config files. The `or` ladders below fire only where
    # the authored file leaves a field empty. The optimizer LLM is install-global
    # (`promptpotter/assets/optimizer/pipeline.yaml`), so no draft carries provider/model.
    authored = read_authored_dataset(dataset_dir)
    cc = authored.campaign_config
    task = authored.task_description
    scoring = str(cc.scoring or "").split("(", 1)[0].strip() or DEFAULT_SCORING_COMPOSITE
    # `is not None`, never `or`: 0 is a MEANINGFUL value here — "measure the origin and stop" —
    # and `or` would silently promote it to the default, handing the operator unbounded rounds
    # when they asked for none. `None` (authored as unlimited) has no draft representation, so it
    # takes the default; that is a deliberate draft starting point, not a coerced answer.
    authored_rounds = cc.optimization.max_rounds
    max_rounds = authored_rounds if authored_rounds is not None else DEFAULT_MAX_ROUNDS
    connector = authored.backend_type or DEFAULT_CONNECTOR
    pipeline_overlay = authored.pipeline_nodes
    if origin_campaign is not None:
        # A merge, not a replacement: the origin's config is sparse, and the dataset still owns
        # every node it never touched.
        pipeline_overlay = merge_node_blocks(
            pipeline_overlay,
            overlay_from_campaign_config(load_campaign_config(origin_campaign.config)),
        )

    # The authored dataset's own starting prompt rides through as the draft's
    # ``origin_prompt_fields`` (its six string fields + few-shot), so committing a
    # demo/benchmark/owned Origin preserves the prompt the optimizer evolves
    # from — a fresh CSV upload instead gets the check-in's decomposition.
    origin_prompt_fields: dict[str, Any] = {}
    prompt_names = list_dataset_prompts(dataset_dir)
    if prompt_names:
        name = "default" if "default" in prompt_names else prompt_names[0]
        try:
            origin_prompt_fields = load_dataset_prompt(dataset_dir, name).prompt_field_dict()
        except FileNotFoundError:
            origin_prompt_fields = {}

    # Keep the canonical slug — an existing dataset (demo / benchmark / owned)
    # is NOT a new dataset, so it must not uniquify into a `{slug}-N` clone. The
    # `dataset:{name}` source_file marks this draft as derived, and the commit
    # path mints against this canonical dataset instead of materializing a folder.
    slug = dataset_name.lower()

    # headers ["query","ground_truth"] auto-confirm the column mapping in
    # new_draft(); the config knobs auto-confirm there too. We then state the
    # task framing + override the knob VALUES from the dataset's own config.
    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        slug=slug,
        n_samples=len(rows),
        sample_preview=rows[:PREVIEW_ROWS],
        headers=["query", "ground_truth"],
        source_file=f"dataset:{dataset_name}",
        column_label_sets=_column_label_sets(["query", "ground_truth"], rows),
    )
    draft = draft.apply_resolution(
        values={
            "raw_task_description": task,
            "connector": connector,
            "scoring_composite": scoring,
            # The campaign-config knobs as one object. Preserve the dataset's round
            # ceiling + its own mechanism toggles (sorting/early-abort) so reusing an
            # Origin carries its config instead of resetting to stock. Built off the
            # default dump (not validated) so a dataset's higher ceiling passes
            # through — the 1-100 bound gates only the operator edit path.
            "optimization_overrides": {
                **OptimizationOverrides().model_dump(mode="json"),
                "max_rounds": max_rounds,
                "mechanisms": cc.optimization.mechanisms.model_dump(mode="json"),
            },
            "pipeline_overlay": pipeline_overlay,
            "origin_prompt_fields": origin_prompt_fields,
            # Preserve the dataset's own pipeline (full Research+Match, llm_only, …)
            # so reuse doesn't reset to the connector default.
            "pipeline_steps": authored.active_steps,
            # Reuse re-probes rather than inheriting: the committed dataset carries only the
            # overlay, and the service may have moved since it was written.
            "backend_nodes": dict(backend_nodes or {}),
            # The origin HOLDS its candidate library — carry the committed value so
            # reopening surfaces the dependency as already FULFILLED (not Missing),
            # and a re-mint re-persists it through the one origin-write seam.
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


__all__ = [
    "MAX_UPLOAD_BYTES",
    "SlugTakenError",
    "draft_from_dataset",
    "fetch_backend_nodes",
    "ingest_draft",
    "refresh_capabilities",
]
