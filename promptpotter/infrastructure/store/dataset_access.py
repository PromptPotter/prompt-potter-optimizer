"""Dataset access gateway — the single seam that resolves a dataset directory, tenant content
first. Install content carries NO capability check: gating bytes that ship protects nothing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.io import read_json_tolerant, read_yaml_optional
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError


class DatasetAccessError(NotFoundError):
    """No dataset *name* this identity can resolve — invalid slug, or absent. A
    :class:`NotFoundError`: 404 rather than 403 is the existence-leak posture."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Dataset '{name}' not found")
        self.name = name


@dataclass(frozen=True)
class DatasetRef:
    name: str
    title: str | None
    n_samples: int
    tier: str  # "yours" | "install"


def dataset_pipeline_path(dataset_dir: Path) -> Path:
    """The dataset's node overlay. **The one place this filename is spelled** — a rename cannot
    desync a reader from an existence probe, which is how a dataset stops being one silently."""
    return dataset_dir / "pipeline.yaml"


def dataset_task_context_path(dataset_dir: Path) -> Path:
    """The dataset's run-start framing, spelled once for every READER across all three tiers.
    The commit writer keeps its own literal: it sits below this module in the import order."""
    return dataset_dir / "task_context.yaml"


def is_dataset_dir(dataset_dir: Path) -> bool:
    """A directory is a dataset iff it carries a pipeline overlay — public so no call site
    re-derives it. A materialized ``cache.json`` does NOT count; rows live outside the dir."""
    return dataset_pipeline_path(dataset_dir).is_file()


def readable_dataset_dir(stores: Stores, name: str) -> Path:
    """Resolve *name*'s directory — tenant content first, then install content. The one resolver
    every read AND every mint goes through; raises :class:`DatasetAccessError` on neither tier."""
    try:
        tenant_dir = stores.tenant_datasets.dataset_dir(name)  # validates the slug
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if is_dataset_dir(tenant_dir):
        return tenant_dir

    install_dir = stores.benchmarks_root / name  # name validated above
    if is_dataset_dir(install_dir):
        return install_dir
    raise DatasetAccessError(name)


def readable_dataset_rows(stores: Stores, name: str) -> dict[str, Any] | None:
    """The materialized rows for *name*, or ``None`` — the row half of the resolver, on the same
    tenant-first ladder: the committed dataset's own ``cache.json``, this tenant's fetch, ours."""
    try:
        tenant = stores.tenant_datasets.load_dataset(name)
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if tenant and tenant.get("items"):
        return tenant
    rows = stores.tenant_datasets.load_benchmark_rows(name)
    if rows and rows.get("items"):
        return rows
    shipped = read_json_tolerant(stores.benchmarks_root / name / "cache.json")
    return shipped if isinstance(shipped, dict) and shipped.get("items") else None


def readable_task_context(stores: Stores, name: str) -> dict[str, Any] | None:
    """The task framing for *name*, or ``None`` — resolved like the rows and for the same reason:
    an LLM decomposition the operator paid for is theirs, so it never lands beside a definition."""
    try:
        tenant_dir = stores.tenant_datasets.dataset_dir(name)  # validates the slug
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    for candidate in (
        read_yaml_optional(dataset_task_context_path(tenant_dir)),
        stores.tenant_datasets.load_task_context(name),
        read_yaml_optional(dataset_task_context_path(stores.benchmarks_root / name)),
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


def list_readable_datasets(stores: Stores) -> list[DatasetRef]:
    """Every dataset this identity may pick, tenant slugs shadowing install ones. No name filter
    is needed — whatever sits in this tree is a dataset, and the optimizer's own is not here."""
    refs: list[DatasetRef] = []
    own: set[str] = set()
    for slug in stores.tenant_datasets.list_slugs():
        dataset_dir = stores.tenant_datasets.dataset_dir(slug)
        own.add(slug)
        refs.append(
            DatasetRef(slug, _read_title(dataset_dir), _read_n_samples(stores, slug), "yours")
        )

    if stores.benchmarks_root.is_dir():
        for entry in sorted(stores.benchmarks_root.iterdir()):
            if entry.name in own:
                continue  # tenant copy already won
            if not entry.is_dir() or not dataset_pipeline_path(entry).is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            refs.append(
                DatasetRef(
                    entry.name, _read_title(entry), _read_n_samples(stores, entry.name), "install"
                )
            )
    return refs


def _read_title(dataset_dir: Path) -> str | None:
    md = dataset_dir / "dataset.md"
    if not md.is_file():
        return None
    for line in md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _read_n_samples(stores: Stores, name: str) -> int:
    """``row_count`` off the resolved rows (falls back to ``items`` length); ``0`` when
    unmaterialized — a benchmark nobody has fetched yet, or a pipeline-only L4 dataset."""
    raw = readable_dataset_rows(stores, name)
    if raw is None:
        return 0
    row_count = raw.get("row_count")
    if isinstance(row_count, int):
        return row_count
    items = raw.get("items")
    return len(items) if isinstance(items, list) else 0


__all__ = [
    "DatasetAccessError",
    "dataset_pipeline_path",
    "is_dataset_dir",
    "list_readable_datasets",
    "readable_dataset_dir",
    "readable_dataset_rows",
    "readable_task_context",
]
