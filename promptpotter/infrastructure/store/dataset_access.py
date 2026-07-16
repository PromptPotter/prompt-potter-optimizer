"""Dataset access gateway — the single seam that resolves a dataset directory.

Two classes of dataset exist, and the split is by *ownership*, not by permission:

* **Tenant content** — ``projects/{tenant}/datasets/{slug}/``. Whatever this
  identity ingested. Isolated by path: a tenant's root is derived from its own
  id, so one tenant cannot name its way into another's.
* **Install content** — repo ``datasets/{name}/``. The benchmarks, demos and
  ``promptpotter-self`` that SHIP WITH THE PRODUCT. Every one of them is tracked
  in git, so anyone who has the install already has these bytes on disk.

Install content therefore carries no capability check: gating a read on data that
arrives with the checkout protects nothing, while denying it blanks every panel
bound to a benchmark campaign. A private cut is not install content and does not
belong in the repo dir — it belongs in the tenant, where path isolation already
protects it. (The predecessor of this module gated repo ``datasets/`` behind a
``datasets.benchmarks.read`` capability granted by a ``PROMPTPOTTER_ADMIN=1`` env
flag. It existed to protect one gitignored scratch cut living in the install dir,
and the cost was that the pipeline hero and the hard-sample leaderboard rendered
blank for every benchmark campaign unless that flag was set.)

Resolution is tenant-first, so a tenant may shadow an install slug with its own.

The *list* and the *resolver* share ONE rule, so the picker can never surface a
dataset the read endpoints would deny. Presentation code MUST NOT read
:attr:`Stores.benchmarks_root` directly — route every access through here; no
standing test (see ``tests/CLAUDE.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores


class DatasetAccessError(Exception):
    """No dataset *name* this identity can resolve — invalid slug, or absent.

    The router maps it to 404. Kept as one exception because there is now one
    reason: the dataset is not there. (It formerly also meant "exists but you
    may not see it"; install content no longer hides.)
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class DatasetRef:
    """One readable dataset and which tier it came from."""

    name: str
    title: str | None
    n_samples: int
    tier: str  # "yours" | "install"


def _has_config(dataset_dir: Path) -> bool:
    """A directory is a dataset iff it carries a cache or a pipeline overlay."""
    return (dataset_dir / "cache.json").is_file() or (dataset_dir / "pipeline.json").is_file()


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
    if _has_config(tenant_dir):
        return tenant_dir

    install_dir = store.benchmarks_root / name  # name validated above
    if _has_config(install_dir):
        return install_dir
    raise DatasetAccessError(name)


def _is_internal(name: str) -> bool:
    """Install dirs prefixed ``_`` are the optimizer's OWN config, not task datasets.

    ``_optimizer`` / ``_optimizer_meta`` hold the meta-prompt pipelines the loop runs
    *with*; no one mints a campaign against them. The leading underscore is the
    existing convention that says so, and the wire contract already agrees — the
    ``slug`` pattern in ``m12-api-openapi.yaml::DatasetIndexEntry`` is
    ``^[a-z][a-z0-9_-]*$``, which no ``_``-prefixed name can match. Listing one was
    always out-of-contract; it only stayed invisible while install content needed a
    capability nobody but a developer held. :func:`readable_dataset_dir` still
    resolves them by exact name, which is how the loop reads them.
    """
    return name.startswith("_")


def list_readable_datasets(store: Stores) -> list[DatasetRef]:
    """Every dataset this identity may pick — tenant content, then install content.

    A tenant slug shadows an install slug of the same name, matching the resolver's
    tenant-first precedence. Narrower than :func:`readable_dataset_dir` by exactly
    the internal dirs (:func:`_is_internal`) — narrower is safe, since the invariant
    is that the picker never surfaces a dataset the read endpoints would deny.
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
            if entry.name in own or _is_internal(entry.name):
                continue  # tenant copy already won / optimizer's own config
            if not entry.is_dir() or not (entry / "pipeline.json").is_file():
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
    "list_readable_datasets",
    "readable_dataset_dir",
]
