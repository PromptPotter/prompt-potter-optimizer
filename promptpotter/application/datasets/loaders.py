from __future__ import annotations

import hashlib
import importlib
import logging
import random
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store.dataset_access import readable_dataset_rows
from promptpotter.shared import GSM8K_ANSWER_RE
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def hf_load_dataset() -> Any:
    # HuggingFace `load_dataset`, imported past this repo's OWN top-level `datasets/`.
    # That directory has no `__init__.py`, so it is a NAMESPACE package — and `python -m
    # promptpotter` puts the cwd on `sys.path[0]`, which is the documented way to run this. From
    # the repo root it therefore beats the installed library and every loader below dies on
    # `cannot import name 'load_dataset' from 'datasets' (unknown location)`. A warm store hides
    # it, because rows are already cached under `benchmark-rows/`; a FRESH CLONE hits it on its
    # first fetch. Renaming the data directory is the other fix and costs far more — it is a
    # first-class layout named in `datasets/CLAUDE.md` and in every dataset path.
    #
    # Without this the shadow also SWALLOWS the honest error: a missing library raises
    # `ImportError: cannot import name` rather than `ModuleNotFoundError`, so the friendly
    # install hint below never fires and the caller sees a namespace riddle instead. `datasets`
    # is opt-in (`.[benchmarks]`, deliberately not in `all`), so absent is the NORMAL case.
    shadow = sys.modules.get("datasets")
    if shadow is not None and getattr(shadow, "__file__", None) is None:
        del sys.modules["datasets"]
    saved = list(sys.path)
    try:
        sys.path[:] = [
            p
            for p in saved
            if not (d := Path(p or ".") / "datasets").is_dir() or (d / "__init__.py").is_file()
        ]
        return importlib.import_module("datasets").load_dataset
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "The 'datasets' library is required for this benchmark. "
            'Install the benchmarks extras: pip install -e ".[benchmarks]"'
        ) from None
    finally:
        sys.path[:] = saved


def samples_from_dicts(items: list[dict[str, Any]]) -> list[Sample]:
    return [Sample.from_dict(item, fallback_id=i) for i, item in enumerate(items)]


def sample_dataset(dataset: list[Sample], sample_size: int) -> list[Sample]:
    """Top-``sample_size`` slice; the bank is already shuffled at creation, so no second RNG. A size above
    the bank yields ALL of it — deliberate, and what ``sp_budget_origin`` above ``sp_budget_ttest`` needs."""
    if sample_size <= 0:
        # Both budgets land here (`sp_budget_ttest` per round, `origin_budget()` at C0), so
        # the message names neither — it named `sp_budget_ttest` and sent anyone hitting it
        # off the origin path to the wrong knob.
        raise ValueError(f"eval budget must be > 0, got {sample_size}")
    return dataset[:sample_size]


def load_gsm8k(split: str = "train") -> list[Sample]:
    """Load GSM8K from HuggingFace. Requires the ``datasets`` library: ``pip install -e ".[benchmarks]"``."""
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
    """Load AIME 2025 from HuggingFace. Requires ``datasets``: ``pip install -e ".[benchmarks]"``."""
    load_dataset = hf_load_dataset()

    ds = load_dataset("MathArena/aime_2025", split="train")
    samples: list[Sample] = [
        Sample(id=i, query=row["problem"], ground_truth=str(row["answer"]))
        for i, row in enumerate(ds)
    ]

    logger.info("Loaded AIME 2025: %d items", len(samples))
    return samples


# --- Dataset loader registry ---


def load_bbeh() -> list[Sample]:
    """Load BBEH mini (460 examples, 23 tasks). No native per-sample id — assigned sequentially after flattening; per-task metadata dropped."""
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


# ONE JustLogic loader for every depth cut. The cut is DERIVED FROM THE DATASET NAME
# (`justlogic-d234` → depths 2,3,4), so measuring a new combination costs a dataset dir and
# nothing else — no loader, no registry row, no depth constant, no listing entry.
#
# Each cut MUST remain its own dataset NAME. The archive keys a cell by
# (dataset_name, node_configs, sample_id) with the query text OUT of the key, so re-cutting
# in place points sample_id 0..N at new queries while the archive still serves the prior
# cut's rows under those keys.
_JUSTLOGIC_TRAIN_PER_DEPTH: int = 200
# Deterministic and fixed: the per-depth train/test split and the interleave shuffle must
# reproduce byte-for-byte across processes, or a cut silently becomes a different bank.
_JUSTLOGIC_SEED: int = 42
_JUSTLOGIC_CUT_RE = re.compile(r"^justlogic-d(\d+)$")


def justlogic_depths(dataset_name: str) -> tuple[int, ...] | None:
    """The depth cut *dataset_name* denotes, or ``None`` when it names none. One digit per depth, since
    JustLogic ships 1-7 — every cut names its depths and there is no irregular spelling to special-case."""
    m = _JUSTLOGIC_CUT_RE.match(dataset_name)
    if m is None:
        return None
    depths = tuple(sorted({int(d) for d in m.group(1)}))
    return depths or None


def _load_justlogic(depths: tuple[int, ...], split: str = "train") -> list[Sample]:
    if split not in ("train", "test"):
        raise ValueError(f"JustLogic split must be 'train' or 'test', got {split!r}")
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
        picked = indices[:cut] if split == "train" else indices[cut:]
        picked_rows.extend(rows[i] for i in picked)

    # Interleave the depths before numbering. `sample_dataset` takes a PREFIX of the bank
    # (`dataset[:sp_budget]`), so a depth-ordered bank hands every campaign a scoring subset
    # drawn entirely from the shallowest depth — the deeper half is loaded, indexed, and never
    # scored. That is what the depth-6/7 cut did for its whole life (every subset was depth 6),
    # and it is why a d2-3 campaign's origin came in near saturation while the full bank sat far
    # below it. Shuffling once, deterministically, makes any prefix a stratified draw — and the
    # same prefix every time, so origin and later rounds score the same samples.
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


DATASET_LOADERS: dict[str, Callable[..., list[Sample]]] = {
    "gsm8k": load_gsm8k,
    "aime_2025": load_aime_2025,
    "bbeh": load_bbeh,
}
"""Map dataset name → loader, for the benchmarks whose cut is fixed.

JustLogic is deliberately absent: its cut is a *family* derived from the name, resolved by
:func:`dataset_loader`. Read that, never this dict, to answer "can this name be loaded?" —
a bare membership test here reports False for every valid ``justlogic-dNNN``.
"""


def dataset_loader(dataset_name: str) -> Callable[[], list[Sample]] | None:
    """The zero-arg loader for *dataset_name*, or ``None``. The one resolver over both shapes: the fixed
    registry, and the JustLogic depth family read off the name — so a new cut needs a dir and no code."""
    fixed = DATASET_LOADERS.get(dataset_name)
    if fixed is not None:
        return fixed
    depths = justlogic_depths(dataset_name)
    if depths is None:
        return None
    return lambda: _load_justlogic(depths)


def loadable_dataset_names() -> list[str]:
    """Names a caller can offer today, DERIVED from the dirs on disk. The family is open, so this is a
    *listing* and never a validity test — ask :func:`dataset_loader` for that.

    The cut half asks the RESOLVER rather than re-matching the family's pattern here. Spelled as a
    list of regexes this rots one family at a time: a second family lands, nothing lists it, and
    the omission looks like the dir is missing rather than like this line is stale."""
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


def resolve_dataset_items(
    stores: Stores,
    dataset_name: str,
    *,
    status: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Resolved rows → ``DATASET_LOADERS`` fallback (fetch + persist). The single materialization seam
    for run-time init AND webapp ingest, so both reach the same samples on a machine missing the rows."""
    ds: dict[str, Any] | None = readable_dataset_rows(stores, dataset_name)
    loader = dataset_loader(dataset_name)
    if not (ds and ds.get("items")) and loader is not None:
        if status:
            status(f"Loading dataset '{dataset_name}' from registry ...")
        loader_items = loader()
        stores.tenant_datasets.save_benchmark_rows(dataset_name, loader_items)
        ds = {"items": [s.model_dump() for s in loader_items]}
    if not (ds and ds.get("items")):
        return []
    return [it.model_dump() if isinstance(it, Sample) else it for it in ds["items"]]


def build_dataset_run_data(
    run_id: str,
    name: str,
    content_hash: str,
    search_point: JobSearchPoint,
    scores: dict[str, Any],
    results: list[Any],
    *,
    dataset_name: str | None,
    source: str = "",
    pipeline_schema: PipelineSchema,
    human_intervened: bool = False,
) -> dict[str, Any]:
    """Measurement-batch dict for ``Stores.archive.save()``. ``pipeline_schema`` is REQUIRED: it picks the
    ``sp_hash`` algorithm and supplies ``node_configs``, so a batch without one gets a second identity."""
    from promptpotter.domain.measurement_provenance import grade_run

    rendered_prompt = search_point.render()
    sp_h = search_point.sp_hash(pipeline_schema)
    measurements = list(results)
    provenance = grade_run(source, measurements, pipeline_schema, human_intervened=human_intervened)
    data: dict[str, Any] = {
        "run_id": run_id,
        "name": name,
        "dataset_name": dataset_name,
        "content_hash": content_hash,
        "prompt_fields_id": sp_h,
        "rendered_prompt_hash": hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE],
        "item_count": scores["total"],
        "scores": scores,
        "source": source,
        "provenance": provenance.as_dict(),
        "created_at": utcnow_iso(),
        "measurements": measurements,
    }
    if search_point.pipeline_params:
        data["node_configs"] = pipeline_schema.node_configs(search_point.pipeline_params)
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    return data
