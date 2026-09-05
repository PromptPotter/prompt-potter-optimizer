"""Dataset access gateway — the single seam that resolves a dataset directory, tenant content
first. Install content carries NO capability check: gating bytes that ship protects nothing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.domain.search_point import has_framing
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
    n_samples: int | None
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


def backend_type_of_dataset(stores: Stores, dataset_name: str) -> str:
    """THE predicate for "which connector does this dataset use?", so no reader hand-maintains a
    list of dataset NAMES. Tolerant, unlike its strict init twin (``initialization/wiring.py``):
    a campaign outlives its dataset dir."""
    try:
        raw = read_yaml_optional(dataset_pipeline_path(readable_dataset_dir(stores, dataset_name)))
    except (OSError, ValueError, DatasetAccessError):
        return ""
    bt = (raw or {}).get("backend_type")
    return bt.lower() if isinstance(bt, str) else ""


def dataset_panel_rows(
    stores: Stores, dataset_name: str
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The panel a CONNECTOR owns — ``(rows, index_terms)`` — or ``None`` where this box has no
    connector-owned panel to read.

    THE reader of an ``experiment_file`` (harbor's task panel, L4's inner benchmark), and it sits
    beside :func:`readable_dataset_rows` because the two ARE the one ladder this module promises:
    a resolver that knows only materialized banks answers EMPTY for a connector-owned one, which
    is not a fact about the dataset. Panel ORDER is the ``sample_id`` (``samples_from_dicts``
    numbers positionally), so a second ordering would misfile every row against ``measurements/``.

    **``None`` and the raise are the two halves of one distinction, and it is NOT
    present-vs-absent.** ``None`` says "nothing here to read" — no connector, no declared panel,
    or a declared panel this machine has not generated; every caller answers all three the same
    way, by falling through to the materialized reader. The raise says "there is a panel and it is
    WRONG", which no caller may render as an empty roster. `harbor_tasks.yaml` is gitignored and
    rebuilt per machine, so a missing one is the ordinary state of a fresh clone: raising on it
    turned a not-yet-generated panel into a 4xx on the dataset preview."""
    connector = connectors.CONNECTORS.get(backend_type_of_dataset(stores, dataset_name))
    if connector is None or not connector.experiment_file:
        return None
    config_dir = readable_dataset_dir(stores, dataset_name)
    panel_path = config_dir / connector.experiment_file
    if not panel_path.is_file():
        return None
    data = read_yaml_optional(panel_path)
    if not data:
        raise ValueError(
            f"Connector {connector.name!r} expects {connector.experiment_file!r} in the "
            f"dataset config dir ({config_dir}), but the file is empty."
        )
    try:
        return connector.extract_experiment(data)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        # A shape the connector did not expect. Re-raised as `ValueError` for the same reason
        # `read_yaml` is: every guard above this seam is `except (ValueError, OSError, ImportError)`,
        # and a raw `KeyError` from a connector walks through all of them into a 500.
        raise ValueError(
            f"Connector {connector.name!r} could not read {connector.experiment_file!r} in "
            f"{config_dir}: {type(exc).__name__}: {exc}"
        ) from exc


def readable_dataset_rows(stores: Stores, name: str) -> dict[str, Any] | None:
    """The materialized rows for *name*, or ``None`` — the row half of the resolver, on the same
    tenant-first ladder: the committed dataset's own ``cache.json``, this tenant's fetch, ours.
    A connector-owned panel materializes nothing and answers here as ``None``; ask
    :func:`dataset_panel_rows` first wherever the question is "what is this dataset's bank"."""
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
        # The VALUES, never the dict: an all-empty record at a higher tier shadows the shipped
        # file under it permanently. One predicate for that, shared with both writers.
        if isinstance(candidate, dict) and has_framing(candidate):
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


def _read_n_samples(stores: Stores, name: str) -> int | None:
    """``row_count`` off the resolved rows (falls back to ``items`` length); ``None`` when
    unmaterialized — a benchmark nobody has fetched yet. NOT ``0``, which is the one state that
    should stop an operator from minting an origin on it. Connector-owned panels answer off their
    own declaration, so a harbor dataset counts here rather than reading as unmaterialized."""
    try:
        if (panel := dataset_panel_rows(stores, name)) is not None:
            return len(panel[0])
    except (ValueError, OSError, ImportError):
        return None
    raw = readable_dataset_rows(stores, name)
    if raw is None:
        return None
    row_count = raw.get("row_count")
    if isinstance(row_count, int):
        return row_count
    items = raw.get("items")
    return len(items) if isinstance(items, list) else None


__all__ = [
    "DatasetAccessError",
    "backend_type_of_dataset",
    "dataset_panel_rows",
    "dataset_pipeline_path",
    "is_dataset_dir",
    "list_readable_datasets",
    "readable_dataset_dir",
    "readable_dataset_rows",
    "readable_task_context",
]
