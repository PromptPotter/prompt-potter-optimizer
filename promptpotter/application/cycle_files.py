from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from promptpotter.application.cycle_reads import view_cycle
from promptpotter.domain.cycle_paths import CyclePath
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import iter_files, read_bytes_optional, stat_optional
from promptpotter.infrastructure.store.layout import CampaignLayout, campaign_root_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import iso_z
from promptpotter.shared.errors import BadRequestError, ContentTooLargeError, NotFoundError

_MAX_PREVIEW_BYTES = 2 * 1024 * 1024
_MAX_FILE_ENTRIES = 5000

_TEXT_SUFFIXES = {".txt", ".jsonl", ""}

FileScope = Literal["cycle", "campaign"]


class FileEntry(StrictModel):
    path: str = Field(description="Path relative to the scope root, forward slashes")
    scope: FileScope = Field(description="Which root the path is under")
    size: int = Field(description="File size in bytes")
    mtime: str = Field(description="ISO 8601 UTC modification time")


class FilesResponse(StrictModel):
    campaign_id: str
    cycle_id: str
    entries: list[FileEntry]


class FileContentResponse(StrictModel):
    campaign_id: str
    cycle_id: str
    scope: FileScope
    path: str
    size: int
    mtime: str
    content_type: Literal["json", "markdown", "log", "text", "binary"]
    content: str | None = Field(
        default=None,
        description="UTF-8 text content; None when binary or oversized",
    )


def _shown(relative: Path) -> bool:
    """A dot-directory is the runtime's, except the cache an operator reads."""
    return not any(part.startswith(".") and part != ".cache" for part in relative.parts)


def _mtime(held: os.stat_result) -> str:
    return iso_z(datetime.fromtimestamp(held.st_mtime, UTC))


def _entry(path: str, scope: FileScope, held: os.stat_result) -> FileEntry:
    return FileEntry(path=path, scope=scope, size=held.st_size, mtime=_mtime(held))


def _resolve_safe_file(scope_root: Path, raw_path: str) -> Path:
    if not raw_path or ".." in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise BadRequestError("Invalid path")
    resolved = (scope_root / raw_path).resolve()
    if not resolved.is_relative_to(scope_root.resolve()):
        raise BadRequestError("Path escapes scope root")
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


def list_cycle_files(stores: Stores, path: CyclePath) -> FilesResponse:
    """The ids echoed are the ROOT hop's — the address the caller named; the files are the leaf's."""
    cycle = view_cycle(stores, path)
    campaign_dir = campaign_root_dir_for(cycle.stores.base_dir, cycle.hop.campaign_id)

    entries: list[FileEntry] = []
    for f, held in iter_files(cycle.dir):
        if _shown(relative := f.relative_to(cycle.dir)):
            entries.append(_entry(relative.as_posix(), "cycle", held))
        if len(entries) > _MAX_FILE_ENTRIES:
            raise ContentTooLargeError(f"Too many entries in cycle dir (>{_MAX_FILE_ENTRIES})")
    for f in CampaignLayout(campaign_dir).files():
        if (artifact := stat_optional(f)) is not None:
            entries.append(_entry(f.name, "campaign", artifact))
    return FilesResponse(
        campaign_id=path[0].campaign_id, cycle_id=path[0].cycle_id, entries=entries
    )


def read_cycle_file(
    stores: Stores, path: CyclePath, *, scope: FileScope, file: str
) -> FileContentResponse:
    """``content`` is ``None`` for a file past the preview cap or one that is not UTF-8 text."""
    cycle = view_cycle(stores, path)
    scope_root = (
        cycle.dir
        if scope == "cycle"
        else campaign_root_dir_for(cycle.stores.base_dir, cycle.hop.campaign_id)
    )
    if not scope_root.exists():
        raise NotFoundError(f"Scope root not found: {path[0].campaign_id}/{path[0].cycle_id}")

    resolved = _resolve_safe_file(scope_root, file)
    held = stat_optional(resolved)
    # The listing's rule, at both scopes: a campaign-scope path reaches its cycles' dot-dirs.
    if held is None or not _shown(resolved.relative_to(scope_root.resolve())):
        raise NotFoundError(f"File not found: {file}")
    raw = None if held.st_size > _MAX_PREVIEW_BYTES else read_bytes_optional(resolved)
    content_type: Literal["json", "markdown", "log", "text", "binary"] = (
        _classify_suffix(resolved.suffix.lower()) or "text"
    )
    content: str | None = None
    if raw is not None:
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content_type = "binary"
    return FileContentResponse(
        campaign_id=path[0].campaign_id,
        cycle_id=path[0].cycle_id,
        scope=scope,
        path=file,
        size=held.st_size,
        mtime=_mtime(held),
        content_type=content_type,
        content=content,
    )
