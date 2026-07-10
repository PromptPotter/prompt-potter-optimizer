"""``ingest_draft`` — parse an uploaded blob into a durable check-in campaign.

The single orchestration seam both ingest surfaces call: the web
``POST /datasets/ingest`` handler and the CLI ``new <file>`` branch. Keeping the
parse → slug-validate → draft-build → mint-check-in sequence here (not inline in
the API handler) is what makes CLI/web parity real — neither surface owns the
logic, both call this. The first ingest action mints a real disk-backed campaign
in the ``checkin`` lifecycle (:func:`create_checkin_campaign`), so the returned
draft's ``draft_id`` IS its ``campaign_id`` and the in-progress authoring survives
a restart. The handler/CLI translate the raised errors to their own shape (HTTP
status vs. stderr); the orchestration is identical.

Spec: ``docs/specs/roadmap.md`` +
``docs/specs/roadmap.md § Ingest``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import validate_dataset_name

# Per-file upload cap. 25 MB comfortably holds ``MAX_SAMPLES`` 500-byte rows
# plus headroom; rejects the obvious DOS shapes (multi-hundred-MB blobs) before
# UTF-8 decode. The web boundary enforces it on the wire; the CLI reads a local
# file the operator already chose, so it relies on the per-row cap in
# ``read_tabular`` instead.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _column_label_sets(
    headers: list[str], rows: list[dict[str, str]]
) -> dict[str, tuple[str, ...]]:
    """Per-column closed label set over the FULL upload (not the truncated
    preview): the answer space the origin gate + check-in proposer need. Only
    columns that read as a fixed taxonomy carry an entry; an open-ended column
    (query text, numeric answers) is absent."""
    n = len(rows)
    return {
        header: labels
        for header in headers
        if (labels := closed_label_set((row.get(header, "") for row in rows), n_rows=n))
    }


class SlugTakenError(Exception):
    """The derived slug already exists in this tenant's collection.

    Carries a free ``suggested`` slug so the caller can offer it (409 on the
    wire, a one-line hint on the CLI)."""

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
) -> DraftCampaign:
    """Parse ``blob`` → build a draft → mint a durable check-in campaign.

    The format is detected from ``filename`` (CSV/TSV/JSON/JSONL/XLSX). Raises
    :class:`~promptpotter.application.datasets.csv_ingest.IngestError`
    (bad/empty/oversized/unsupported upload, or hardened-mode-blocked Excel),
    :class:`ValueError` (bad slug), or :class:`SlugTakenError` (slug collision —
    raised BEFORE the check-in campaign is minted, so a collision leaves no
    orphan). Byte-size capping is the wire boundary's concern — not enforced here.
    Returns the keyed draft (``draft_id`` == the new ``campaign_id``).
    """
    from promptpotter.application.jobs.launcher import create_checkin_campaign

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
) -> DraftCampaign:
    """Build a fully-confirmed :class:`DraftCampaign` straight from an authored
    dataset's on-disk files, then mint a check-in campaign from it.

    ``overrides`` are draft fields the *campaign* supplies rather than the dataset
    (campaign-from-origin passes ``reused_origin_id`` + the seed's prompt). They are
    applied before the mint, so creation persists the final state in one write.

    This is the direct path behind "open an existing dataset (demo / benchmark /
    owned tenant dataset) in the ingest panel": no browser-side CSV reconstruction,
    no ``/preview`` round-trip, no field-by-field prefill. The dataset's pipeline
    node config rides through as ``pipeline_overlay``, so starting the campaign
    **preserves the backend model/provider** (a fresh CSV upload would instead
    fall back to connector defaults). The same check-in → Start sequence runs from
    here on, so both surfaces share one path. Returns the keyed draft.
    """
    from promptpotter.application.jobs.launcher import create_checkin_campaign

    items = resolve_dataset_items(stores, dataset_name)
    rows: list[dict[str, str]] = [
        {"query": str(it["query"]), "ground_truth": str(it["ground_truth"])}
        for it in items
        if it.get("query") and it.get("ground_truth")
    ]
    if not rows:
        raise IngestError(
            reason="empty",
            message=f"Dataset {dataset_name!r} has no usable (query, ground_truth) rows.",
        )

    # One validated parse of the dataset's config files. The `or` ladders below
    # fire only where the authored file leaves a field empty. (The optimizer LLM
    # is install-global — datasets/_optimizer/pipeline.json — so the draft no
    # longer carries provider/model.)
    authored = read_authored_dataset(dataset_dir)
    cc = authored.campaign_config
    task = authored.task_description
    scoring = str(cc.scoring or "").split("(", 1)[0].strip() or DEFAULT_SCORING_COMPOSITE
    max_rounds = cc.optimization.max_rounds or DEFAULT_MAX_ROUNDS
    connector = authored.backend_type or DEFAULT_CONNECTOR
    pipeline_overlay = authored.pipeline_nodes

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
            # Origin carries its config instead of resetting to stock; lock_model
            # keeps the stock default. Built off the default dump (not validated)
            # so a dataset's higher ceiling passes through — the 1-100 bound gates
            # only the operator edit path.
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


__all__ = ["MAX_UPLOAD_BYTES", "SlugTakenError", "draft_from_dataset", "ingest_draft"]
