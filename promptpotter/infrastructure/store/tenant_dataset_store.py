"""Per-tenant user-uploaded dataset store — committed Origins at ``{tenant_root}/datasets/{slug}/``.
The pre-commit working state lives under the check-in campaign, never here."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    read_yaml_optional,
    write_json,
    write_text,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


class TenantDatasetStore:
    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- Path helpers ---------------------------------------------------------

    def committed_datasets_root(self) -> Path:
        """This tenant's OWN dataset tree — read-write, theirs outright. Named apart from
        ``Stores.benchmarks_root``, since one identifier named two opposite write semantics."""
        return self._base_dir / "datasets"

    def dataset_dir(self, slug: str) -> Path:
        validate_dataset_name(slug)
        return self.committed_datasets_root() / slug

    # -- Slug registry --------------------------------------------------------

    def list_slugs(self) -> list[str]:
        """Sorted committed slugs (excludes dotted sidetrees), keyed on the same file
        :func:`is_dataset_dir` asks for — so listing and resolver cannot disagree."""
        root = self.committed_datasets_root()
        if not root.is_dir():
            return []
        out: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if not (entry / "pipeline.yaml").is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            out.append(entry.name)
        return out

    def slug_exists(self, slug: str) -> bool:
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

    def suggest_free_version(self, slug: str) -> str:
        """The smallest free ``{slug}-v{n}``, the archival name old data moves to on Replace.
        A distinct namespace from :meth:`suggest_free_slug`, whose ``-N`` is a sibling copy."""
        n = 1
        while True:
            candidate = f"{slug}-v{n}"
            if not self.slug_exists(candidate):
                return candidate
            n += 1

    # -- migration sidetree ---------------------------------------------------

    def migrations_dir(self) -> Path:
        """Resumable-migration marker home (``datasets/.migrations/``). Hidden, so
        :meth:`list_slugs` already skips it — a marker is never a dataset."""
        return self.committed_datasets_root() / ".migrations"

    # -- Committed dataset I/O ------------------------------------------------

    def load_dataset(self, slug: str) -> dict[str, Any] | None:
        return read_json_optional(self.dataset_dir(slug) / "cache.json")

    # -- Benchmark rows -------------------------------------------------------

    def benchmark_rows_path(self, name: str) -> Path:
        validate_dataset_name(name)
        return self._base_dir / "benchmark-rows" / f"{name}.json"

    def load_benchmark_rows(self, name: str) -> dict[str, Any] | None:
        return read_json_optional(self.benchmark_rows_path(name))

    def save_benchmark_rows(self, name: str, items: Sequence[Sample | dict[str, Any]]) -> Path:
        """Persist a fetched benchmark's rows. The one writer of materialized rows
        outside a committed dataset's own dir."""
        from promptpotter.domain.sample import Sample

        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        path = self.benchmark_rows_path(name)
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

    # -- Derived task context -------------------------------------------------

    def task_context_path(self, name: str) -> Path:
        validate_dataset_name(name)
        return self._base_dir / "task-context" / f"{name}.yaml"

    def load_task_context(self, name: str) -> dict[str, Any] | None:
        return read_yaml_optional(self.task_context_path(name))

    def save_task_context(self, name: str, data: dict[str, Any]) -> Path:
        """Persist a first-sight decomposition. The one writer of derived framing
        outside a committed dataset's own dir."""
        path = self.task_context_path(name)
        write_yaml(path, data)
        return path

    def task_description(self, slug: str) -> str | None:
        path = self.dataset_dir(slug) / "task_description.md"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    # -- Commit ---------------------------------------------------------------

    def version_dataset(self, slug: str, versioned: str) -> Path:
        """Atomic-rename ``{slug}/`` → ``{versioned}/`` and fix its self-referential dataset name.
        Idempotent for crash recovery; raises when neither or both exist, never papering over it."""
        src = self.dataset_dir(slug)
        dst = self.dataset_dir(versioned)
        if not src.is_dir():
            if dst.is_dir():
                return dst  # already versioned by a prior (crashed) run
            raise FileNotFoundError(slug)
        if dst.exists():
            raise FileExistsError(versioned)
        src.rename(dst)
        self._rewrite_campaign_self_ref(dst, versioned)
        return dst

    def _rewrite_campaign_self_ref(self, dataset_dir: Path, new_name: str) -> None:
        """Point the moved dataset's own ``campaign.json`` at its new name, so a later
        ``draft_from_dataset`` off the versioned copy doesn't resurrect the old slug."""
        path = dataset_dir / "campaign.yaml"
        data = read_yaml_optional(path)
        if not isinstance(data, dict):
            return
        cc = data.get("campaign_config")
        if isinstance(cc, dict) and cc.get("dataset_name"):
            cc["dataset_name"] = new_name
            write_yaml(path, data)

    def write_committed_dataset(
        self,
        slug: str,
        *,
        samples: Sequence[Sample | dict[str, Any]],
        source_file: str,
        headers: Sequence[str],
        pipeline_json: dict[str, Any],
        campaign_json: dict[str, Any],
        task_description: str,
        prompt_default: dict[str, Any],
        task_context: dict[str, Any],
        sample_order_seed: str | None = None,
    ) -> Path:
        """Create ``datasets/{slug}/`` fresh and write the Origin files — the one commit mechanism
        for both entry points. The candidate library rides :meth:`write_candidate_library`."""
        from promptpotter.domain.sample import Sample

        dst = self.dataset_dir(slug)
        if dst.exists():
            raise FileExistsError(slug)
        dst.mkdir(parents=True, exist_ok=True)
        serialized = [s.model_dump() if isinstance(s, Sample) else s for s in samples]
        write_json(
            dst / "cache.json",
            {
                "name": slug,
                "created_at": utcnow_iso(),
                "source_file": source_file,
                "headers": list(headers),
                "row_count": len(serialized),
                # WHICH permutation minted the ids below (`csv_ingest.materialize_samples`);
                # `null` = the rows sit in the order the operator delivered them. Recorded
                # because those ids are the measurement cache key, so the ordering is part of
                # the origin and re-seeding is a re-cut onto a NEW dataset, never an edit.
                "sample_order_seed": sample_order_seed,
                "items": serialized,
            },
        )
        write_yaml(dst / "pipeline.yaml", pipeline_json)
        write_yaml(dst / "campaign.yaml", campaign_json)
        write_text(dst / "task_description.md", task_description)
        write_yaml(dst / "task_context.yaml", task_context)
        write_yaml(dst / "prompts" / "default.yaml", prompt_default)
        return dst

    def write_candidate_library(self, slug: str, library: Sequence[str]) -> Path:
        """The SOLE origin-write seam for ``candidate_library.txt``, called on every mint route, so
        a dropped or built library persists identically however the origin was minted."""
        path = self.dataset_dir(slug) / CANDIDATE_LIBRARY_FILE
        write_text(path, "\n".join(library))
        return path


__all__ = ["TenantDatasetStore"]
