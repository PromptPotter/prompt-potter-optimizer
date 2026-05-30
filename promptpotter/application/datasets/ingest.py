"""``ingest_draft`` — parse an uploaded blob into a registered ``DraftCampaign``.

The single orchestration seam both ingest surfaces call: the web
``POST /datasets/ingest`` handler and the CLI ``ingest`` verb. Keeping the
parse → slug-validate → draft-create → cache-write sequence here (not inline in
the API handler) is what makes CLI/web parity real — neither surface owns the
logic, both call this. The handler/CLI translate the raised errors to their own
shape (HTTP status vs. stderr); the orchestration is identical.

Spec: ``docs/specs/m10-origin-resolution-checkin.md`` (CLI parity) +
``docs/specs/m13-chat-first-user-web.md § Ingest``.
"""

from __future__ import annotations

from promptpotter.application.datasets.csv_ingest import read_tabular
from promptpotter.application.datasets.draft_campaign import (
    PREVIEW_ROWS,
    DraftCampaign,
    DraftCampaignRegistry,
    default_slug_from_filename,
)
from promptpotter.application.datasets.origin_readiness import resolution_block
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import validate_dataset_name

# Per-file upload cap. 25 MB comfortably holds ``MAX_SAMPLES`` 500-byte rows
# plus headroom; rejects the obvious DOS shapes (multi-hundred-MB blobs) before
# UTF-8 decode. The web boundary enforces it on the wire; the CLI reads a local
# file the operator already chose, so it relies on the per-row cap in
# ``read_tabular`` instead.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


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
    registry: DraftCampaignRegistry,
    blob: bytes,
    filename: str,
    slug: str | None = None,
) -> DraftCampaign:
    """Parse ``blob`` → register a ``DraftCampaign`` → persist its draft cache.

    Raises :class:`~promptpotter.application.datasets.csv_ingest.IngestError`
    (bad/empty/oversized CSV), :class:`ValueError` (bad slug), or
    :class:`SlugTakenError` (slug collision). Byte-size capping is the wire
    boundary's concern — not enforced here.
    """
    table = read_tabular(blob, fmt="csv")
    base_slug = (slug or default_slug_from_filename(filename or "upload")).lower()
    validate_dataset_name(base_slug)  # raises ValueError on a bad slug
    if stores.tenant_datasets.slug_exists(base_slug):
        raise SlugTakenError(base_slug, stores.tenant_datasets.suggest_free_slug(base_slug))

    preview = [dict(row) for row in table.rows[:PREVIEW_ROWS]]
    draft = registry.create(
        tenant_id=stores.identity.tenant_id,
        slug=base_slug,
        n_samples=len(table.rows),
        sample_preview=preview,
        headers=list(table.headers),
        source_file=filename or "",
    )
    # Stash the raw rows + headers; materialization to Samples waits until the
    # column mapping is confirmed (at mint). The resolution block lets an
    # operator open cache.json and see what still blocks mint.
    stores.tenant_datasets.write_draft_cache(
        draft.draft_id,
        list(table.rows),
        source_file=filename or "",
        headers=list(table.headers),
    )
    stores.tenant_datasets.write_draft_resolution(draft.draft_id, resolution_block(draft))
    return draft


__all__ = ["MAX_UPLOAD_BYTES", "SlugTakenError", "ingest_draft"]
