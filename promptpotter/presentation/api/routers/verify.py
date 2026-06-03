"""Workspace-scope diagnostic-run records — sole data feed for the Verify tab.

Reads the sidecars written by ``cmd_verify`` (one
:class:`DiagnosticRunRecord` per re-evaluation pass) and serves them under
``/diagnostic-runs``. Cross-cycle, cross-campaign — the records live on the
tenant archive root, not on any single campaign.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.presentation.api.deps import StoreDep

verify_router = APIRouter(tags=["Verify"])


class DiagnosticRunListResponse(BaseModel):
    """One page of diagnostic-run records, newest first."""

    n: int
    runs: list[DiagnosticRunRecord]


@verify_router.get("/diagnostic-runs", response_model=DiagnosticRunListResponse)
async def list_diagnostic_runs(
    store: StoreDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset."),
) -> DiagnosticRunListResponse:
    """Return every diagnostic-run record on disk, optionally filtered by dataset."""
    runs = store.diagnostic_runs.list(dataset=dataset)
    return DiagnosticRunListResponse(n=len(runs), runs=runs)


__all__ = ["DiagnosticRunListResponse", "verify_router"]
