"""The four dataset WRITES — CSV ingest, candidate-library upload, library-from-column, and drafting
a campaign off an existing dataset. Every one funnels into ``application/datasets/ingest.py``; this
module is the wire shell (upload caps, multipart, idempotency) and holds no ingest logic of its own."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import File, Form, Header, Request, UploadFile
from pydantic import Field

from promptpotter.application.datasets.csv_ingest import (
    MAX_SAMPLES,
    IngestError,
    candidate_library_from_rows,
    parse_candidate_library,
)
from promptpotter.application.datasets.ingest import (
    MAX_UPLOAD_BYTES,
    SlugTakenError,
    draft_from_dataset,
    ingest_draft,
)
from promptpotter.application.jobs.launcher.checkin import load_checkin_draft
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    readable_dataset_dir,
)
from promptpotter.presentation.api.deps import (
    StoresDep,
)
from promptpotter.presentation.api.routers.commands import (
    dispatch_draft_patch,
    ensure_idempotency_key,
)
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import (
    ConflictError,
    ContentTooLargeError,
    NotFoundError,
    PayloadInvalidError,
)


def _too_large(observed: int | str) -> ContentTooLargeError:
    return ContentTooLargeError(
        f"Upload {observed} bytes exceeds the per-file cap of {MAX_UPLOAD_BYTES} bytes."
    )


async def _read_capped(request: Request, upload: UploadFile, cap: int) -> bytes:
    """Read ``upload`` into memory, aborting with 413 the moment it exceeds ``cap``. Fast-reject on
    the declared ``Content-Length``, then stream in chunks — the header can lie or be absent."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise _too_large(declared)

    chunk_size = 1024 * 1024  # 1 MiB
    buf = bytearray()
    while chunk := await upload.read(chunk_size):
        buf.extend(chunk)
        if len(buf) > cap:
            raise _too_large(len(buf))
    return bytes(buf)


@datasets_router.post("/ingest")
async def ingest_dataset(
    request: Request,
    stores: StoresDep,
    file: Annotated[
        UploadFile,
        File(description="Tabular upload (CSV/TSV/JSON/JSONL/XLSX); any columns."),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> dict[str, Any]:
    """Parse an uploaded tabular file (CSV/TSV/JSON/XLSX); mint a durable check-in
    campaign and return its :class:`DraftCampaign` (``draft_id`` == ``campaign_id``).

    Wire contract pinned in ``docs/specs/m12-api-openapi.yaml::POST /datasets/ingest``.
    The check-in campaign appears in the sidebar immediately and survives a restart;
    nothing runs until the operator starts it via ``/commands/start-checkin``.
    """
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)

    # Parse → mint the check-in campaign → persist its bank. Shared with the CLI
    # `new <file>` path (`application/datasets/ingest.py`) so both surfaces drive
    # the identical orchestration; the handler only maps errors to HTTP.
    try:
        draft = ingest_draft(
            stores=stores,
            blob=blob,
            filename=file.filename or "",
            slug=slug,
        )
    except IngestError as exc:
        exc.details["max_samples"] = MAX_SAMPLES
        raise
    except ValueError as exc:
        raise PayloadInvalidError(str(exc), details={"reason": "bad_slug"}) from None
    except SlugTakenError as exc:
        # The chat turns this into an in-flow choice (use existing / save as new),
        # so it needs BOTH names: the colliding one (to offer "use existing") and
        # the free suggestion (to offer "save as new").
        raise ConflictError(
            f"A dataset named '{exc.slug}' already exists.",
            code="slug_collision",
            details={"slug": exc.slug, "suggested_slug": exc.suggested},
        ) from None
    return draft_wire_with_locks(draft)


@datasets_router.post("/draft/candidate-library")
async def upload_candidate_library(
    request: Request,
    stores: StoresDep,
    file: Annotated[
        UploadFile,
        File(description="Target library — one entry per line, or a single-column CSV/Excel."),
    ],
    draft_id: Annotated[
        str, Form(description="Draft to attach the library to.", min_length=8, max_length=128)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Attach a candidate library to a draft — the operator's "drop in place" for an
    unfulfilled ``candidate_source`` dependency. Returns the updated draft wire (its
    ``dependencies`` block now reports the dependency ``fulfilled``).

    A multipart ingress for an `edit-draft-campaign`: the upload is parsed into the
    patch value, then dispatched, so the edit lands on the check-in ledger like every
    other origin mutation. Nothing runs until Start. Wire contract pinned in
    ``docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library``.
    ``draft_id`` is the check-in campaign id.
    """
    idemp = ensure_idempotency_key(idempotency_key)
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)
    terms = parse_candidate_library(blob, file.filename or "")
    if not terms:
        raise PayloadInvalidError(
            "The candidate library has no usable entries (every line was blank).",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return await dispatch_draft_patch(
        stores,
        draft_id=draft_id,
        patch_raw={"candidate_library": terms},
        idempotency_key=idemp,
    )


class _BuildLibraryBody(StrictModel):
    """Body for building a candidate library from one of the draft's own columns."""

    # `draft_id` IS the owning `campaign_id`; bound it exactly as every other
    # check-in route does (`commands.py::_require_checkin_id`), not 64.
    draft_id: str = Field(min_length=8, max_length=128)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post("/draft/candidate-library/from-column")
async def build_candidate_library_from_column(
    stores: StoresDep,
    body: _BuildLibraryBody,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Build a draft's candidate library from the distinct values of one of its own
    columns — the unified "build from dataset" path (no external file).

    When the targets already live in the data (the ground-truth/target column, the
    union of the dataset's category sheets), the library is derived rather than
    uploaded. The derived terms become an `edit-draft-campaign` patch, so this lands
    on the check-in ledger exactly like the upload does. Returns the updated draft
    wire (its `candidate_library` dependency now `fulfilled`). Wire contract pinned in
    `docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library/from-column`.
    `draft_id` is the check-in campaign id.
    """
    idemp = ensure_idempotency_key(idempotency_key)
    draft = load_checkin_draft(stores, body.draft_id)
    if draft is None:
        raise NotFoundError(f"draft {body.draft_id!r} not found.", code="command_target_not_found")
    if body.column not in draft.headers:
        raise PayloadInvalidError(
            f"column {body.column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    bank = stores.checkin.load_bank(body.draft_id)
    if bank is None:
        raise PayloadInvalidError("draft has no cached rows to build from.")
    terms = candidate_library_from_rows(bank.get("items", []), body.column)
    if not terms:
        raise PayloadInvalidError(
            f"column {body.column!r} has no usable values.",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return await dispatch_draft_patch(
        stores,
        draft_id=body.draft_id,
        patch_raw={"candidate_library": terms},
        idempotency_key=idemp,
    )


@datasets_router.post("/{name}/draft")
def draft_from_existing_dataset(name: str, stores: StoresDep) -> dict[str, Any]:
    """Open an authored dataset's files as a durable check-in campaign.

    The direct path behind "open this dataset in the ingest panel" — a demo /
    benchmark / owned Origin becomes a prefilled check-in (``draft_id`` ==
    ``campaign_id``) without a browser-side CSV round-trip. Identity-gated through
    the same resolver as the other dataset reads; nothing runs until the operator
    starts it via ``/commands/start-checkin``.
    """
    dataset_dir = readable_dataset_dir(stores, name)
    draft = draft_from_dataset(stores=stores, dataset_dir=dataset_dir, dataset_name=name)
    return draft_wire_with_locks(draft)
