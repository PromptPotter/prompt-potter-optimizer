"""Per-tenant user-uploaded dataset store — the M13 chat-first ingest target.

User-uploaded Origins live at ``{tenant_root}/datasets/{slug}/``. Commit writes
``{slug}/`` fresh (:meth:`write_committed_dataset`) from the materialized samples
+ the Origin files (`cache.json`, `pipeline.yaml`, `task_description.md`,
`prompts/default.yaml`, `task_context.yaml`) and the sibling `campaign.yaml`. The
pre-commit working state (the parsed sample bank + the draft) lives under the
owning check-in campaign (``campaigns/{id}/checkin/``, :class:`CheckinDraftStore`),
not here — this store only owns committed datasets.

Built-in benchmark datasets (`aime_2025`, `bbeh`, `gsm8k`, …) stay under
repo ``datasets/`` and are served by :class:`BackendStore`; this store
only touches the tenant tree.
"""

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

    def datasets_root(self) -> Path:
        return self._base_dir / "datasets"

    def dataset_dir(self, slug: str) -> Path:
        """Resolve ``{tenant_root}/datasets/{slug}``. Raises ``ValueError`` on bad slug."""
        validate_dataset_name(slug)
        return self.datasets_root() / slug

    # -- Slug registry --------------------------------------------------------

    def list_slugs(self) -> list[str]:
        """Sorted committed slugs (excludes dotted sidetrees). Each entry has a ``cache.json``."""
        root = self.datasets_root()
        if not root.is_dir():
            return []
        out: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
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
        """Return the smallest free ``{slug}-v{n}`` (``n>=1``) — the archival name the
        old data moves to on Replace. Distinct namespace from
        :meth:`suggest_free_slug` (``-N``, a sibling copy): ``-vN`` reads as
        "an earlier version of this name", which is exactly what a version-and-
        repoint Replace produces.
        """
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
        return self.datasets_root() / ".migrations"

    # -- Committed dataset I/O ------------------------------------------------

    def load_dataset(self, slug: str) -> dict[str, Any] | None:
        return read_json_optional(self.dataset_dir(slug) / "cache.json")

    def task_description(self, slug: str) -> str | None:
        path = self.dataset_dir(slug) / "task_description.md"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    # -- Commit ---------------------------------------------------------------

    def version_dataset(self, slug: str, versioned: str) -> Path:
        """Atomic-rename a committed dataset ``{slug}/`` → ``{versioned}/`` and fix its
        self-referential ``campaign.json::campaign_config.dataset_name``.

        Step 1 of version-and-repoint Replace
        (``application/datasets/dataset_replace.py``): preserves the bytes,
        frees the canonical ``{slug}`` name for the newly-dropped data. Atomic
        on the shared tenant filesystem. **Idempotent for crash recovery** — if
        a prior run already moved the dir (``{slug}`` gone, ``{versioned}``
        present) this returns the destination instead of raising, so the
        repoint steps can re-run. Raises ``FileNotFoundError`` when neither
        exists and ``FileExistsError`` when both do (a genuinely ambiguous
        state the migration must not paper over).
        """
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
    ) -> Path:
        """Create ``datasets/{slug}/`` fresh and write the Origin files.

        The one commit mechanism for both entry points — the CLI ``new <file>``
        commit and the durable check-in Start. The bank is delivered as
        already-materialized ``Sample`` rows (the column mapping is confirmed by
        now), so the pre-commit working dir (the campaign's ``checkin/`` dir) is the
        caller's to clean up or keep as an audit breadcrumb — this writer never moves
        it. ``task_context.yaml`` is the
        run-start framing the check-in already decomposed (read at run-start instead
        of a second LLM decomposition). The candidate library is NOT written here:
        it rides the one origin-write seam (:meth:`write_candidate_library`).

        On slug collision raises ``FileExistsError`` (caller maps to 409 with a
        :meth:`suggest_free_slug` suggestion).
        """
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
        """Write/replace a committed dataset's ``candidate_library.txt`` (one entry
        per line) — the per-pipeline origin's target list, part of the origin spec,
        sourced from a drop or a build-from-dataset. The run unions it into the
        session term index. This is the SOLE origin-write seam for the library: the
        launcher calls it on every mint route (fresh-upload commit + reused-dataset
        mint), so a dropped/built library persists identically however the origin
        was minted."""
        path = self.dataset_dir(slug) / CANDIDATE_LIBRARY_FILE
        write_text(path, "\n".join(library))
        return path


__all__ = ["TenantDatasetStore"]
