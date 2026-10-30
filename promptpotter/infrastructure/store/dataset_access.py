"""Install content carries NO capability check: gating bytes that ship protects nothing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from promptpotter import connectors
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import DEFAULT_PIPELINE
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.infrastructure.store.read_model import file_sig
from promptpotter.shared.errors import DatasetAccessError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

DEFINITION_FILE = "dataset.yaml"
FAMILY_FILE = "family.yaml"
DEFAULTS_FILE = "defaults.yaml"
FRAMING_FILE = "task.md"
ORIGIN_FILE = "origin.yaml"
README_FILE = "README.md"
MATERIALIZED_PANEL_FILE = "panel.yaml"

DatasetTier = Literal["yours", "install"]


@dataclass(frozen=True)
class DatasetRef:
    dataset: ResolvedDataset
    title: str | None
    summary: str | None


def is_dataset_dir(dataset_dir: Path) -> bool:
    return (dataset_dir / DEFINITION_FILE).is_file()


def _holds_definition(directory: Path) -> bool:
    return is_dataset_dir(directory) or (directory / FAMILY_FILE).is_file()


def definition_chain(dataset_dir: Path) -> list[Path]:
    """The directories a definition is laid from, root first: the nearest ancestor holding
    ``defaults.yaml`` (the install root), every directory below it holding ``family.yaml`` or a
    ``dataset.yaml``, then the leaf; a plain directory between them is a path segment, never a
    layer. With no such ancestor the leaf is whole — no tenant writes a family or a default."""
    for depth, root in enumerate(dataset_dir.parents):
        if (root / DEFAULTS_FILE).is_file():
            between = list(dataset_dir.parents)[:depth][::-1]
            return [root, *(d for d in between if _holds_definition(d)), dataset_dir]
    return [dataset_dir]


def _layer_file(directory: Path, leaf: Path) -> Path:
    if directory == leaf or is_dataset_dir(directory):
        return directory / DEFINITION_FILE
    return directory / (FAMILY_FILE if (directory / FAMILY_FILE).is_file() else DEFAULTS_FILE)


def definition_files(dataset_dir: Path) -> list[Path]:
    """Every file a definition reads, so a cache keyed on them sees any layer's edit."""
    return [_layer_file(d, dataset_dir) for d in definition_chain(dataset_dir)]


def _layers(dataset_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in definition_files(dataset_dir):
        data = read_yaml_optional(path)
        if data is None:
            continue
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a mapping of sections")
        out.append((path, data))
    return out


def _section(layer: Mapping[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = layer.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: `{key}` must be a mapping")
    return value


@dataclass(frozen=True)
class DatasetDefinition:
    directory: Path
    pipeline: dict[str, Any]
    # Root first; application folds them by `campaign_config.py::merge_config_layers`.
    campaign_layers: tuple[dict[str, Any], ...]
    source: dict[str, Any]
    panel: dict[str, Any] | None
    # The deepest layer declaring a panel key: a local path the panel names resolves against it.
    panel_dir: Path | None

    @property
    def backend_type(self) -> str:
        """The ONE reading of ``pipeline.backend_type``: lowercased, ``""`` where none is declared."""
        declared = self.pipeline.get("backend_type")
        return declared.lower() if isinstance(declared, str) else ""


def read_dataset_definition(dataset_dir: Path) -> DatasetDefinition | None:
    """Per section, root first: ``pipeline`` replaces its top-level keys and merges its nodes,
    ``campaign`` layers depth-first, ``source`` and ``panel`` merge by key. A node in
    ``defaults.yaml`` is a TEMPLATE: only a node the dataset or a family declares receives it."""
    if not is_dataset_dir(dataset_dir):
        return None
    layers = _layers(dataset_dir)
    top: dict[str, Any] = {}
    nodes: dict[str, Any] = {}
    templates: dict[str, Any] = {}
    campaign: list[dict[str, Any]] = []
    source: dict[str, Any] = {}
    panel: dict[str, Any] = {}
    panel_dir: Path | None = None
    for path, layer in layers:
        pipeline = _section(layer, "pipeline", path)
        declared = pipeline.get("nodes") or {}
        if path.name == DEFAULTS_FILE:
            templates = dict(declared)
        else:
            nodes = merge_node_blocks(nodes, declared)
        top |= {k: v for k, v in pipeline.items() if k != "nodes"}
        campaign.append(_section(layer, "campaign", path))
        source |= _section(layer, "source", path)
        if "panel" in layer:
            panel |= _section(layer, "panel", path)
            panel_dir = path.parent
    if nodes:
        top["nodes"] = {
            n: merge_node_blocks({n: templates[n]}, {n: block})[n] if n in templates else block
            for n, block in nodes.items()
        }
    return DatasetDefinition(dataset_dir, top, tuple(campaign), source, panel or None, panel_dir)


def read_dataset_pipeline(dataset_dir: Path) -> dict[str, Any] | None:
    definition = read_dataset_definition(dataset_dir)
    return None if definition is None else definition.pipeline


def declared_pipelines(declaration: Mapping[str, Any]) -> dict[str, list[str]]:
    """A ``default`` left undeclared is every declared node in order, as the schema parser reads it."""
    nodes = declaration.get("nodes") or {}
    declared = {
        str(name): [str(step) for step in steps or []]
        for name, steps in (declaration.get("pipelines") or {}).items()
    }
    return ({DEFAULT_PIPELINE: [str(node) for node in nodes]} if nodes else {}) | declared


def nearest_file(dataset_dir: Path, filename: str) -> Path | None:
    """The dataset's own file, else its nearest family's: one framing, origin or README per kind."""
    return next(
        (
            path
            for directory in reversed(definition_chain(dataset_dir))
            if (path := directory / filename).is_file()
        ),
        None,
    )


_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)^---\r?\n?", re.DOTALL | re.MULTILINE)


@dataclass(frozen=True)
class DatasetFraming:
    description: str
    # The front matter decomposed; blank where none is committed, which leaves the body to decompose.
    framing: TaskDecomposition


def read_framing_file(path: Path | None) -> DatasetFraming:
    if path is None:
        return DatasetFraming("", TaskDecomposition())
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER.match(text)
    if m is None:
        return DatasetFraming(text.strip(), TaskDecomposition())
    try:
        fields = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: front matter is not valid YAML — {exc}") from exc
    if not isinstance(fields, dict):
        raise ValueError(f"{path}: front matter must be a mapping")
    body = text[m.end() :].strip()
    framing = TaskDecomposition.from_dict(fields)
    # Blank fields commit nothing: the shape `render_framing_file` writes, read back the same.
    if not framing:
        return DatasetFraming(body, TaskDecomposition())
    return DatasetFraming(body, TaskDecomposition.from_dict({**fields, "raw_description": body}))


def read_dataset_framing(dataset_dir: Path) -> DatasetFraming:
    return read_framing_file(nearest_file(dataset_dir, FRAMING_FILE))


def render_framing_file(description: str, framing: TaskDecomposition) -> str:
    """The one writer of ``task.md``'s shape: front matter only where a field is set."""
    body = description.strip() + "\n"
    fields = {k: v for k, v in framing.to_dict().items() if k != "raw_description" and v}
    if not fields:
        return body
    head = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=100)
    return f"---\n{head}---\n{body}"


def read_dataset_origin(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """Starting prompts keyed by node; a family's entry is the default for a node it names."""
    out: dict[str, dict[str, Any]] = {}
    for directory in definition_chain(dataset_dir):
        path = directory / ORIGIN_FILE
        data = read_yaml_optional(path)
        if data is None:
            continue
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise ValueError(f"{path} must map each node name to its starting prompt")
        out |= data
    return out


@dataclass(frozen=True)
class ResolvedDataset:
    """A dataset name resolved ONCE to the tier that holds it: every read of the dataset takes this,
    so none re-decides which directory answers."""

    name: str
    directory: Path
    tier: DatasetTier


def resolve_dataset(stores: Stores, name: str) -> ResolvedDataset:
    """Tenant first: an upload of the same name shadows the install's."""
    try:
        tenant_dir = stores.tenant_datasets.dataset_dir(name)
    except ValueError as exc:
        raise DatasetAccessError(name) from exc
    if is_dataset_dir(tenant_dir):
        return ResolvedDataset(name, tenant_dir, "yours")
    install_dir = stores.install_datasets_root / name
    if is_dataset_dir(install_dir):
        return ResolvedDataset(name, install_dir, "install")
    raise DatasetAccessError(name)


def definition_version(stores: Stores, name: str) -> tuple[object, ...]:
    """Stats only: which tier *name* resolves to, and every file its definition reads there."""
    try:
        dataset = resolve_dataset(stores, name)
    except DatasetAccessError:
        return (name, None)
    files = definition_files(dataset.directory)
    return (name, str(dataset.directory), *(file_sig(path) for path in files))


def declared_backend_type(dataset_dir: Path) -> str:
    """Raises typed: a bare ``ValueError`` becomes a 500 the webapp retries forever."""
    path = dataset_dir / DEFINITION_FILE
    try:
        definition = read_dataset_definition(dataset_dir)
    except ValueError as exc:
        raise PayloadInvalidError(
            f"{path} is malformed: {exc}",
            code="pipeline_config_invalid",
            details={"dataset_dir": str(dataset_dir)},
        ) from exc
    if definition is None or not definition.backend_type:
        raise PayloadInvalidError(
            f"backend_type missing or empty in {path}",
            code="pipeline_config_invalid",
            details={"dataset_dir": str(dataset_dir)},
        )
    return definition.backend_type


def backend_type_of_dataset(stores: Stores, dataset_name: str) -> str:
    """Tolerant, a SURVEY's: a campaign outlives its dataset, and one bad file fails no listing."""
    try:
        return declared_backend_type(resolve_dataset(stores, dataset_name).directory)
    except (DatasetAccessError, PayloadInvalidError, OSError):
        return ""


@dataclass(frozen=True)
class PanelRead:
    experiment: dict[str, Any]
    # Where the panel was read: a local path it names resolves against it.
    directory: Path


def _declared_panel(stores: Stores, dataset: ResolvedDataset) -> PanelRead | None:
    materialized = stores.tenant_datasets.materialized_panel_dir(dataset.name)
    if (materialized / MATERIALIZED_PANEL_FILE).is_file():
        data = read_yaml_optional(materialized / MATERIALIZED_PANEL_FILE)
        if not isinstance(data, dict) or not data:
            raise ValueError(
                f"{materialized / MATERIALIZED_PANEL_FILE} must be a non-empty mapping."
            )
        return PanelRead(data, materialized)
    definition = read_dataset_definition(dataset.directory)
    if definition is None or definition.panel is None or definition.panel_dir is None:
        return None
    return PanelRead(definition.panel, definition.panel_dir)


def dataset_experiment(
    stores: Stores, dataset: ResolvedDataset, connector: Connector
) -> PanelRead | None:
    """A panel a source recipe materialized into the tenant tier, else the definition's own. One
    materialized on no machine yet is ``None``, not an error; a malformed one is."""
    if not connector.owns_panel:
        return None
    declared = _declared_panel(stores, dataset)
    if declared is None or connector.resolve_experiment is None:
        return declared
    return PanelRead(connector.resolve_experiment(stores, declared.experiment), declared.directory)


def extract_panel_rows(
    connector: Connector, dataset_name: str, experiment: dict[str, Any]
) -> list[dict[str, Any]]:
    assert connector.extract_experiment is not None, "a panel is read only where one is owned"
    try:
        return connector.extract_experiment(experiment)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        # Every guard above this seam catches `ValueError`; a raw `KeyError` walks through to a 500.
        raise ValueError(
            f"Connector {connector.name!r} could not read {dataset_name!r}'s panel: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def dataset_connector(dataset: ResolvedDataset) -> Connector | None:
    """``None`` where the definition names no backend; one naming a connector this PROCESS lacks
    raises, since its panel and identity are unreadable, never empty."""
    try:
        backend_type = declared_backend_type(dataset.directory)
    except PayloadInvalidError:
        return None
    connector = connectors.CONNECTORS.table.get(backend_type)
    if connector is None:
        raise ValueError(
            f"Dataset {dataset.name!r} declares backend_type {backend_type!r}, which this "
            f"process has no connector for. Restart the server."
        )
    return connector


def dataset_panel_rows(
    stores: Stores, dataset: ResolvedDataset, *, experiment: dict[str, Any] | None = None
) -> list[dict[str, Any]] | None:
    """*experiment* is the panel a cycle pinned: a campaign is read off what it measured."""
    connector = dataset_connector(dataset)
    if connector is None:
        return None
    if experiment is None:
        read = dataset_experiment(stores, dataset, connector)
        experiment = None if read is None else read.experiment
    return None if experiment is None else extract_panel_rows(connector, dataset.name, experiment)


def readable_task_context(stores: Stores, dataset: ResolvedDataset) -> TaskDecomposition:
    """An upload's own framing wins; an install's yields to the one this tenant decomposed or
    edited, which the read-only tier cannot hold. A blank record is absent."""
    committed = read_dataset_framing(dataset.directory).framing
    decomposed = stores.tenant_datasets.load_task_context(dataset.name)
    tiers = (committed, decomposed) if dataset.tier == "yours" else (decomposed, committed)
    return next((framing for framing in tiers if framing), TaskDecomposition())


def _install_names(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in sorted(root.rglob(DEFINITION_FILE)):
        name = path.parent.relative_to(root).as_posix()
        try:
            validate_dataset_name(name)
        except ValueError:
            continue
        out.append((name, path.parent))
    return out


def list_readable_datasets(stores: Stores) -> list[DatasetRef]:
    refs: list[DatasetRef] = []
    own: set[str] = set()
    for slug in stores.tenant_datasets.list_slugs():
        dataset_dir = stores.tenant_datasets.dataset_dir(slug)
        own.add(slug)
        refs.append(_dataset_ref(ResolvedDataset(slug, dataset_dir, "yours")))

    if stores.install_datasets_root.is_dir():
        for name, entry in _install_names(stores.install_datasets_root):
            if name not in own:
                refs.append(_dataset_ref(ResolvedDataset(name, entry, "install")))
    return refs


def _dataset_ref(dataset: ResolvedDataset) -> DatasetRef:
    title, summary = _read_heading(dataset.directory / README_FILE)
    return DatasetRef(dataset, title, summary)


_MARKUP = re.compile(r"\*\*|__|`|\[([^\]]*)\]\([^)]*\)")


def _read_heading(md: Path) -> tuple[str | None, str | None]:
    """The ``# `` title, and the first sentence of the paragraph under it as plain text."""
    if not md.is_file():
        return None, None
    title: str | None = None
    paragraph: list[str] = []
    for line in md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if title is None:
            if stripped.startswith("# "):
                title = stripped[2:].strip() or None
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            break
        paragraph.append(stripped)
    text = _MARKUP.sub(lambda m: m.group(1) or "", " ".join(paragraph))
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].strip()
    return title, sentence or None
