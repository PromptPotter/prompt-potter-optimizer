from __future__ import annotations

import functools
import json
import os
import random
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import CodeType, FunctionType, ModuleType
from typing import Any

from promptpotter.application.datasets.tabular_ingest import materialize_samples, read_kept_table
from promptpotter.connectors.protocol import PanelRecipe
from promptpotter.domain.dataset_source import Source, SourceKind
from promptpotter.domain.dataset_start import DatasetUnstartableError, StartBlock
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.dataset_hub import from_hub
from promptpotter.infrastructure.store.dataset_access import (
    MATERIALIZED_PANEL_FILE,
    ResolvedDataset,
    dataset_connector,
    dataset_experiment,
    read_dataset_definition,
)
from promptpotter.infrastructure.store.io import write_bytes
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra
from promptpotter.shared.hashing import ADDRESS_HEX, module_source_digest, stable_hash

_PACKAGE = __name__.rpartition(".")[0]

Row = dict[str, Any]
Drawn = tuple[str, str]


def samples_from_dicts(items: list[dict[str, Any]]) -> list[Sample]:
    return [Sample.from_dict(item, fallback_id=i) for i, item in enumerate(items)]


def kept_rows(samples: list[Sample]) -> list[Sample]:
    # No row labelled = a verifier-graded dataset, which dropping unlabelled rows would empty.
    labelled = any(s.ground_truth for s in samples)
    return [s for s in samples if s.query and (s.ground_truth or not labelled)]


def sample_dataset(dataset: list[Sample], sample_size: int) -> list[Sample]:
    """A prefix, never a draw: the bank is shuffled at creation."""
    if sample_size <= 0:
        raise ValueError(f"sp_budget_origin must be > 0, got {sample_size}")
    return dataset[:sample_size]


# The one seed every bank's shuffle and draw takes; changing it re-cuts each bank, so its hash.
_BANK_SEED: int = 42


def _query_and_truth(row: Row) -> Drawn:
    truth = row["ground_truth"]
    # An object gold is graded field by field (`fields_match`): its one spelling keys the sample.
    if isinstance(truth, dict):
        truth = json.dumps(truth, ensure_ascii=False, separators=(",", ":"))
    return row["query"], truth


@dataclass(frozen=True, slots=True)
class BankLoader:
    source: SourceKind
    rows: Callable[[], list[Row]]
    # `sample_dataset` takes a PREFIX, so a source-ordered bank is shuffled once, here, when its
    # definition says so (`source.shuffled`).
    shuffled: bool
    draw: Callable[[Row], Drawn] = _query_and_truth
    cap: int | None = None
    bench_tail: BankLoader | None = None

    @property
    def extra(self) -> Extra | None:
        """What a FETCH imports; a bank already materialized needs nothing installed."""
        return self.source.extra

    def load(self) -> list[Sample]:
        rows = self.rows()
        if self.shuffled:
            random.Random(_BANK_SEED).shuffle(rows)
        # A sample is its content, so a source repeating a row would bank one sample twice.
        drawn: dict[str, str] = {}
        for query, truth in map(self.draw, rows):
            if drawn.setdefault(query, truth) != truth:
                raise PayloadInvalidError(f"the source answers one query two ways: {query[:80]!r}")
        samples = [
            Sample(id=i, query=query, ground_truth=truth)
            for i, (query, truth) in enumerate(list(drawn.items())[: self.cap])
        ]
        if self.bench_tail is None:
            return samples
        return samples + [
            row.model_copy(update={"id": len(samples) + row.id, "held_out": True})
            for row in self.bench_tail.load()
        ]


def _bank(
    src: Source, rows: Callable[[], list[Row]], draw: Callable[[Row], Drawn] = _query_and_truth
) -> BankLoader:
    return BankLoader(src.kind, rows, bool(src["shuffled"]), draw, cap=src.get("cap"))


def _plain_number(value: float) -> str:
    # `GSM8K_ANSWER_RE` reads no exponent, which is what `repr` makes of a large or tiny float.
    return str(int(value)) if value.is_integer() else format(Decimal(repr(value)), "f")


def _cell(value: object) -> str:
    return _plain_number(value) if isinstance(value, float) else str(value)


def _stratified(rows: list[Row], src: Source, *, held: bool) -> list[Row]:
    """`per_stratum` rows of each `strata` value of column `stratum`, each stratum drawn apart; the
    held cut is the next `held_per_stratum` of the same draw, so the two never share a row."""
    column, strata, take = src["stratum"], set(src["strata"]), int(src["per_stratum"])
    by_stratum: dict[Any, list[Row]] = defaultdict(list)
    for row in rows:
        if row[column] in strata:
            by_stratum[row[column]].append(row)
    # The tail's size is the held cut's alone: the training cut declares none.
    end = take + int(src["held_per_stratum"]) if held else take
    picked: list[Row] = []
    for key in sorted(by_stratum):
        group = by_stratum[key]
        indices = list(range(len(group)))
        random.Random(_BANK_SEED).shuffle(indices)
        picked.extend(group[i] for i in (indices[take:end] if held else indices[:take]))
    return picked


def _columns(src: Source) -> BankLoader:
    """Two columns of a hub split: `query` and `truth` name them, `truth_prefix` leads the answer,
    and `where` names a boolean column a row must hold. A `query` holding `{column}` is a template
    over the row, and `data_files` picks files of a repo whose files share no schema. A class-label
    truth reads as its name. `paragraphs: {column, title, text}` replaces a struct column of parallel
    lists with its `"{title}: {text}"` paragraphs before the template is formatted. `stratum` cuts
    the split per stratum (`_stratified`), and `held_per_stratum` appends that held cut as the bench
    tail."""
    query, truth, prefix, where, paragraphs = (
        src["query"],
        src["truth"],
        src.get("truth_prefix", ""),
        src.get("where"),
        src.get("paragraphs"),
    )

    def rows() -> list[Row]:
        files = {"data_files": src["data_files"]} if "data_files" in src.params else {}
        ds = from_hub(src, split=src["split"], **files)
        named = getattr(ds.features[truth], "int2str", None)
        return [
            {**row, truth: row[truth] if named is None else named(row[truth])}
            for row in ds
            if where is None or row[where]
        ]

    def query_of(row: Row) -> str:
        if paragraphs is not None:
            lists = row[paragraphs["column"]]
            joined = "\n\n".join(
                f"{title}: {''.join(text)}"
                for title, text in zip(
                    lists[paragraphs["title"]], lists[paragraphs["text"]], strict=True
                )
            )
            row = {**row, paragraphs["column"]: joined}
        return str(query).format_map(row) if "{" in str(query) else str(row[query])

    def draw(row: Row) -> Drawn:
        return query_of(row), f"{prefix}{_cell(row[truth])}"

    if "stratum" not in src.params:
        return _bank(src, rows, draw)
    tail = (
        BankLoader(
            src.kind, lambda: _stratified(rows(), src, held=True), bool(src["shuffled"]), draw
        )
        if "held_per_stratum" in src.params
        else None
    )
    cut = _bank(src, lambda: _stratified(rows(), src, held=False), draw)
    return replace(cut, bench_tail=tail)


def _table(src: Source) -> BankLoader:
    """An uploaded table's two columns, in file order."""
    path = Path(src.repo)

    def rows() -> list[Row]:
        samples = materialize_samples(
            read_kept_table(path),
            query_col=src["query"],
            ground_truth_col=src["truth"],
        )
        return [{"query": s.query, "ground_truth": s.ground_truth} for s in samples]

    return BankLoader(src.kind, rows, False)


BANK_RECIPES: dict[str, Callable[[Source], BankLoader]] = {
    "columns": _columns,
    # The rows themselves, inline: a hand-authored bank small enough to ship as its definition.
    "rows": lambda src: _bank(src, lambda: [dict(row) for row in src["items"]]),
    "table": _table,
}
"""A recipe is the code half of a cut; the ``source:`` section beside the definition is the data half
— repo, commit and every cut parameter. ``cut_identity`` keys a held cut on both halves."""


def cut_identity(src: Source) -> str:
    """A new ``rev``, cut parameter or edit to its recipe's code cuts a bank of its own."""
    return stable_hash(
        [recipe_code(src.recipe), src.recipe, src.kind.value, src.repo, src.rev, dict(src.params)],
        length=ADDRESS_HEX,
    )


def _github_raw(src: Source, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{src.repo}/{src.rev}/{path}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return str(response.read().decode("utf-8"))


def _repo_file(src: Source, out_dir: Path) -> int:
    """One file of a GitHub repo, verbatim, as the panel: JSON is YAML, and a byte-for-byte copy is
    what "their panel" means. `tasks` names the key listing its tasks."""
    text = _github_raw(src, src["path"])
    write_bytes(out_dir / MATERIALIZED_PANEL_FILE, text.encode("utf-8"))
    return len(json.loads(text)[src["tasks"]])


PANEL_RECIPES: dict[str, PanelRecipe] = {"repo_file": _repo_file}
"""A recipe that BUILDS a connector-owned panel and the task directories it names, into the tenant
tier; a connector declares its own beside these (`Connector.panel_recipes`)."""


def _reached(value: object, seen: set[object]) -> set[ModuleType]:
    """The modules of this package, beside this one, whose code *value* runs: through every
    function, lambda, closure and class of this module it names."""
    if not isinstance(value, FunctionType | type) or value in seen:
        return set()
    seen.add(value)
    if value.__module__ != __name__:
        named = value.__module__.startswith(f"{_PACKAGE}.")
        return {sys.modules[value.__module__]} if named else set()
    if isinstance(value, type):
        return set().union(*(_reached(v, seen) for v in vars(value).values()))
    cells = [cell.cell_contents for cell in value.__closure__ or ()]
    codes = [value.__code__]
    names: set[object] = set()
    while codes:
        code = codes.pop()
        names.update(value.__globals__[n] for n in code.co_names if n in value.__globals__)
        codes.extend(c for c in code.co_consts if isinstance(c, CodeType))
    return set().union(*(_reached(v, seen) for v in (*names, *cells)))


@functools.cache
def recipe_modules(recipe: str) -> tuple[ModuleType, ...]:
    """What a bank recipe's code IS: this module and every module of this package it runs."""
    imported = sorted(_reached(BANK_RECIPES[recipe], set()), key=lambda m: m.__name__)
    return (sys.modules[__name__], *imported)


def recipe_code(recipe: str) -> str:
    """Kept by its modules' mtimes: a served read re-digests only a recipe whose code moved on disk."""
    modules = recipe_modules(recipe)
    return _recipe_digest(modules, tuple(os.stat(str(m.__file__)).st_mtime_ns for m in modules))


@functools.lru_cache(maxsize=64)
def _recipe_digest(modules: tuple[ModuleType, ...], mtimes: tuple[int, ...]) -> str:
    del mtimes
    return module_source_digest(*modules)


def definition_source(dataset: ResolvedDataset) -> Source | None:
    definition = read_dataset_definition(dataset)
    return None if definition is None else Source.of(definition.source)


def _loader(stores: Stores, src: Source) -> BankLoader:
    if src.kind is SourceKind.UPLOAD:
        # The definition names the kept file; where this tenant keeps it is the store's.
        src = replace(src, repo=str(stores.tenant_datasets.upload_path(src.repo)))
    return BANK_RECIPES[src.recipe](src)


def cut_bank(stores: Stores, section: Mapping[str, Any]) -> list[Sample]:
    """What a ``source:`` section cuts, read before any definition names it."""
    src = Source.of(section)
    if src is None or src.recipe not in BANK_RECIPES:
        raise ValueError(f"`source:` names no bank recipe: {dict(section)}")
    return _loader(stores, src).load()


def source_extra(dataset: ResolvedDataset) -> Extra | None:
    """What a fetch from this definition's source imports: a panel build reads the hub too."""
    src = definition_source(dataset)
    return None if src is None else src.kind.extra


def builds_bank(dataset: ResolvedDataset) -> bool:
    """Whether rows no read holds are built: by a fetch's recipe, or at run init off the panel its
    connector owns."""
    if (connector := dataset_connector(dataset)) is not None and connector.owns_panel:
        return True
    src = definition_source(dataset)
    return src is not None and (src.recipe in BANK_RECIPES or src.recipe in PANEL_RECIPES)


def build_panel(stores: Stores, dataset: ResolvedDataset) -> bool:
    """A panel recipe materializes into the tenant tier, never beside the definition — on demand,
    per account, so no account depends on another having built it."""
    src, connector = definition_source(dataset), dataset_connector(dataset)
    recipes = {**PANEL_RECIPES, **(connector.panel_recipes if connector else {})}
    if src is None or (recipe := recipes.get(src.recipe)) is None:
        return False
    recipe(src, stores.tenant_datasets.materialized_panel_dir(dataset.name))
    return True


def _cut(loader: BankLoader) -> Bank:
    return Bank.of(kept_rows(loader.load()))


def fetch_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    """The ladder's last rung: it records the bank its recipe cut, and moves no label. A panel's
    bank is built at run init alone (`wiring.py::session_bank`)."""
    src = definition_source(dataset)
    if src is None or src.recipe not in BANK_RECIPES:
        return Bank.of(())
    try:
        bank = _cut(_loader(stores, src))
    except OSError as fault:
        # The source's door: a hub or a repo this box cannot reach refuses by where it asked.
        asked = f"{src.kind.value} {src.repo}@{src.rev} ({type(fault).__name__})"
        raise DatasetUnstartableError(
            dataset.name, StartBlock.SOURCE_UNREACHABLE, source=asked
        ) from fault
    stores.tenant_datasets.record_cut(cut_identity(src), bank)
    return bank


def held_bank(stores: Stores, dataset: ResolvedDataset) -> Bank | None:
    """No fetch, no write; a connector owning a panel answers with the bank its newest mint
    labelled ALONE (its build is run init's), since a same-named cut is another instrument; a panel
    it cannot read raises as it would at that build."""
    connector = dataset_connector(dataset)
    if connector is not None and connector.owns_panel:
        dataset_experiment(stores, dataset, connector)
        return stores.tenant_datasets.labelled(dataset.name)
    src = definition_source(dataset)
    if src is None or src.recipe not in BANK_RECIPES:
        return stores.tenant_datasets.labelled(dataset.name)
    if src.kind.local:
        return _cut(_loader(stores, src))
    return stores.tenant_datasets.cut(cut_identity(src))


def resolve_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    held = held_bank(stores, dataset)
    return held if held is not None else fetch_bank(stores, dataset)


def archive_entry(
    search_point: JobSearchPoint,
    *,
    dataset_hash: str,
    pipeline_schema: PipelineSchema,
    cell_identity: Mapping[str, Mapping[str, Any]],
) -> ArchiveEntry:
    """*search_point* filed under *cell_identity* (`PipelineParams.keyed_by`): its id is what it sends."""
    sp_h = search_point.sp_hash(pipeline_schema)
    params = search_point.pipeline_params
    node_configs = pipeline_schema.node_configs(params.keyed_by(cell_identity)) if params else []
    return ArchiveEntry(
        config_key=config_key(node_configs or [("", {"prompt_fields_id": sp_h})]),
        prompt_fields_id=sp_h,
        node_configs=node_configs,
        pipeline_params=params,
        dataset_hash=dataset_hash,
    )
