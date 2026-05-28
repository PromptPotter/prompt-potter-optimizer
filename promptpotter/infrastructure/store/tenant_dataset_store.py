"""Per-tenant user-uploaded dataset store — the M13 chat-first ingest target.

User-uploaded Origins live at ``{tenant_root}/datasets/{slug}/``; in-flight
ingests live at ``{tenant_root}/datasets/.drafts/{draft_id}/cache.json``.
Commit = atomic rename of the draft dir to ``{slug}/`` plus the four
Origin files (`cache.json`, `pipeline.json`, `task_description.md`,
`prompts/default.json`) and the sibling `campaign.json` per
``docs/specs/m13-chat-first-user-web.md § Commit path``.

Built-in benchmark datasets (`aime_2025`, `bbeh`, `gsm8k`, …) stay under
repo ``datasets/`` and are served by :class:`BackendStore`; this store
only touches the tenant tree.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.paths import validate_dataset_name

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


_DRAFTS_SUBDIR = ".drafts"


class TenantDatasetStore:
    """Reads + writes tenant-scoped Origins under ``{tenant_root}/datasets/``."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- Path helpers ---------------------------------------------------------

    def datasets_root(self) -> Path:
        """Tenant's ``datasets/`` dir — parent of every committed slug + the ``.drafts/`` sidetree."""
        return self._base_dir / "datasets"

    def dataset_dir(self, slug: str) -> Path:
        """Resolve ``{tenant_root}/datasets/{slug}``. Raises ``ValueError`` on bad slug."""
        validate_dataset_name(slug)
        return self.datasets_root() / slug

    def draft_dir(self, draft_id: str) -> Path:
        """Resolve the in-flight draft staging dir."""
        validate_path_component(draft_id)
        return self.datasets_root() / _DRAFTS_SUBDIR / draft_id

    # -- Slug registry --------------------------------------------------------

    def list_slugs(self) -> list[str]:
        """Sorted committed slugs (excludes ``.drafts/``). Each entry has a ``cache.json``."""
        root = self.datasets_root()
        if not root.is_dir():
            return []
        out: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name == _DRAFTS_SUBDIR or entry.name.startswith("."):
                continue
            if not (entry / "cache.json").is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            out.append(entry.name)
        return out

    def slug_exists(self, slug: str) -> bool:
        """Whether a committed dataset already holds this slug."""
        try:
            return self.dataset_dir(slug).is_dir()
        except ValueError:
            return False

    def suggest_free_slug(self, slug: str) -> str:
        """Return the smallest free ``{slug}-{n}`` (``n>=2``). Caller has already confirmed collision."""
        n = 2
        while True:
            candidate = f"{slug}-{n}"
            if not self.slug_exists(candidate):
                return candidate
            n += 1

    # -- Committed dataset I/O ------------------------------------------------

    def load_dataset(self, slug: str) -> dict[str, Any] | None:
        """Read ``{slug}/cache.json`` from the committed tenant tree, or ``None``."""
        return read_json_optional(self.dataset_dir(slug) / "cache.json")

    def task_description(self, slug: str) -> str | None:
        """Read ``{slug}/task_description.md`` or ``None`` when absent."""
        path = self.dataset_dir(slug) / "task_description.md"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    # -- Draft staging --------------------------------------------------------

    def write_draft_cache(
        self,
        draft_id: str,
        items: Sequence[Sample | dict[str, Any]],
        *,
        source_file: str = "",
    ) -> Path:
        """Persist the parsed sample bank for ``draft_id`` to ``.drafts/{draft_id}/cache.json``."""
        from promptpotter.domain.sample import Sample

        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        data: dict[str, Any] = {
            "name": draft_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source_file": source_file,
            "row_count": len(serialized),
            "items": serialized,
        }
        path = self.draft_dir(draft_id) / "cache.json"
        write_json(path, data)
        return path

    def load_draft_cache(self, draft_id: str) -> dict[str, Any] | None:
        """Read a draft's parsed bank, or ``None`` if the draft is gone."""
        return read_json_optional(self.draft_dir(draft_id) / "cache.json")

    def discard_draft(self, draft_id: str) -> None:
        """Best-effort removal of a draft's staging dir. Idempotent."""
        path = self.draft_dir(draft_id)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    # -- Commit ---------------------------------------------------------------

    def commit_draft(
        self,
        draft_id: str,
        *,
        slug: str,
        pipeline_json: dict[str, Any],
        campaign_json: dict[str, Any],
        task_description: str,
        prompt_default: dict[str, Any],
    ) -> Path:
        """Atomic-rename ``.drafts/{draft_id}/`` to ``{slug}/`` + materialize the Origin files.

        On collision raises ``FileExistsError`` (caller maps to 409 with a
        :meth:`suggest_free_slug` suggestion). On unknown draft raises
        ``FileNotFoundError``.
        """
        src = self.draft_dir(draft_id)
        if not src.is_dir():
            raise FileNotFoundError(f"draft {draft_id!r} not found")
        dst = self.dataset_dir(slug)
        if dst.exists():
            raise FileExistsError(slug)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Atomic on the same filesystem; the .drafts/ sidetree lives under
        # the same tenant root as {slug}/ so this never crosses devices.
        src.rename(dst)
        write_json(dst / "pipeline.json", pipeline_json)
        write_json(dst / "campaign.json", campaign_json)
        write_text(dst / "task_description.md", task_description)
        write_json(dst / "prompts" / "default.json", prompt_default)
        return dst


__all__ = ["TenantDatasetStore"]
