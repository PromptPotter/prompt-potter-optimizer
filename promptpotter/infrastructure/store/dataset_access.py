"""Dataset access gateway — the single seam that resolves a dataset directory.

Tenant content is isolated by path; install content ships in git, so it carries **no
capability check** — gating a read on bytes that arrive with the checkout protects
nothing and blanks every panel bound to that campaign. A private cut is tenant content,
never a repo dir. Resolution is tenant-first, so a tenant may shadow an install slug, and
the *list* and the *resolver* share one rule so the picker cannot surface what the read
endpoints would deny. Presentation MUST NOT read :attr:`Stores.benchmarks_root` directly
— every access comes through here, and no standing test enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores


class DatasetAccessError(Exception):
    """No dataset *name* this identity can resolve — invalid slug, or absent.

    The router maps it to 404. One exception because there is one reason: the
    dataset is not there.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class DatasetRef:
    name: str
    title: str | None
    n_samples: int
    tier: str  # "yours" | "install"


def dataset_pipeline_path(dataset_dir: Path) -> Path:
    """The dataset's node overlay. **The one place this filename is spelled.**

    Every reader takes the resolved dir and asks here, so a rename cannot desync a
    reader from an existence probe — which is the shape that lets a dataset silently
    stop being a dataset instead of failing loudly.
    """
    return dataset_dir / "pipeline.yaml"


def is_dataset_dir(dataset_dir: Path) -> bool:
    """A directory is a dataset iff it carries a cache or a pipeline overlay.

    Public because three call sites used to re-derive it with their own literal —
    the readiness probe in the origins router among them — so "is this a dataset"
    could answer differently depending on who asked.
    """
    return (dataset_dir / "cache.json").is_file() or dataset_pipeline_path(dataset_dir).is_file()


def readable_dataset_dir(store: Stores, name: str) -> Path:
    """Resolve *name*'s directory — tenant content first, then install content.

    The one resolver every read AND every mint goes through (see the module
    docstring for why install content needs no capability). Raises
    :class:`DatasetAccessError` when *name* is an invalid slug or resolves to
    no dataset dir on either tier.
    """
    try:
        tenant_dir = store.tenant_datasets.dataset_dir(name)  # validates the slug
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if is_dataset_dir(tenant_dir):
        return tenant_dir

    install_dir = store.benchmarks_root / name  # name validated above
    if is_dataset_dir(install_dir):
        return install_dir
    raise DatasetAccessError(name)


def list_readable_datasets(store: Stores) -> list[DatasetRef]:
    """Every dataset this identity may pick — tenant content, then install content.

    A tenant slug shadows an install slug of the same name, matching the resolver's
    tenant-first precedence.

    There used to be an ``_``-prefix filter here (``_is_internal``), because the
    optimizer's own pipeline sat among the benchmark datasets as ``_optimizer`` /
    ``_optimizer_meta`` and had to be hidden from a picker that would otherwise offer
    "mint a campaign against the optimizer". That config is install content and now
    lives under the package (``config/paths.py::optimizer_assets_root``), so the
    convention it needed is gone with it: whatever sits in this tree is a dataset.
    """
    refs: list[DatasetRef] = []
    own: set[str] = set()
    for slug in store.tenant_datasets.list_slugs():
        dataset_dir = store.tenant_datasets.dataset_dir(slug)
        own.add(slug)
        refs.append(
            DatasetRef(slug, _read_title(dataset_dir), _read_n_samples(dataset_dir), "yours")
        )

    if store.benchmarks_root.is_dir():
        for entry in sorted(store.benchmarks_root.iterdir()):
            if entry.name in own:
                continue  # tenant copy already won
            if not entry.is_dir() or not dataset_pipeline_path(entry).is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            refs.append(
                DatasetRef(entry.name, _read_title(entry), _read_n_samples(entry), "install")
            )
    return refs


def _read_title(dataset_dir: Path) -> str | None:
    """First ``# `` heading of ``dataset.md``, or ``None``."""
    md = dataset_dir / "dataset.md"
    if not md.is_file():
        return None
    for line in md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _read_n_samples(dataset_dir: Path) -> int:
    """``cache.json::row_count`` (falls back to ``items`` length); ``0`` when unmaterialized."""
    raw = read_json_tolerant(dataset_dir / "cache.json")
    if not isinstance(raw, dict):
        return 0
    row_count = raw.get("row_count")
    if isinstance(row_count, int):
        return row_count
    items = raw.get("items")
    return len(items) if isinstance(items, list) else 0


__all__ = [
    "DatasetAccessError",
    "DatasetRef",
    "dataset_pipeline_path",
    "is_dataset_dir",
    "list_readable_datasets",
    "readable_dataset_dir",
]
