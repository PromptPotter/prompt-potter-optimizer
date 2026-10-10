from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from promptpotter.application.cycle_reads import view_cycle
from promptpotter.domain.cycle_paths import CyclePath
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import CampaignLayout, campaign_root_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import iso_z
from promptpotter.shared.errors import BadRequestError, ContentTooLargeError, NotFoundError

__all__ = [
    "FileContentResponse",
    "FileEntry",
    "FileScope",
    "FilesResponse",
    "list_cycle_files",
    "read_cycle_file",
]

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


def _walk_files(root: Path) -> Iterator[Path]:
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
    return iso_z(datetime.fromtimestamp(p.stat().st_mtime, UTC))


def _resolve_safe_file(scope_root: Path, raw_path: str) -> Path:
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


def list_cycle_files(stores: Stores, path: CyclePath) -> FilesResponse:
    """The ids echoed are the ROOT hop's — the address the caller named; the files are the leaf's."""
    cycle = view_cycle(stores, path)
    campaign_dir = campaign_root_dir_for(cycle.stores.base_dir, cycle.hop.campaign_id)

    entries: list[FileEntry] = []
    for f in _walk_files(cycle.dir):
        entries.append(
            FileEntry(
                path=f.relative_to(cycle.dir).as_posix(),
                scope="cycle",
                size=f.stat().st_size,
                mtime=_iso_mtime(f),
            )
        )
        if len(entries) > _MAX_FILE_ENTRIES:
            raise ContentTooLargeError(f"Too many entries in cycle dir (>{_MAX_FILE_ENTRIES})")

    for f in CampaignLayout(campaign_dir).files():
        if f.is_file():
            entries.append(
                FileEntry(
                    path=f.name,
                    scope="campaign",
                    size=f.stat().st_size,
                    mtime=_iso_mtime(f),
                )
            )

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
    size = resolved.stat().st_size
    classification = _classify_suffix(resolved.suffix.lower())
    content_type: Literal["json", "markdown", "log", "text", "binary"] = classification or "text"
    content: str | None = None
    if size > _MAX_PREVIEW_BYTES:
        content_type = "text"
    elif classification is None or classification == "text":
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content_type = "binary"
    else:
        content = resolved.read_text(encoding="utf-8")
    return FileContentResponse(
        campaign_id=path[0].campaign_id,
        cycle_id=path[0].cycle_id,
        scope=scope,
        path=file,
        size=size,
        mtime=_iso_mtime(resolved),
        content_type=content_type,
        content=content,
    )
