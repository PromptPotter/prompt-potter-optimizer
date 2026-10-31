"""The one machine ingress (`docs/architecture.md` § Site records).

The operator's website keeps its visit log, its messages and its newsletter list on this box, so
no outside party stores them. What a table means is the sender's: nothing here reads a row.
"""

from __future__ import annotations

import json

from pydantic import Field, JsonValue

from promptpotter.domain.strict_model import RequestBody, StrictModel
from promptpotter.infrastructure.store.site_records import SiteRecord, default_site_record_store
from promptpotter.shared.clock import epoch_seconds, utcnow_iso
from promptpotter.shared.errors import BadRequestError, ContentTooLargeError

_MAX_ROW_CHARS = 16_384
# An email address at its longest.
_MAX_KEY_CHARS = 320


class SiteRecordBody(RequestBody):
    row: dict[str, JsonValue] = Field(description="What to keep, returned as sent")
    key: str = Field(
        default="",
        description="Set, this row stands in for the one kept under the same key before it",
    )


class SiteRecordWire(StrictModel):
    at: str = Field(description="When this box kept the row, UTC")
    key: str = Field(description="Empty on a row that is only ever added")
    row: dict[str, JsonValue] = Field(description="The row as it was sent")


class SiteRecordsResponse(StrictModel):
    records: list[SiteRecordWire] = Field(
        description="In the order kept; one per key where rows carry one"
    )


class SiteRecordsDropped(StrictModel):
    dropped: int = Field(description="Rows removed")


def _epoch(stamp: str, what: str) -> float:
    at = epoch_seconds(stamp)
    if at is None:
        raise BadRequestError(f"{what} is not a timestamp: {stamp!r}")
    return at


def keep_site_record(table: str, body: SiteRecordBody) -> SiteRecordWire:
    """The time is this box's, never the sender's: retention is counted from it."""
    if len(json.dumps(body.row)) > _MAX_ROW_CHARS or len(body.key) > _MAX_KEY_CHARS:
        raise ContentTooLargeError(f"A row holds at most {_MAX_ROW_CHARS} characters")
    record = SiteRecord(at=utcnow_iso(), key=body.key, row=body.row)
    default_site_record_store().keep(table, record)
    return SiteRecordWire(**record._asdict())


def kept_site_records(table: str, since: str) -> SiteRecordsResponse:
    floor = _epoch(since, "since") if since else 0.0
    return SiteRecordsResponse(
        records=[
            SiteRecordWire(**record._asdict())
            for record in default_site_record_store().kept(table)
            if (epoch_seconds(record.at) or 0.0) >= floor
        ]
    )


def drop_site_records_before(table: str, before: str) -> SiteRecordsDropped:
    return SiteRecordsDropped(
        dropped=default_site_record_store().drop_before(table, _epoch(before, "before"))
    )
