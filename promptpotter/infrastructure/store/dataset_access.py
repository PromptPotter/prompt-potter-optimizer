"""Install content carries NO capability check: gating bytes that ship protects nothing."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import yaml
from pydantic import Field, TypeAdapter

from promptpotter import connectors
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import DEFAULT_PIPELINE
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.io import parse_yaml, read_bytes_optional
from promptpotter.infrastructure.store.read_model import file_sig, read_file
from promptpotter.shared.errors import (
    DatasetAccessError,
    FileKind,
    FileUnreadableError,
    PayloadInvalidError,
    SlugTakenError,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

DEFINITION_FILE = "dataset.yaml"
FAMILY_FILE = "family.yaml"
DEFAULTS_FILE = "defaults.yaml"
FRAMING_FILE = "task.md"
ORIGIN_FILE = "origin.yaml"
MATERIALIZED_PANEL_FILE = "panel.yaml"
# A connector panel a source recipe materialized: the experiment the connector resolves.
MATERIALIZED_PANEL: TypeAdapter[dict[str, Any]] = TypeAdapter(
    Annotated[dict[str, Any], Field(min_length=1)]
)

# Where a definition lives says what it is: an account's own upload, what the install ships, or
# research and test tooling no wheel carries (`config/paths.py::research_datasets_root`).
DatasetTier = Literal["yours", "install", "research"]


@dataclass(frozen=True)
class ResolvedDataset:
    """A dataset name resolved ONCE to the tier that holds it: every read of the dataset takes this,
    so none re-decides which directory answers, nor which layers lie under it."""

    name: str
    directory: Path
    tier: DatasetTier
    # The tier's root, below which a `family.yaml` is a layer; `None` on an account's own, which
    # no tenant can lay a family or a default under.
    tier_root: Path | None
    # The install root, whose `defaults.yaml` lies under every install and research definition.
    defaults_root: Path | None


def tenant_dataset(name: str, directory: Path) -> ResolvedDataset:
    return ResolvedDataset(name, directory, "yours", None, None)


def install_dataset(install_root: Path, name: str) -> ResolvedDataset:
    return ResolvedDataset(name, install_root / name, "install", install_root, install_root)


def is_dataset_dir(dataset_dir: Path) -> bool:
    return (dataset_dir / DEFINITION_FILE).is_file()


def _holds_definition(directory: Path) -> bool:
    return is_dataset_dir(directory) or (directory / FAMILY_FILE).is_file()


def definition_chain(dataset: ResolvedDataset) -> list[Path]:
    """The directories a definition is laid from, root first: the install root (its
    ``defaults.yaml``), every directory below the tier root holding ``family.yaml`` or a
    ``dataset.yaml``, then the leaf; a plain directory between them is a path segment, never a
    layer. An account's own is the leaf whole."""
    leaf, root = dataset.directory, dataset.tier_root
    if root is None or dataset.defaults_root is None:
        return [leaf]
    between = [d for d in reversed(leaf.parents) if d.is_relative_to(root) and d != root]
    return [dataset.defaults_root, *(d for d in between if _holds_definition(d)), leaf]


def _layer_file(directory: Path, leaf: Path) -> Path:
    if directory == leaf or is_dataset_dir(directory):
        return directory / DEFINITION_FILE
    return directory / (FAMILY_FILE if (directory / FAMILY_FILE).is_file() else DEFAULTS_FILE)


def definition_files(dataset: ResolvedDataset) -> list[Path]:
    """Every file a definition reads, so a cache keyed on them sees any layer's edit."""
    return [_layer_file(d, dataset.directory) for d in definition_chain(dataset)]


_SECTIONS = ("pipeline", "campaign", "source", "panel", "card")


def definition_layer(raw: bytes) -> dict[str, Any] | None:
    """One layer of a definition, ``None`` where the file is empty: a mapping of sections, each a
    mapping where set."""
    data = parse_yaml(raw)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("must be a mapping of sections")
    for key in _SECTIONS:
        if not isinstance(data.get(key) or {}, dict):
            raise ValueError(f"`{key}` must be a mapping")
    _card(data.get("card") or {})
    return data


def _named[T](path: Path, read: Callable[[bytes], T]) -> T | None:
    """*read* over the file at *path*, ``None`` where it is absent; a refusal names the file."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    try:
        return read(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _layers(dataset: ResolvedDataset) -> list[tuple[Path, dict[str, Any]]]:
    """Each layer file read whole: one that is no definition layer is refused by its path."""
    return [
        (path, layer)
        for path in definition_files(dataset)
        if (layer := read_file(path, definition_layer, FileKind.STORED_CONFIG)) is not None
    ]


def _section(layer: Mapping[str, Any], key: str) -> dict[str, Any]:
    return dict(layer.get(key) or {})


@dataclass(frozen=True)
class DatasetCard:
    """What a listing calls the dataset: product copy the definition declares."""

    title: str
    summary: str


def _card(section: Mapping[str, Any]) -> DatasetCard | None:
    if not section:
        return None
    title, summary = section.get("title"), section.get("summary")
    if set(section) != {"title", "summary"} or not all(
        isinstance(text, str) and text.strip() for text in (title, summary)
    ):
        raise ValueError("`card` declares a `title` and a `summary`, each plain text")
    assert isinstance(title, str) and isinstance(summary, str)
    return DatasetCard(title.strip(), " ".join(summary.split()))


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
    card: DatasetCard | None

    @property
    def backend_type(self) -> str:
        """The ONE reading of ``pipeline.backend_type``: lowercased, ``""`` where none is declared."""
        declared = self.pipeline.get("backend_type")
        return declared.lower() if isinstance(declared, str) else ""


def read_dataset_definition(dataset: ResolvedDataset) -> DatasetDefinition | None:
    """Per section, root first: ``pipeline`` replaces its top-level keys and merges its nodes,
    ``campaign`` layers depth-first, ``source`` and ``panel`` merge by key, and the deepest
    ``card`` stands whole. A node in ``defaults.yaml`` is a TEMPLATE: only a node the dataset or a
    family declares receives it. A layer that is no definition layer raises
    ``FileUnreadableError`` by its path."""
    if not is_dataset_dir(dataset.directory):
        return None
    layers = _layers(dataset)
    top: dict[str, Any] = {}
    nodes: dict[str, Any] = {}
    templates: dict[str, Any] = {}
    campaign: list[dict[str, Any]] = []
    source: dict[str, Any] = {}
    panel: dict[str, Any] = {}
    card: dict[str, Any] = {}
    panel_dir: Path | None = None
    for path, layer in layers:
        pipeline = _section(layer, "pipeline")
        declared = pipeline.get("nodes") or {}
        if path.name == DEFAULTS_FILE:
            templates = dict(declared)
        else:
            nodes = merge_node_blocks(nodes, declared)
        top |= {k: v for k, v in pipeline.items() if k != "nodes"}
        campaign.append(_section(layer, "campaign"))
        source |= _section(layer, "source")
        if "card" in layer:
            card = _section(layer, "card")
        if "panel" in layer:
            panel |= _section(layer, "panel")
            panel_dir = path.parent
    if nodes:
        top["nodes"] = {
            n: merge_node_blocks({n: templates[n]}, {n: block})[n] if n in templates else block
            for n, block in nodes.items()
        }
    return DatasetDefinition(
        dataset.directory,
        top,
        tuple(campaign),
        source,
        panel or None,
        panel_dir,
        _card(card),
    )


def read_dataset_pipeline(dataset: ResolvedDataset) -> dict[str, Any] | None:
    definition = read_dataset_definition(dataset)
    return None if definition is None else definition.pipeline


def declared_pipelines(declaration: Mapping[str, Any]) -> dict[str, list[str]]:
    """A ``default`` left undeclared is every declared node in order, as the schema parser reads it."""
    nodes = declaration.get("nodes") or {}
    declared = {
        str(name): [str(step) for step in steps or []]
        for name, steps in (declaration.get("pipelines") or {}).items()
    }
    return ({DEFAULT_PIPELINE: [str(node) for node in nodes]} if nodes else {}) | declared


def nearest_file(dataset: ResolvedDataset, filename: str) -> Path | None:
    """The dataset's own file, else its nearest family's: one framing or origin per kind."""
    return next(
        (
            path
            for directory in reversed(definition_chain(dataset))
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


def dataset_framing(raw: bytes) -> DatasetFraming:
    # Newlines as a text-mode read gives them.
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    m = _FRONT_MATTER.match(text)
    if m is None:
        return DatasetFraming(text.strip(), TaskDecomposition())
    try:
        fields = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"front matter is not valid YAML — {exc}") from exc
    if not isinstance(fields, dict):
        raise ValueError("front matter must be a mapping")
    body = text[m.end() :].strip()
    framing = TaskDecomposition.from_dict(fields)
    # Blank fields commit nothing: the shape `render_framing_file` writes, read back the same.
    if not framing:
        return DatasetFraming(body, TaskDecomposition())
    return DatasetFraming(body, TaskDecomposition.from_dict({**fields, "raw_description": body}))


def read_framing_file(path: Path | None) -> DatasetFraming:
    framing = None if path is None else _named(path, dataset_framing)
    return DatasetFraming("", TaskDecomposition()) if framing is None else framing


def read_dataset_framing(dataset: ResolvedDataset) -> DatasetFraming:
    return read_framing_file(nearest_file(dataset, FRAMING_FILE))


def render_framing_file(description: str, framing: TaskDecomposition) -> str:
    """The one writer of ``task.md``'s shape: front matter only where a field is set."""
    body = description.strip() + "\n"
    fields = {k: v for k, v in framing.to_dict().items() if k != "raw_description" and v}
    if not fields:
        return body
    head = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=100)
    return f"---\n{head}---\n{body}"


def origin_layer(raw: bytes) -> dict[str, dict[str, Any]] | None:
    data = parse_yaml(raw)
    if data is None:
        return None
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError("must map each node name to its starting prompt")
    return data


def read_dataset_origin(dataset: ResolvedDataset) -> dict[str, dict[str, Any]]:
    """Starting prompts keyed by node; a family's entry is the default for a node it names."""
    out: dict[str, dict[str, Any]] = {}
    for directory in definition_chain(dataset):
        if (layer := _named(directory / ORIGIN_FILE, origin_layer)) is not None:
            out |= layer
    return out


def _holders(stores: Stores, name: str) -> list[ResolvedDataset]:
    """Each tier holding a definition *name*, in reading order: tenant, then install, then
    research; raises ``ValueError`` on a name no tier could hold."""
    research = stores.research_datasets_root
    tiers = [
        tenant_dataset(name, stores.tenant_datasets.dataset_dir(name)),
        install_dataset(stores.install_datasets_root, name),
    ]
    if research is not None:
        tiers.append(
            ResolvedDataset(
                name, research / name, "research", research, stores.install_datasets_root
            )
        )
    return [dataset for dataset in tiers if is_dataset_dir(dataset.directory)]


def claim_own_definition(stores: Stores, slug: str, *, new: bool) -> None:
    """THE claim on the name of an account's definition: refused where the install or research
    tier holds it, since the account's would shadow theirs. A re-write of a name the account holds
    is admitted; *new* is a draft naming a dataset the account does not hold yet, refused where it
    does. The suggestion is a name no tier holds."""
    held = _holders(stores, slug)
    if held and (new or held[0].tier != "yours"):
        free = next(f"{slug}-{n}" for n in count(2) if not _holders(stores, f"{slug}-{n}"))
        raise SlugTakenError(slug, free)


def dataset_not_found(stores: Stores, name: str) -> DatasetAccessError:
    """The one 404 for a dataset *name* this caller may not start: an unoffered name reads
    exactly as an absent one, so the answer never says which."""
    return DatasetAccessError(name, research_tier=stores.research_datasets_root is not None)


def resolve_dataset(stores: Stores, name: str) -> ResolvedDataset:
    """The first tier holding *name*, in reading order: an account's own shadows the install's,
    which shadows the research tier's."""
    try:
        held = _holders(stores, name)
    except ValueError as exc:
        raise dataset_not_found(stores, name) from exc
    if held:
        return held[0]
    raise dataset_not_found(stores, name)


def definition_version(stores: Stores, name: str) -> tuple[object, ...]:
    """Stats only: which tier *name* resolves to, and every file its definition reads there."""
    try:
        dataset = resolve_dataset(stores, name)
    except DatasetAccessError:
        return (name, None)
    return (name, str(dataset.directory), *(file_sig(path) for path in definition_files(dataset)))


def runnable_definition(dataset: ResolvedDataset) -> DatasetDefinition:
    """One naming its backend; raises typed: a bare ``ValueError`` becomes a 500 the webapp retries forever."""
    path = dataset.directory / DEFINITION_FILE
    definition = read_dataset_definition(dataset)
    if definition is None or not definition.backend_type:
        raise PayloadInvalidError(
            f"backend_type missing or empty in {path}",
            code="pipeline_config_invalid",
            details={"dataset_dir": str(dataset.directory)},
        )
    return definition


def backend_type_of_dataset(stores: Stores, dataset_name: str) -> str:
    """Tolerant, a SURVEY's: a campaign outlives its dataset, and one bad file fails no listing."""
    try:
        return runnable_definition(resolve_dataset(stores, dataset_name)).backend_type
    except (DatasetAccessError, FileUnreadableError, PayloadInvalidError, OSError):
        return ""


@dataclass(frozen=True)
class PanelRead:
    experiment: dict[str, Any]
    # Where the panel was read: a local path it names resolves against it.
    directory: Path


def _declared_panel(stores: Stores, dataset: ResolvedDataset) -> PanelRead | None:
    materialized = stores.tenant_datasets.materialized_panel_dir(dataset.name)
    held = read_file(
        materialized / MATERIALIZED_PANEL_FILE,
        lambda raw: MATERIALIZED_PANEL.validate_python(parse_yaml(raw)),
    )
    if held is not None:
        return PanelRead(held, materialized)
    definition = read_dataset_definition(dataset)
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
        backend_type = runnable_definition(dataset).backend_type
    except (FileUnreadableError, PayloadInvalidError):
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
    committed = read_dataset_framing(dataset).framing
    decomposed = stores.tenant_datasets.load_task_context(dataset.name)
    tiers = (committed, decomposed) if dataset.tier == "yours" else (decomposed, committed)
    return next((framing for framing in tiers if framing), TaskDecomposition())


def _install_names(root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(root.rglob(DEFINITION_FILE)):
        name = path.parent.relative_to(root).as_posix()
        try:
            validate_dataset_name(name)
        except ValueError:
            continue
        out.append(name)
    return out


@dataclass(frozen=True)
class DatasetRef:
    dataset: ResolvedDataset
    # `None` on an account's own, which declares none, and on a malformed definition, which the
    # listing's reading reports.
    card: DatasetCard | None


def list_readable_datasets(stores: Stores) -> list[DatasetRef]:
    """An account's own, then the install's named unlike them: each name once, as it resolves.
    The research tier is never listed."""
    refs: list[DatasetRef] = []
    own: set[str] = set()
    for slug in stores.tenant_datasets.list_slugs():
        own.add(slug)
        refs.append(_dataset_ref(tenant_dataset(slug, stores.tenant_datasets.dataset_dir(slug))))

    if stores.install_datasets_root.is_dir():
        for name in _install_names(stores.install_datasets_root):
            if name not in own:
                refs.append(_dataset_ref(install_dataset(stores.install_datasets_root, name)))
    return refs


def _dataset_ref(dataset: ResolvedDataset) -> DatasetRef:
    try:
        definition = read_dataset_definition(dataset)
    except FileUnreadableError:
        return DatasetRef(dataset, None)
    return DatasetRef(dataset, None if definition is None else definition.card)
