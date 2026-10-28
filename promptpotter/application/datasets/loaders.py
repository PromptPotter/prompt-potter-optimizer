from __future__ import annotations

import json
import logging
import random
import re
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.datasets.sealqa_panel import write_longseal_panel
from promptpotter.application.datasets.tabular_ingest import materialize_samples, read_tabular
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample
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
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra, require_extra
from promptpotter.shared.hashing import ADDRESS_HEX, module_source_digest, stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

RECIPE_CODE = module_source_digest(sys.modules[__name__])

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
            raise ValueError(f"`source:` names no recipe: {sorted(section)}")
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
            raise ValueError(f"`source:` fetches {repo} at no pinned `rev`")
        return cls(recipe, kind, repo, rev, params)

    @property
    def identity(self) -> str:
        """A new ``rev``, cut parameter or edit to this module's code cuts a bank of its own."""
        return stable_hash(
            [RECIPE_CODE, self.recipe, self.kind.value, self.repo, self.rev, dict(self.params)],
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


def _hub_split(src: Source) -> list[Row]:
    return list(from_hub(src, *_configs(src), split=src["split"]))


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
    return row["query"], row["ground_truth"]


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
            row.model_copy(update={"id": len(samples) + row.id, "bench_only": True})
            for row in self.bench_tail.load()
        ]


def _bank(
    src: Source, rows: Callable[[], list[Row]], draw: Callable[[Row], Drawn] = _query_and_truth
) -> BankLoader:
    return BankLoader(
        src.kind, rows, draw, cap=src.get("cap"), shuffled=bool(src.get("shuffled", True))
    )


def _hotpotqa(row: Row) -> Drawn:
    context = row["context"]
    paragraphs = "\n\n".join(
        f"{title}: {''.join(sentences)}"
        for title, sentences in zip(context["title"], context["sentences"], strict=True)
    )
    return f"Question: {row['question']}\n\nContext:\n\n{paragraphs}", row["answer"]


def _plain_number(value: float) -> str:
    # `GSM8K_ANSWER_RE` reads no exponent, which is what `repr` makes of a large or tiny float.
    return str(int(value)) if value.is_integer() else format(Decimal(repr(value)), "f")


def _class_label_rows(
    src: Source, column: str, query: Callable[[Row, Any], str]
) -> Callable[[], list[Row]]:
    def rows() -> list[Row]:
        ds = from_hub(src, split=src["split"])
        label = ds.features[column]
        return [
            {"query": query(row, label), "ground_truth": label.int2str(row[column])} for row in ds
        ]

    return rows


def _naturalplan_rows(src: Source) -> list[Row]:
    examples = json.loads(_github_raw(src, src["path"]))
    # Tiered 100 per head count, so the take keeps the fewest-people tiers whole.
    return sorted(examples.values(), key=lambda row: row["num_people"])[: int(src["take"])]


def _naturalplan(row: Row) -> Drawn:
    golden = {key: row[key] for key in ("constraints", "dist_matrix", "golden_plan")}
    return row["prompt_0shot"], json.dumps(golden, separators=(",", ":"))


_BLOCKSWORLD_FACT_RE = re.compile(r"the (\w+) block is (?:on top of the (\w+) block|on the table)")


def _blocksworld_problem(query: str) -> str:
    # The last [STATEMENT] is the instance to solve; the one before it is the prompt's worked example.
    init, goal = query.rsplit("[STATEMENT]", 1)[1].split("My goal is to have that")
    return json.dumps(
        {
            "below": {top: under or "table" for top, under in _BLOCKSWORLD_FACT_RE.findall(init)},
            "goal": [[top, under or "table"] for top, under in _BLOCKSWORLD_FACT_RE.findall(goal)],
        },
        separators=(",", ":"),
    )


def _arc(row: Row) -> Drawn:
    choices = row["choices"]
    options = "\n".join(
        f"{label}) {text}" for label, text in zip(choices["label"], choices["text"], strict=True)
    )
    return f"{row['question']}\n\nOptions:\n{options}", row["answerKey"]


def _instruction_induction_task_rows(task: str, examples: dict[str, Row]) -> list[Row]:
    rng = random.Random(_BANK_SEED)
    rows: list[Row] = []
    for example in examples.values():
        if task == "cause_and_effect":
            pair = [example["cause"], example["effect"]]
            rng.shuffle(pair)
            text, gold = f"Sentence 1: {pair[0]} Sentence 2: {pair[1]}", example["cause"]
        elif task == "taxonomy_animal":
            # The source lists the animals in an arbitrary order and grades a set; input order is exact.
            animals = set(example["category_items"])
            kept = [word for word in example["words"] if word in animals]
            if len(kept) != len(animals):
                raise ValueError(
                    f"taxonomy_animal: {animals - set(kept)} not among the input words"
                )
            text, gold = example["input"], ", ".join(kept)
        else:
            text, gold = example["input"], example["output"]
        rows.append({"query": f"Task: {task}\nInput: {text}", "ground_truth": gold})
    return rows


def _instruction_induction_rows(src: Source) -> list[Row]:
    rows: list[Row] = []
    for task in src["tasks"]:
        doc = json.loads(_github_raw(src, f"data/raw/execute/{task}.json"))
        rows.extend(_instruction_induction_task_rows(task, doc["examples"]))
    return rows


def _justlogic_rows(src: Source, *, held: bool) -> list[Row]:
    depths = set(src["depths"])
    by_depth: dict[int, list[Row]] = defaultdict(list)
    for row in from_hub(src, split=src["split"]):
        if row["depth"] in depths:
            by_depth[row["depth"]].append(row)
    train = int(src["train_per_depth"])
    # The tail's size is the held cut's alone: the training cut declares none.
    end = train + int(src["held_per_depth"]) if held else train
    picked: list[Row] = []
    for depth in sorted(by_depth):
        rows = by_depth[depth]
        indices = list(range(len(rows)))
        random.Random(_BANK_SEED).shuffle(indices)
        drawn = indices[train:end] if held else indices[:train]
        picked.extend(rows[i] for i in drawn)
    return picked


def _justlogic(row: Row) -> Drawn:
    return (
        f"Premises:\n{row['paragraph']}\n\n"
        f"Claim: {row['question']}\n\n"
        f"Is the claim TRUE, FALSE, or Uncertain given the premises?"
    ), str(row["label"])


def _justlogic_bank(src: Source) -> BankLoader:
    tail = BankLoader(src.kind, lambda: _justlogic_rows(src, held=True), _justlogic)
    return BankLoader(
        src.kind,
        lambda: _justlogic_rows(src, held=False),
        _justlogic,
        bench_tail=tail if src.get("held_tail") else None,
    )


_SCREEN_TASTE_QUERY = """Titles this person likes:
{shown}

Candidates:
{slate}"""


def _screen_taste_rows(src: Source) -> list[Row]:
    """Each row is one DRAW: ``shown`` liked titles as context, and a slate of one more held out plus
    distractors. A title on both lists is an unscoreable row, so the recipe refuses one."""
    liked, other = list(src["titles"]), list(src["distractors"])
    n_rows, n_shown, n_slate = int(src["rows"]), int(src["shown"]), int(src["slate"])
    if both := sorted(set(liked) & set(other)):
        raise ValueError(f"in titles AND distractors, so no row can score: {both}")
    rng = random.Random(int(src["seed"]))
    items: list[Row] = []
    seen: set[str] = set()
    while len(items) < n_rows:
        draw = rng.sample(liked, n_shown + 1)
        shown, held = draw[:n_shown], draw[n_shown]
        # The ANSWER is what makes a row distinct; a second draw holding it out is wasted spend.
        if held in seen:
            continue
        seen.add(held)
        slate = [held, *rng.sample(other, n_slate - 1)]
        # Shuffled, or the answer is always first and position alone scores 1.0.
        rng.shuffle(slate)
        query = _SCREEN_TASTE_QUERY.format(shown="\n".join(shown), slate="\n".join(slate))
        items.append({"query": query, "ground_truth": held})
    return items


def _hub_rows(draw: Callable[[Row], Drawn]) -> Callable[[Source], BankLoader]:
    return lambda src: _bank(src, lambda: _hub_split(src), draw)


def _cell(value: object) -> str:
    return _plain_number(value) if isinstance(value, float) else str(value)


def _columns(src: Source) -> BankLoader:
    """Two columns of a hub split: `query` and `truth` name them, `truth_prefix` leads the answer,
    and `where` names a boolean column a row must hold. A `query` holding `{column}` is a template
    over the row, and `data_files` picks files of a repo whose files share no schema. A class-label
    truth reads as its name."""
    query, truth, prefix, where = (
        src["query"],
        src["truth"],
        src.get("truth_prefix", ""),
        src.get("where"),
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
        return str(query).format_map(row) if "{" in str(query) else str(row[query])

    return _bank(src, rows, lambda row: (query_of(row), f"{prefix}{_cell(row[truth])}"))


def _table(src: Source) -> BankLoader:
    """An uploaded table's two columns, in file order."""
    path = Path(src.repo)

    def rows() -> list[Row]:
        samples = materialize_samples(
            read_tabular(path.read_bytes(), path.name),
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
    "hotpotqa": _hub_rows(_hotpotqa),
    "trec": lambda src: _bank(
        src,
        _class_label_rows(
            src,
            "coarse_label",
            lambda row, _: (
                f"Question: {row['text']}\n\nAnswer type: ABBR, ENTY, DESC, HUM, LOC or NUM?"
            ),
        ),
    ),
    "naturalplan_meeting": lambda src: _bank(src, lambda: _naturalplan_rows(src), _naturalplan),
    "planbench_blocksworld": lambda src: _bank(
        src,
        lambda: [row for row in _hub_split(src) if row["domain"] == src["domain"]],
        lambda row: (row["query"], _blocksworld_problem(row["query"])),
    ),
    "arc": _hub_rows(_arc),
    "instruction_induction": lambda src: _bank(src, lambda: _instruction_induction_rows(src)),
    "justlogic": _justlogic_bank,
    "screen_taste": lambda src: _bank(src, lambda: _screen_taste_rows(src)),
}
"""A recipe is the code half of a cut; the ``source:`` section beside the definition is the data half
— repo, commit and every cut parameter. ``Source.identity`` keys a held cut on both halves."""


def _sealqa_longseal(src: Source, out_dir: Path) -> int:
    rows = list(from_hub(src, *_configs(src), split=src["split"]))
    provenance = f"{src.repo} config={src['config']} split={src['split']} revision={src.rev}"
    return write_longseal_panel(
        rows,
        out_dir,
        docs=int(src["docs"]),
        two_step=bool(src.get("two_step")),
        provenance=provenance,
    )


def _dbllmbench_questions(src: Source, out_dir: Path) -> int:
    # Upstream's file verbatim: JSON is YAML, and a byte-for-byte copy is what "their panel" means.
    text = _github_raw(src, src["path"])
    write_bytes(out_dir / MATERIALIZED_PANEL_FILE, text.encode("utf-8"))
    return len(json.loads(text)["questions"])


PANEL_RECIPES: dict[str, Callable[[Source, Path], int]] = {
    "sealqa_longseal": _sealqa_longseal,
    "dbllmbench_questions": _dbllmbench_questions,
}
"""A recipe that BUILDS a connector-owned panel and the task directories it names, into the tenant tier."""


def definition_source(dataset_dir: Path) -> Source | None:
    definition = read_dataset_definition(dataset_dir)
    return None if definition is None else Source.of(definition.source)


def _loader(stores: Stores, src: Source) -> BankLoader:
    if src.kind is SourceKind.UPLOAD:
        # The definition names the kept file; where this tenant keeps it is the store's.
        src = replace(src, repo=str(stores.tenant_datasets.upload_path(src.repo)))
    return BANK_RECIPES[src.recipe](src)


def dataset_loader(stores: Stores, dataset_dir: Path) -> BankLoader | None:
    src = definition_source(dataset_dir)
    if src is None or src.recipe not in BANK_RECIPES:
        return None
    return _loader(stores, src)


def cut_bank(stores: Stores, section: Mapping[str, Any]) -> list[Sample]:
    """What a ``source:`` section cuts, read before any definition names it."""
    src = Source.of(section)
    if src is None or src.recipe not in BANK_RECIPES:
        raise ValueError(f"`source:` names no bank recipe: {dict(section)}")
    return _loader(stores, src).load()


def source_extra(dataset_dir: Path) -> Extra | None:
    """What a fetch from this definition's source imports: a panel build reads the hub too."""
    src = definition_source(dataset_dir)
    return None if src is None else src.kind.extra


def has_source(dataset_dir: Path) -> bool:
    src = definition_source(dataset_dir)
    return src is not None and (src.recipe in BANK_RECIPES or src.recipe in PANEL_RECIPES)


def no_dataset_hint(stores: Stores) -> str:
    """Every dataset this account reads: its own definitions first, then the installed templates."""
    lines = [
        f"  python -m promptpotter new {ref.dataset.name}" for ref in list_readable_datasets(stores)
    ]
    body = "\n".join(lines) if lines else "  (no datasets readable here)"
    return "Available datasets:\n\n" + body


def loadable_dataset_names(stores: Stores) -> list[str]:
    return sorted(
        ref.dataset.name
        for ref in list_readable_datasets(stores)
        if has_source(ref.dataset.directory)
    )


def build_panel(stores: Stores, dataset: ResolvedDataset) -> bool:
    """A panel recipe materializes into the tenant tier, never beside the definition — on demand,
    per account, so no account depends on another having built it."""
    src = definition_source(dataset.directory)
    if src is None or src.recipe not in PANEL_RECIPES:
        return False
    out = stores.tenant_datasets.materialized_panel_dir(dataset.name)
    n = PANEL_RECIPES[src.recipe](src, out)
    logger.info("Built panel '%s' from its source: %d tasks -> %s", dataset.name, n, out)
    return True


def _cut(loader: BankLoader) -> Bank:
    return Bank.of(kept_rows(loader.load()))


def fetch_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    """The ladder's last rung: it records the bank its recipe cut, and moves no label."""
    if build_panel(stores, dataset):
        return held_bank(stores, dataset) or Bank.of(())
    src = definition_source(dataset.directory)
    if src is None or src.recipe not in BANK_RECIPES:
        return Bank.of(())
    bank = _cut(_loader(stores, src))
    logger.info("Loaded dataset '%s' from its source: %d items", dataset.name, len(bank.samples))
    stores.tenant_datasets.record_cut(src.identity, bank)
    return bank


def held_bank(stores: Stores, dataset: ResolvedDataset) -> Bank | None:
    """No fetch, no write; a connector owning a panel answers from it ALONE, since a same-named bank is another instrument."""
    connector = dataset_connector(dataset)
    if connector is not None and connector.owns_panel:
        rows = dataset_panel_rows(stores, dataset)
        return None if rows is None else Bank.of(samples_from_dicts(rows))
    src = definition_source(dataset.directory)
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
