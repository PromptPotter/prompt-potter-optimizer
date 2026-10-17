from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    FRAMING_FILE,
    ORIGIN_FILE,
    ROWS_FILE,
    render_framing_file,
)
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    read_yaml_optional,
    write_bytes,
    write_json,
    write_text,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample

# What a definition's `source:` materialized for this tenant: fetched rows and built panels.
MATERIALIZED_DIR = "materialized"


class TenantDatasetStore:
    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def committed_datasets_root(self) -> Path:
        return self._base_dir / "datasets"

    def dataset_dir(self, slug: str) -> Path:
        validate_dataset_name(slug)
        return self.committed_datasets_root() / slug

    def list_slugs(self) -> list[str]:
        root = self.committed_datasets_root()
        if not root.is_dir():
            return []
        out: list[str] = []
        for path in sorted(root.rglob(DEFINITION_FILE)):
            name = path.parent.relative_to(root).as_posix()
            try:
                validate_dataset_name(name)
            except ValueError:
                continue
            out.append(name)
        return out

    def slug_exists(self, slug: str) -> bool:
        try:
            return self.dataset_dir(slug).is_dir()
        except ValueError:
            return False

    def suggest_free_slug(self, slug: str) -> str:
        n = 2
        while True:
            candidate = f"{slug}-{n}"
            if not self.slug_exists(candidate):
                return candidate
            n += 1

    def suggest_free_version(self, slug: str) -> str:
        n = 1
        while True:
            candidate = f"{slug}-v{n}"
            if not self.slug_exists(candidate):
                return candidate
            n += 1

    def migrations_dir(self) -> Path:
        return self.committed_datasets_root() / ".migrations"

    def load_dataset(self, slug: str) -> dict[str, Any] | None:
        return read_json_optional(self.dataset_dir(slug) / ROWS_FILE)

    def materialized_rows_path(self, name: str) -> Path:
        validate_dataset_name(name)
        return self._base_dir / MATERIALIZED_DIR / f"{name}.json"

    def load_materialized_rows(self, name: str) -> dict[str, Any] | None:
        return read_json_optional(self.materialized_rows_path(name))

    def materialized_panel_dir(self, name: str) -> Path:
        """Where a panel recipe materializes a connector panel and the task directories it names."""
        validate_dataset_name(name)
        return self._base_dir / MATERIALIZED_DIR / name

    def save_materialized_rows(self, name: str, items: Sequence[Sample | dict[str, Any]]) -> Path:
        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        path = self.materialized_rows_path(name)
        write_json(
            path,
            {
                "name": name,
                "created_at": utcnow_iso(),
                "row_count": len(serialized),
                "items": serialized,
            },
        )
        return path

    def keep_upload(self, blob: bytes, extension: str) -> Path:
        """Every upload, kept BEFORE it is parsed, so a file the parser refused or crashed on can be re-read."""
        suffix = f".{extension}" if extension.isalnum() and len(extension) <= 8 else ""
        path = self._base_dir / "uploads" / f"{hashlib.sha256(blob).hexdigest()[:16]}{suffix}"
        write_bytes(path, blob)
        return path

    def task_context_path(self, name: str) -> Path:
        validate_dataset_name(name)
        return self._base_dir / "task-context" / f"{name}.yaml"

    def load_task_context(self, name: str) -> TaskDecomposition:
        return TaskDecomposition.from_dict(read_yaml_optional(self.task_context_path(name)))

    def save_task_context(self, name: str, framing: TaskDecomposition) -> Path | None:
        """`None` on an empty record: this tier shadows the definition's committed framing, and an empty file buries it."""
        if not framing:
            return None
        path = self.task_context_path(name)
        write_yaml(path, framing.to_dict())
        return path

    def version_dataset(self, slug: str, versioned: str) -> Path:
        src = self.dataset_dir(slug)
        dst = self.dataset_dir(versioned)
        if not src.is_dir():
            if dst.is_dir():
                return dst
            raise FileNotFoundError(slug)
        if dst.exists():
            raise FileExistsError(versioned)
        src.rename(dst)
        return dst

    def write_committed_dataset(
        self,
        slug: str,
        *,
        samples: Sequence[Sample | dict[str, Any]],
        source_file: str,
        headers: Sequence[str],
        sample_order_seed: str | None = None,
    ) -> Path:
        """The rows alone: the directory is a dataset only once :meth:`write_definition` lands."""
        dst = self.dataset_dir(slug)
        if dst.exists():
            raise FileExistsError(slug)
        serialized = [s.model_dump() if isinstance(s, Sample) else s for s in samples]
        write_json(
            dst / ROWS_FILE,
            {
                "name": slug,
                "created_at": utcnow_iso(),
                "source_file": source_file,
                "headers": list(headers),
                "row_count": len(serialized),
                # Which permutation minted the ids below (`null` = delivered order); re-seeding is a re-cut, never an edit.
                "sample_order_seed": sample_order_seed,
                "items": serialized,
            },
        )
        return dst

    def write_definition(
        self,
        slug: str,
        *,
        pipeline: dict[str, Any],
        campaign: dict[str, Any],
        description: str,
        framing: TaskDecomposition,
        origin: dict[str, dict[str, Any]],
    ) -> Path:
        """WHOLE files: the tenant tier lays no defaults or family under a definition."""
        dst = self.dataset_dir(slug)
        write_text(dst / FRAMING_FILE, render_framing_file(description, framing))
        if origin:
            write_yaml(dst / ORIGIN_FILE, origin)
        # Last: the definition file is what makes the directory a dataset.
        write_yaml(dst / DEFINITION_FILE, {"pipeline": pipeline, "campaign": campaign})
        return dst

    def write_candidate_library(self, slug: str, library: Sequence[str]) -> Path:
        path = self.dataset_dir(slug) / CANDIDATE_LIBRARY_FILE
        write_text(path, "\n".join(library))
        return path


__all__ = ["MATERIALIZED_DIR", "TenantDatasetStore"]
