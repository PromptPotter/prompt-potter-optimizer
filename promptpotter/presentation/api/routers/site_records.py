from __future__ import annotations

from fastapi import APIRouter, Depends

from promptpotter.application.site_records import (
    SiteRecordBody,
    SiteRecordsDropped,
    SiteRecordsResponse,
    SiteRecordWire,
    drop_site_records_before,
    keep_site_record,
    kept_site_records,
)
from promptpotter.presentation.api.deps import require_site_records_token

site_records_router = APIRouter(
    prefix="/site-records",
    tags=["Site records"],
    dependencies=[Depends(require_site_records_token)],
)


@site_records_router.post("/{table}", response_model=SiteRecordWire)
def post_site_record(table: str, body: SiteRecordBody) -> SiteRecordWire:
    """Keep one row. The key rides the body, never the address: an address is logged."""
    return keep_site_record(table, body)


@site_records_router.get("/{table}", response_model=SiteRecordsResponse)
def get_site_records(table: str, since: str = "") -> SiteRecordsResponse:
    """Every row kept from ``since`` on; a table nothing was kept in answers empty."""
    return kept_site_records(table, since)


@site_records_router.delete("/{table}", response_model=SiteRecordsDropped)
def delete_site_records(table: str, before: str) -> SiteRecordsDropped:
    """Retention: drop the added rows kept before ``before``. A keyed row stays."""
    return drop_site_records_before(table, before)


assert any(dep.dependency is require_site_records_token for dep in site_records_router.dependencies)
