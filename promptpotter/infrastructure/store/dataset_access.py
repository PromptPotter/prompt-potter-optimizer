"""Dataset access gateway — the single seam that resolves a dataset directory.

Tenant content is isolated by path; install content carries **no capability check** —
gating a read on bytes that ship with the software protects nothing and blanks every
panel bound to that campaign. That rationale turns on DISTRIBUTION, not on git: since
2026-07-30 the benchmark definitions ship inside the wheel as well as in the checkout
(``config/paths.py::benchmark_datasets_root``), and the argument is unchanged either way
— anyone holding the artifact already holds the bytes. A private cut is tenant content,
never an install dir. Resolution is tenant-first, so a tenant may shadow an install slug, and
the *list* and the *resolver* share one rule so the picker cannot surface what the read
endpoints would deny. Presentation MUST NOT read :attr:`Stores.benchmarks_root` directly
— every access comes through here, and no standing test enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.io import read_json_tolerant, read_yaml_optional
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError


class DatasetAccessError(NotFoundError):
    """No dataset *name* this identity can resolve — invalid slug, or absent.

    A :class:`NotFoundError`, so the central ``PotterError`` handler maps it to
    404 with no per-route arm. Two routers used to catch it and rebuild that same
    404 by hand. One exception because there is one reason: the dataset is not
    there.

    404 rather than 403 is the existence-leak posture, and it belongs here rather
    than at a router: a non-admin must not be able to tell a benchmark they may not
    read from a dataset that does not exist.
    """

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
    """The dataset's node overlay. **The one place this filename is spelled.**

    Every reader takes the resolved dir and asks here, so a rename cannot desync a
    reader from an existence probe — which is the shape that lets a dataset silently
    stop being a dataset instead of failing loudly.
    """
    return dataset_dir / "pipeline.yaml"


def dataset_task_context_path(dataset_dir: Path) -> Path:
    """The dataset's run-start framing — spelled once for every READER, same rule as
    :func:`dataset_pipeline_path`, across all three tiers it can resolve from (see
    :func:`readable_task_context`). The commit writer keeps its own literal: it lives
    below this module in the import order and cannot reach up to it."""
    return dataset_dir / "task_context.yaml"


def is_dataset_dir(dataset_dir: Path) -> bool:
    """A directory is a dataset iff it carries a pipeline overlay.

    Public because three call sites used to re-derive it with their own literal —
    the readiness probe in the origins router among them — so "is this a dataset"
    could answer differently depending on who asked.

    A materialized ``cache.json`` used to count too, which made this disagree with
    :func:`list_readable_datasets` (that one has always asked for the overlay): a
    row bank with no definition listed nowhere yet resolved here. Rows now live
    outside the dir entirely, so the definition is the only thing left to ask for.
    """
    return dataset_pipeline_path(dataset_dir).is_file()


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


def readable_dataset_rows(store: Stores, name: str) -> dict[str, Any] | None:
    """The materialized rows for *name*, or ``None`` — the row half of the resolver.

    A dataset dir is a DEFINITION (ours under a wheel, read-only) and its rows are a
    MEASUREMENT INPUT the operator materialized (fetched, regenerable, 6.8 MB). They
    used to share a directory, which is the only reason the install tier ever had to
    be writable — and writable it is not, once the definitions ship inside
    ``site-packages``. So they are resolved separately, in the same tenant-first order
    the dir resolver uses:

    1. A committed tenant dataset's own ``cache.json`` — authored at commit time,
       inside a dir the tenant owns outright.
    2. This tenant's ``benchmark-rows/{name}.json`` — what
       ``resolve_dataset_items`` persisted after fetching.
    3. The install dir's shipped ``cache.json``. One dataset ships rows —
       ``email-tagging``'s hand-authored demo samples — and it seeds a machine that
       has fetched nothing. Read-only, like everything else on that tier.
    """
    try:
        tenant = store.tenant_datasets.load_dataset(name)
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if tenant and tenant.get("items"):
        return tenant
    rows = store.tenant_datasets.load_benchmark_rows(name)
    if rows and rows.get("items"):
        return rows
    shipped = read_json_tolerant(store.benchmarks_root / name / "cache.json")
    return shipped if isinstance(shipped, dict) and shipped.get("items") else None


def readable_task_context(store: Stores, name: str) -> dict[str, Any] | None:
    """The task framing for *name*, or ``None`` — resolved like the rows, and for the
    same reason.

    ``task_context.yaml`` is DERIVED from the definition's ``task_description.md`` by
    an LLM call the operator paid for. That makes it theirs, not ours, so it resolves
    on its own tenant-first ladder rather than being written back beside a definition
    that is read-only under a wheel:

    1. A committed tenant dataset's own ``task_context.yaml`` — written at commit
       from the check-in's decomposition, inside a dir the tenant owns outright.
    2. This tenant's ``task-context/{name}.yaml`` — what
       :func:`~promptpotter.application.optimization.task_context.load_or_build_task_context`
       persisted after decomposing on first sight.
    3. The install dir's shipped ``task_context.yaml``. Six of the ten benchmarks
       ship one; the other four are what made the missing tier-split visible.
    """
    try:
        tenant_dir = store.tenant_datasets.dataset_dir(name)  # validates the slug
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    for candidate in (
        read_yaml_optional(dataset_task_context_path(tenant_dir)),
        store.tenant_datasets.load_task_context(name),
        read_yaml_optional(dataset_task_context_path(store.benchmarks_root / name)),
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


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
            DatasetRef(slug, _read_title(dataset_dir), _read_n_samples(store, slug), "yours")
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
                DatasetRef(
                    entry.name, _read_title(entry), _read_n_samples(store, entry.name), "install"
                )
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


def _read_n_samples(store: Stores, name: str) -> int:
    """``row_count`` off the resolved rows (falls back to ``items`` length); ``0`` when
    unmaterialized — a benchmark nobody has fetched yet, or a pipeline-only L4 dataset."""
    raw = readable_dataset_rows(store, name)
    if raw is None:
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
    "dataset_task_context_path",
    "is_dataset_dir",
    "list_readable_datasets",
    "readable_dataset_dir",
    "readable_dataset_rows",
    "readable_task_context",
]
