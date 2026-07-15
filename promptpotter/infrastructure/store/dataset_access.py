"""Identity-aware dataset access gateway — the single seam for reading a
dataset directory through the API trust boundary.

Tenant Origins (``projects/{tenant}/datasets/{slug}/``) are always readable by
their owner. Install-global benchmarks (``datasets/{name}/``) are readable only
by an identity holding :data:`BENCHMARKS_READ_CAP` (the registered operator,
pinned at the OIDC seam). The *list* of readable datasets and the per-dataset
directory *resolver* share ONE rule, so the picker can never surface a dataset
the read endpoints would deny — and no handler can reach a benchmark directory
without the capability.

Why a gateway and not an inline check per handler: capability enforcement that
lives at each call site is only as strong as the least careful handler. Routing
every dataset-directory access through here makes the check structural — a route
that does not go through the gateway cannot resolve a dataset at all.
Presentation code MUST NOT read :attr:`Stores.benchmarks_root` directly —
the structural guard is routing every access through this gateway; no
standing test (see ``tests/CLAUDE.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.identity import BENCHMARKS_READ_CAP, has_capability

# Built-in try-and-learn demo datasets (repo ``datasets/{slug}/``). Surfaced to a
# user while ``User.demo_mode_enabled`` is on — independent of the benchmark
# capability, so a brand-new signup can try the product without being an admin.
DEMO_DATASET_SLUGS: tuple[str, ...] = ("email-tagging",)


class DatasetAccessError(Exception):
    """The identity may not read *name* — absent, or a benchmark sans capability.

    Both reasons collapse to one exception (and the router maps it to 404) so the
    response never distinguishes "no such dataset" from "exists but you may not
    see it" — the existence-leak posture the rest of the API already takes.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class DatasetRef:
    """One readable dataset and which tier granted it visibility."""

    name: str
    title: str | None
    n_samples: int
    tier: str  # "yours" | "benchmark" | "demo"


def _has_config(dataset_dir: Path) -> bool:
    """A directory is a dataset iff it carries a cache or a pipeline overlay."""
    return (dataset_dir / "cache.json").is_file() or (dataset_dir / "pipeline.json").is_file()


def _demo_mode_on(store: Stores) -> bool:
    """Whether this identity has the try-and-learn demo origins switched on."""
    user = store.users.get_or_create(
        user_id=str(store.identity.user_id),
        tenant_id=str(store.identity.tenant_id),
    )
    return user.demo_mode_enabled


def readable_dataset_dir(store: Stores, name: str) -> Path:
    """Resolve the directory for *name* the identity is permitted to read.

    The visibility policy, in one place (shared with :func:`list_readable_datasets`):
    tenant Origin first; then a demo origin while demo mode is on; then an install
    benchmark with :data:`BENCHMARKS_READ_CAP`. Raises :class:`DatasetAccessError`
    when *name* is invalid, absent, or a dataset this identity may not see.
    """
    try:
        tenant_dir = store.tenant_datasets.dataset_dir(name)  # validates the slug
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if _has_config(tenant_dir):
        return tenant_dir

    # Demo origins are gated by the user's demo flag, NOT the benchmark capability,
    # so a brand-new (non-admin) signup can still open them.
    if name in DEMO_DATASET_SLUGS:
        demo_dir = store.benchmarks_root / name  # name validated above
        if _demo_mode_on(store) and _has_config(demo_dir):
            return demo_dir
        raise DatasetAccessError(name)

    if has_capability(store.identity, BENCHMARKS_READ_CAP):
        benchmark_dir = store.benchmarks_root / name  # name validated above
        if _has_config(benchmark_dir):
            return benchmark_dir
    raise DatasetAccessError(name)


def list_readable_datasets(store: Stores) -> list[DatasetRef]:
    """Every dataset the identity may read — the same policy as :func:`readable_dataset_dir`.

    Tenant Origins always; install benchmarks only with :data:`BENCHMARKS_READ_CAP`
    (never the demo slugs); the demo origins while demo mode is on.
    """
    refs: list[DatasetRef] = []
    for slug in store.tenant_datasets.list_slugs():
        dataset_dir = store.tenant_datasets.dataset_dir(slug)
        refs.append(
            DatasetRef(slug, _read_title(dataset_dir), _read_n_samples(dataset_dir), "yours")
        )

    if has_capability(store.identity, BENCHMARKS_READ_CAP) and store.benchmarks_root.is_dir():
        for entry in sorted(store.benchmarks_root.iterdir()):
            if entry.name in DEMO_DATASET_SLUGS:
                continue  # demo origins surface via the flag below, not the benchmark tier
            if not entry.is_dir() or not (entry / "pipeline.json").is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            refs.append(
                DatasetRef(entry.name, _read_title(entry), _read_n_samples(entry), "benchmark")
            )

    if _demo_mode_on(store):
        for slug in DEMO_DATASET_SLUGS:
            demo_dir = store.benchmarks_root / slug
            if (demo_dir / "pipeline.json").is_file():
                refs.append(
                    DatasetRef(slug, _read_title(demo_dir), _read_n_samples(demo_dir), "demo")
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
    "DEMO_DATASET_SLUGS",
    "DatasetAccessError",
    "DatasetRef",
    "list_readable_datasets",
    "readable_dataset_dir",
]
