"""File-tree reads — recursive listing + content for one file under the
cycle dir or the campaign-level artifacts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Query
from pydantic import BaseModel, Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store import campaign_root_dir_for, cycle_dir_for
from promptpotter.infrastructure.store.stores import resolve_cycle_path
from promptpotter.presentation.api.deps import StoreDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, ContentTooLargeError, NotFoundError

# Campaign-level file artifacts that live at the campaign dir, not a cycle dir.
# ``dashboard.json`` is NOT here — it is session-scoped and lives in the
# session's root cycle dir under ``cycles/``.
_CAMPAIGN_FILE_LEVEL_ARTIFACTS = ("campaign.json", "log.md", "hard_samples.json")
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MiB
_MAX_FILE_ENTRIES = 5000

_TEXT_SUFFIXES = {".txt", ".jsonl", ""}


class FileEntry(BaseModel):
    path: str = Field(description="Path relative to the scope root, forward slashes")
    scope: Literal["cycle", "campaign"] = Field(description="Which root the path is under")
    size: int = Field(description="File size in bytes")
    mtime: str = Field(description="ISO 8601 UTC modification time")


class FilesResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    entries: list[FileEntry]


class FileContentResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    scope: Literal["cycle", "campaign"]
    path: str
    size: int
    mtime: str
    content_type: Literal["json", "markdown", "log", "text", "binary"]
    content: str | None = Field(
        default=None,
        description="UTF-8 text content; None when binary or oversized",
    )


def _walk_files(root: Path) -> Iterator[Path]:
    """Walk *root* recursively, yielding files. Skip dotfiles except ``.cache/``."""
    if not root.exists():
        return
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith(".") and entry.name != ".cache":
            continue
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def _iso_mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()


def _resolve_safe_file(scope_root: Path, raw_path: str) -> Path:
    """Validate *raw_path* against escape attempts and confine it under *scope_root*."""
    if not raw_path or ".." in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise BadRequestError("Invalid path")
    scope_root_resolved = scope_root.resolve()
    resolved = (scope_root / raw_path).resolve()
    if not resolved.is_relative_to(scope_root_resolved):
        raise BadRequestError("Path escapes scope root")
    if not resolved.is_file():
        raise NotFoundError(f"File not found: {raw_path}")
    return resolved


def _classify_suffix(suffix: str) -> Literal["json", "markdown", "log", "text"] | None:
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "markdown"
    if suffix == ".log":
        return "log"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return None


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/files",
    response_model=FilesResponse,
)
def list_cycle_files(
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
) -> FilesResponse:
    """Recursive file tree for the cycle dir + campaign-level artifacts.

    ``descend`` walks into the ``.inner/`` sandbox (same seam as the dashboard +
    file-content routes), so the Files tree of an L4 inner descendant lists the
    inner cycle's tree. Absent/empty ``descend`` is a plain per-cycle read.
    """
    leaf_store, leaf = resolve_cycle_path(
        store, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    leaf_campaign, leaf_cycle = leaf.campaign_id, leaf.cycle_id
    cycle_dir = cycle_dir_for(leaf_store.base_dir, leaf_campaign, leaf_cycle)
    if not cycle_dir.is_dir():
        raise NotFoundError(f"Cycle not found: {campaign_id}/{cycle_id}")
    campaign_dir = campaign_root_dir_for(leaf_store.base_dir, leaf_campaign)

    entries: list[FileEntry] = []
    for f in _walk_files(cycle_dir):
        entries.append(
            FileEntry(
                path=f.relative_to(cycle_dir).as_posix(),
                scope="cycle",
                size=f.stat().st_size,
                mtime=_iso_mtime(f),
            )
        )
        if len(entries) > _MAX_FILE_ENTRIES:
            raise ContentTooLargeError(f"Too many entries in cycle dir (>{_MAX_FILE_ENTRIES})")

    for name in _CAMPAIGN_FILE_LEVEL_ARTIFACTS:
        f = campaign_dir / name
        if f.is_file():
            entries.append(
                FileEntry(
                    path=name,
                    scope="campaign",
                    size=f.stat().st_size,
                    mtime=_iso_mtime(f),
                )
            )

    return FilesResponse(campaign_id=campaign_id, cycle_id=cycle_id, entries=entries)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/file",
    response_model=FileContentResponse,
)
def get_cycle_file(
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    scope: Literal["cycle", "campaign"] = Query(..., description="cycle | campaign"),
    path: str = Query(..., description="Relative path under the chosen scope root"),
    descend: str | None = Query(None),
) -> FileContentResponse:
    """Read the contents of one file under the cycle or campaign scope.

    ``descend`` walks into the ``.inner/<previous cycle id>`` sandbox one hop per
    ``~``-joined ``campaign::cycle`` segment (same seam as the dashboard route), so
    an L4 inner descendant's ``rounds/round_NNNN.json`` reads from the inner cycle
    dir — not the outer root. Absent/empty ``descend`` is a plain per-cycle read.
    """
    leaf_store, leaf = resolve_cycle_path(
        store, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    leaf_campaign, leaf_cycle = leaf.campaign_id, leaf.cycle_id
    if scope == "cycle":
        scope_root = cycle_dir_for(leaf_store.base_dir, leaf_campaign, leaf_cycle)
    else:
        scope_root = campaign_root_dir_for(leaf_store.base_dir, leaf_campaign)
    if not scope_root.exists():
        raise NotFoundError(f"Scope root not found: {campaign_id}/{cycle_id}")

    resolved = _resolve_safe_file(scope_root, path)
    size = resolved.stat().st_size
    mtime = _iso_mtime(resolved)
    classification = _classify_suffix(resolved.suffix.lower())

    if size > _MAX_PREVIEW_BYTES:
        return FileContentResponse(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=None,
        )

    if classification is None or classification == "text":
        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileContentResponse(
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )
        return FileContentResponse(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=text,
        )

    return FileContentResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        scope=scope,
        path=path,
        size=size,
        mtime=mtime,
        content_type=classification,
        content=resolved.read_text(encoding="utf-8"),
    )
