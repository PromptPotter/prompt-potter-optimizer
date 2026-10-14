from __future__ import annotations

import importlib
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.domain.sample import ArchiveEntry, Sample
from promptpotter.infrastructure.store.dataset_access import held_rows
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.shared.answer_text import GSM8K_ANSWER_RE
from promptpotter.shared.extras import Extra, require_extra
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def hf_load_dataset() -> Any:
    require_extra(Extra.BENCHMARKS)
    return importlib.import_module("datasets").load_dataset


def samples_from_dicts(items: list[dict[str, Any]]) -> list[Sample]:
    return [Sample.from_dict(item, fallback_id=i) for i, item in enumerate(items)]


def bank_samples(items: list[dict[str, Any]]) -> list[Sample]:
    # No row labelled = a verifier-graded dataset, which dropping unlabelled rows would empty.
    labelled = any(item.get("ground_truth") for item in items)
    return samples_from_dicts(
        [item for item in items if item.get("query") and (item.get("ground_truth") or not labelled)]
    )


def sample_dataset(dataset: list[Sample], sample_size: int) -> list[Sample]:
    """A prefix, never a draw: the bank is shuffled at creation."""
    if sample_size <= 0:
        raise ValueError(f"sp_budget_origin must be > 0, got {sample_size}")
    return dataset[:sample_size]


def load_gsm8k(split: str = "train") -> list[Sample]:
    load_dataset = hf_load_dataset()

    ds = load_dataset("openai/gsm8k", "main", split=split)
    samples: list[Sample] = []
    for i, row in enumerate(ds):
        m = GSM8K_ANSWER_RE.search(row["answer"])
        gt = f"#### {m.group(1)}" if m else row["answer"].strip()
        samples.append(Sample(id=i, query=row["question"], ground_truth=gt))

    logger.info("Loaded GSM8K %s: %d items", split, len(samples))
    return samples


def load_aime_2025() -> list[Sample]:
    load_dataset = hf_load_dataset()

    ds = load_dataset("MathArena/aime_2025", split="train")
    samples: list[Sample] = [
        Sample(id=i, query=row["problem"], ground_truth=str(row["answer"]))
        for i, row in enumerate(ds)
    ]

    logger.info("Loaded AIME 2025: %d items", len(samples))
    return samples


def load_bbeh() -> list[Sample]:
    load_dataset = hf_load_dataset()

    ds = load_dataset("BBEH/bbeh", split="train")
    samples: list[Sample] = []
    for row in ds:
        if not row.get("mini"):
            continue
        samples.append(
            Sample(
                id=len(samples),
                query=row["input"],
                ground_truth=row["target"],
            )
        )

    logger.info("Loaded BBEH mini: %d items", len(samples))
    return samples


# Changing any of these re-cuts a bank whose per-sample history is keyed by (dataset_name, id).
_JUSTLOGIC_TRAIN_PER_DEPTH: int = 200
_JUSTLOGIC_SEED: int = 42
_JUSTLOGIC_HELD_PER_DEPTH: int = 300
_JUSTLOGIC_HELD_SUFFIX = "-held"
_JUSTLOGIC_CUT_RE = re.compile(rf"^justlogic-d(\d+)(?:{_JUSTLOGIC_HELD_SUFFIX})?$")


def justlogic_depths(dataset_name: str) -> tuple[int, ...] | None:
    m = _JUSTLOGIC_CUT_RE.match(dataset_name)
    if m is None:
        return None
    depths = tuple(sorted({int(d) for d in m.group(1)}))
    return depths or None


def _load_justlogic(depths: tuple[int, ...], split: str = "train") -> list[Sample]:
    if split not in ("train", "test", "held"):
        raise ValueError(f"JustLogic split must be 'train', 'test' or 'held', got {split!r}")
    load_dataset = hf_load_dataset()
    from collections import defaultdict

    ds = load_dataset("michaelchenkj/JustLogic", split="train")
    by_depth: dict[int, list[Any]] = defaultdict(list)
    for row in ds:
        if row["depth"] in depths:
            by_depth[row["depth"]].append(row)

    picked_rows: list[Any] = []
    for depth in sorted(by_depth):
        rows = by_depth[depth]
        indices = list(range(len(rows)))
        random.Random(_JUSTLOGIC_SEED).shuffle(indices)
        cut = _JUSTLOGIC_TRAIN_PER_DEPTH
        stop = cut + _JUSTLOGIC_HELD_PER_DEPTH if split == "held" else None
        picked = indices[:cut] if split == "train" else indices[cut:stop]
        picked_rows.extend(rows[i] for i in picked)

    # `sample_dataset` takes a PREFIX, so a depth-ordered bank hands out only the shallowest depth.
    random.Random(_JUSTLOGIC_SEED).shuffle(picked_rows)

    samples: list[Sample] = [
        Sample(
            id=i,
            query=(
                f"Premises:\n{row['paragraph']}\n\n"
                f"Claim: {row['question']}\n\n"
                f"Is the claim TRUE, FALSE, or Uncertain given the premises?"
            ),
            ground_truth=str(row["label"]),
        )
        for i, row in enumerate(picked_rows)
    ]

    logger.info(
        "Loaded JustLogic %s: %d items (depths %s, %d/depth)",
        split,
        len(samples),
        list(depths),
        _JUSTLOGIC_TRAIN_PER_DEPTH,
    )
    return samples


def _load_justlogic_held(depths: tuple[int, ...]) -> list[Sample]:
    bank = _load_justlogic(depths)
    held = _load_justlogic(depths, "held")
    return bank + [
        row.model_copy(update={"id": len(bank) + row.id, "bench_only": True}) for row in held
    ]


@dataclass(frozen=True, slots=True)
class BankLoader:
    load: Callable[[], list[Sample]]
    # What a FETCH imports; a bank already materialized needs nothing installed.
    extra: Extra | None


def _hf(load: Callable[[], list[Sample]]) -> BankLoader:
    return BankLoader(load, Extra.BENCHMARKS)


DATASET_LOADERS: dict[str, BankLoader] = {
    "gsm8k": _hf(load_gsm8k),
    "aime_2025": _hf(load_aime_2025),
    "bbeh": _hf(load_bbeh),
}
"""Map dataset name → loader, for the benchmarks whose cut is fixed.

JustLogic is deliberately absent: its cut is a *family* derived from the name, resolved by
:func:`dataset_loader`. Read that, never this dict, to answer "can this name be loaded?" —
a bare membership test here reports False for every valid ``justlogic-dNNN``.
"""


def dataset_loader(dataset_name: str) -> BankLoader | None:
    fixed = DATASET_LOADERS.get(dataset_name)
    if fixed is not None:
        return fixed
    depths = justlogic_depths(dataset_name)
    if depths is None:
        return None
    if dataset_name.endswith(_JUSTLOGIC_HELD_SUFFIX):
        return _hf(lambda: _load_justlogic_held(depths))
    return _hf(lambda: _load_justlogic(depths))


def loadable_dataset_names() -> list[str]:
    root = benchmark_datasets_root()
    cuts = (
        sorted(
            p.name
            for p in root.iterdir()
            if p.is_dir() and p.name not in DATASET_LOADERS and dataset_loader(p.name) is not None
        )
        if root.is_dir()
        else []
    )
    return [*sorted(DATASET_LOADERS), *cuts]


def fetch_dataset_items(stores: Stores, dataset_name: str) -> list[dict[str, Any]]:
    """The ladder's last rung: fetch through the registered loader and bank the rows."""
    loader = dataset_loader(dataset_name)
    if loader is None:
        return []
    logger.info("Loading dataset '%s' from registry ...", dataset_name)
    loader_items = loader.load()
    stores.tenant_datasets.save_benchmark_rows(dataset_name, loader_items)
    return [s.model_dump() for s in loader_items]


def resolve_dataset_items(stores: Stores, dataset_name: str) -> list[dict[str, Any]]:
    """The one row ladder: what is held (`held_rows`), else a fetch."""
    held = held_rows(stores, dataset_name)
    return held if held is not None else fetch_dataset_items(stores, dataset_name)


def archive_entry(
    search_point: JobSearchPoint,
    *,
    dataset_name: str | None,
    pipeline_schema: PipelineSchema,
) -> ArchiveEntry:
    sp_h = search_point.sp_hash(pipeline_schema)
    params = search_point.pipeline_params
    node_configs = pipeline_schema.node_configs(params) if params else []
    return ArchiveEntry(
        config_key=config_key(node_configs or [("", {"prompt_fields_id": sp_h})]),
        prompt_fields_id=sp_h,
        rendered_prompt_hash=stable_hash(search_point.render(), length=ADDRESS_HEX),
        node_configs=node_configs,
        pipeline_params=params,
        dataset_name=dataset_name,
    )
