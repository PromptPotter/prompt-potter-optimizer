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
from enum import Enum
from pathlib import Path
from types import CodeType, FunctionType, ModuleType
from typing import Any

from promptpotter.application.datasets.harbor_panel import write_task_panel
from promptpotter.application.datasets.tabular_ingest import materialize_samples, read_kept_table
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.store.dataset_access import (
    MATERIALIZED_PANEL_FILE,
    ResolvedDataset,
    dataset_connector,
    dataset_panel_rows,
    list_readable_datasets,
    read_dataset_definition,
)
from promptpotter.infrastructure.store.io import write_bytes
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra, require_extra
from promptpotter.shared.hashing import ADDRESS_HEX, module_source_digest, stable_hash

_PACKAGE = __name__.rpartition(".")[0]

Row = dict[str, Any]
Drawn = tuple[str, str]


_HUB_EXTRA = Extra.BENCHMARKS


class SourceKind(Enum):
    HUB = "hub"
    GITHUB = "github"
    # A file this tenant uploaded, named by its own content: pinned without a `rev`.
    UPLOAD = "upload"
    # The rows' inputs are the `source:` section itself.
    INLINE = "inline"

    @property
    def extra(self) -> Extra | None:
        return _HUB_EXTRA if self is SourceKind.HUB else None

    @property
    def local(self) -> bool:
        return self in (SourceKind.UPLOAD, SourceKind.INLINE)


def _source_lacks(field: str, said: str) -> PayloadInvalidError:
    return PayloadInvalidError(
        f"the definition's `source:` names no `{field}`: {said}.",
        code="source_incomplete",
        details={"lacks": field},
    )


@dataclass(frozen=True, slots=True)
class Source:
    """A definition's ``source:`` section: where the rows come from, at which commit, cut how."""

    recipe: str
    kind: SourceKind
    repo: str
    rev: str
    params: Mapping[str, Any]

    @classmethod
    def of(cls, section: Mapping[str, Any]) -> Source | None:
        if not section:
            return None
        params = dict(section)
        recipe = str(params.pop("recipe", ""))
        if not recipe:
            raise _source_lacks("recipe", f"it names {sorted(section)}")
        kind = next(
            (
                k
                for k in (SourceKind.HUB, SourceKind.GITHUB, SourceKind.UPLOAD)
                if k.value in params
            ),
            None,
        )
        if kind is None:
            return cls(recipe, SourceKind.INLINE, "", "", params)
        repo = str(params.pop(kind.value))
        if kind is SourceKind.UPLOAD:
            return cls(recipe, kind, repo, "", params)
        # A moving ref re-cuts the bank on the next machine that fetches it, under the name it has.
        rev = str(params.pop("rev", ""))
        if not rev:
            raise _source_lacks("rev", f"it fetches {repo} at no pinned commit")
        return cls(recipe, kind, repo, rev, params)

    @property
    def identity(self) -> str:
        """A new ``rev``, cut parameter or edit to its recipe's code cuts a bank of its own."""
        return stable_hash(
            [
                recipe_code(self.recipe),
                self.recipe,
                self.kind.value,
                self.repo,
                self.rev,
                dict(self.params),
            ],
            length=ADDRESS_HEX,
        )

    def __getitem__(self, key: str) -> Any:
        try:
            return self.params[key]
        except KeyError:
            raise ValueError(f"recipe {self.recipe!r} needs `source.{key}`") from None

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


def _load_dataset(*args: Any, **kwargs: Any) -> Any:
    require_extra(_HUB_EXTRA)
    from datasets import load_dataset

    return load_dataset(*args, **kwargs)


def from_hub(src: Source, *config: str, **kwargs: Any) -> Any:
    return _load_dataset(src.repo, *config, revision=src.rev, **kwargs)


def _github_raw(src: Source, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{src.repo}/{src.rev}/{path}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return str(response.read().decode("utf-8"))


def _configs(src: Source) -> tuple[str, ...]:
    return (str(src["config"]),) if "config" in src.params else ()


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
    draw: Callable[[Row], Drawn] = _query_and_truth
    cap: int | None = None
    # `sample_dataset` takes a PREFIX, so a source-ordered bank is shuffled once, here.
    shuffled: bool = True
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
    return BankLoader(
        src.kind, rows, draw, cap=src.get("cap"), shuffled=bool(src.get("shuffled", True))
    )


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
        ds = from_hub(src, *_configs(src), split=src["split"], **files)
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
        BankLoader(src.kind, lambda: _stratified(rows(), src, held=True), draw)
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

    return BankLoader(src.kind, rows, shuffled=False)


BANK_RECIPES: dict[str, Callable[[Source], BankLoader]] = {
    "columns": _columns,
    # The rows themselves, inline: a hand-authored bank small enough to ship as its definition.
    "rows": lambda src: _bank(src, lambda: [dict(row) for row in src["items"]]),
    "table": _table,
}
"""A recipe is the code half of a cut; the ``source:`` section beside the definition is the data half
— repo, commit and every cut parameter. ``Source.identity`` keys a held cut on both halves."""


def _harbor_tasks(src: Source, out_dir: Path) -> int:
    """A hub split as Harbor task directories, each laid from the `template` layers and formatted
    over its row's `question`, `answer` and `documents` columns."""
    rows = list(from_hub(src, *_configs(src), split=src["split"]))
    provenance = f"{src.repo} config={src['config']} split={src['split']} revision={src.rev}"
    return write_task_panel(
        rows,
        out_dir,
        layers=list(src["template"]),
        columns={key: str(src[key]) for key in ("question", "answer", "documents")},
        task_prefix=str(src["task_prefix"]),
        provenance=provenance,
    )


def _repo_file(src: Source, out_dir: Path) -> int:
    """One file of a GitHub repo, verbatim, as the panel: JSON is YAML, and a byte-for-byte copy is
    what "their panel" means. `tasks` names the key listing its tasks."""
    text = _github_raw(src, src["path"])
    write_bytes(out_dir / MATERIALIZED_PANEL_FILE, text.encode("utf-8"))
    return len(json.loads(text)[src["tasks"]])


PANEL_RECIPES: dict[str, Callable[[Source, Path], int]] = {
    "harbor_tasks": _harbor_tasks,
    "repo_file": _repo_file,
}
"""A recipe that BUILDS a connector-owned panel and the task directories it names, into the tenant tier."""


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
    """What a recipe's code IS: this module and every module of this package it runs."""
    run = (BANK_RECIPES | PANEL_RECIPES)[recipe]
    imported = sorted(_reached(run, set()), key=lambda m: m.__name__)
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


def has_source(dataset: ResolvedDataset) -> bool:
    src = definition_source(dataset)
    return src is not None and (src.recipe in BANK_RECIPES or src.recipe in PANEL_RECIPES)


def no_dataset_hint(stores: Stores) -> str:
    """Every dataset this account reads: its own definitions first, then the installed templates."""
    lines = [f"  {ref.dataset.name}" for ref in list_readable_datasets(stores)]
    body = "\n".join(lines) if lines else "  (no datasets readable here)"
    return "Available datasets, by name:\n\n" + body


def build_panel(stores: Stores, dataset: ResolvedDataset) -> bool:
    """A panel recipe materializes into the tenant tier, never beside the definition — on demand,
    per account, so no account depends on another having built it."""
    src = definition_source(dataset)
    if src is None or src.recipe not in PANEL_RECIPES:
        return False
    out = stores.tenant_datasets.materialized_panel_dir(dataset.name)
    PANEL_RECIPES[src.recipe](src, out)
    return True


def _cut(loader: BankLoader) -> Bank:
    return Bank.of(kept_rows(loader.load()))


def fetch_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    """The ladder's last rung: it records the bank its recipe cut, and moves no label."""
    if build_panel(stores, dataset):
        return held_bank(stores, dataset) or Bank.of(())
    src = definition_source(dataset)
    if src is None or src.recipe not in BANK_RECIPES:
        return Bank.of(())
    bank = _cut(_loader(stores, src))
    stores.tenant_datasets.record_cut(src.identity, bank)
    return bank


def held_bank(stores: Stores, dataset: ResolvedDataset) -> Bank | None:
    """No fetch, no write; a connector owning a panel answers from it ALONE, since a same-named bank is another instrument."""
    connector = dataset_connector(dataset)
    if connector is not None and connector.owns_panel:
        rows = dataset_panel_rows(stores, dataset)
        return None if rows is None else Bank.of(samples_from_dicts(rows))
    src = definition_source(dataset)
    if src is None or src.recipe not in BANK_RECIPES:
        return stores.tenant_datasets.labelled(dataset.name)
    if src.kind.local:
        return _cut(_loader(stores, src))
    return stores.tenant_datasets.cut(src.identity)


def resolve_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    held = held_bank(stores, dataset)
    return held if held is not None else fetch_bank(stores, dataset)


def archive_entry(
    search_point: JobSearchPoint,
    *,
    dataset_hash: str,
    pipeline_schema: PipelineSchema,
) -> ArchiveEntry:
    sp_h = search_point.sp_hash(pipeline_schema)
    params = search_point.pipeline_params
    node_configs = pipeline_schema.node_configs(params) if params else []
    return ArchiveEntry(
        config_key=config_key(node_configs or [("", {"prompt_fields_id": sp_h})]),
        prompt_fields_id=sp_h,
        node_configs=node_configs,
        pipeline_params=params,
        dataset_hash=dataset_hash,
    )
