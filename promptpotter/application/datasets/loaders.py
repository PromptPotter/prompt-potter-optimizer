"""Ground-truth loaders + dataset registry.

Loaders return ``list[Sample]``; ``DATASET_LOADERS`` is the name → loader map;
``build_dataset_run_data`` builds the measurement-batch dict."""

from __future__ import annotations

import hashlib
import logging
import random
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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


def samples_from_dicts(items: list[dict[str, Any]]) -> list[Sample]:
    """Convert dicts → Samples; assigns positional ``id`` when missing, ignores extras."""
    return [Sample.from_dict(item, fallback_id=i) for i, item in enumerate(items)]


def sample_dataset(dataset: list[Sample], sample_size: int) -> list[Sample]:
    """Top-``sample_size`` slice; datasets already shuffled at creation (deterministic prefix, no second RNG).

    A *sample_size* above ``len(dataset)`` yields the whole bank — deliberate, and what the
    origin path relies on: ``sp_budget_origin`` defaults above ``sp_budget_ttest``, so on a
    small bank "score the origin on everything" is the right answer, not an error.
    """
    if sample_size <= 0:
        # Both budgets land here (`sp_budget_ttest` per round, `origin_budget()` at C0), so
        # the message names neither — it named `sp_budget_ttest` and sent anyone hitting it
        # off the origin path to the wrong knob.
        raise ValueError(f"eval budget must be > 0, got {sample_size}")
    return dataset[:sample_size]


def load_gsm8k(split: str = "train") -> list[Sample]:
    """Load GSM8K from HuggingFace.

    Requires the ``datasets`` library: ``pip install -e ".[benchmarks]"``.
    """
    try:
        from datasets import load_dataset
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "The 'datasets' library is required for GSM8K. "
            'Install the benchmarks extras: pip install -e ".[benchmarks]"'
        ) from None

    ds = load_dataset("openai/gsm8k", "main", split=split)
    samples: list[Sample] = []
    for i, row in enumerate(ds):
        m = GSM8K_ANSWER_RE.search(row["answer"])
        gt = f"#### {m.group(1)}" if m else row["answer"].strip()
        samples.append(Sample(id=i, query=row["question"], ground_truth=gt))

    logger.info("Loaded GSM8K %s: %d items", split, len(samples))
    return samples


def load_aime_2025() -> list[Sample]:
    """Load AIME 2025 from HuggingFace.

    Requires the ``datasets`` library: ``pip install -e ".[benchmarks]"``.
    """
    try:
        from datasets import load_dataset
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "The 'datasets' library is required for AIME 2025. "
            'Install the benchmarks extras: pip install -e ".[benchmarks]"'
        ) from None

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
    try:
        from datasets import load_dataset
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "The 'datasets' library is required for BBEH. "
            'Install the benchmarks extras: pip install -e ".[benchmarks]"'
        ) from None

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
# nothing else — no loader, no registry row, no depth constant. `justlogic` is the one
# irregular name: it predates the `-dNNN` convention and means depths 6-7.
#
# Each cut MUST remain its own dataset NAME. The measurement archive keys a cell by
# (dataset_name, node_configs, sample_id) with the query text OUT of the key, so re-cutting
# in place would leave sample_id 0..N pointing at new queries while the archive still served
# the old cut's rows under those keys. Deriving the cut from the name is what keeps one
# loader from becoming one name.
_JUSTLOGIC_TRAIN_PER_DEPTH: int = 200
# Deterministic and fixed: the per-depth train/test split and the interleave shuffle must
# reproduce byte-for-byte across processes, or a cut silently becomes a different bank.
_JUSTLOGIC_SEED: int = 42
_JUSTLOGIC_LEGACY_DEPTHS: tuple[int, ...] = (6, 7)
_JUSTLOGIC_CUT_RE = re.compile(r"^justlogic-d(\d+)$")


def justlogic_depths(dataset_name: str) -> tuple[int, ...] | None:
    """The depth cut *dataset_name* denotes, or ``None`` when it names no JustLogic cut.

    ``justlogic-d234`` → ``(2, 3, 4)`` — one digit per depth, since JustLogic ships depths
    1-7. The bare ``justlogic`` → its historical ``(6, 7)``.
    """
    if dataset_name == "justlogic":
        return _JUSTLOGIC_LEGACY_DEPTHS
    m = _JUSTLOGIC_CUT_RE.match(dataset_name)
    if m is None:
        return None
    depths = tuple(sorted({int(d) for d in m.group(1)}))
    return depths or None


def _load_justlogic(depths: tuple[int, ...], split: str = "train") -> list[Sample]:
    """Filter to *depths*, deterministic per-depth train/test split, format queries."""
    if split not in ("train", "test"):
        raise ValueError(f"JustLogic split must be 'train' or 'test', got {split!r}")
    try:
        from datasets import load_dataset
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "The 'datasets' library is required for JustLogic. "
            'Install the benchmarks extras: pip install -e ".[benchmarks]"'
        ) from None
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
    """The zero-arg loader for *dataset_name*, or ``None`` when nothing can load it.

    The one resolver over both shapes: the fixed-cut registry above, and the JustLogic
    depth family, whose cut comes off the name (``justlogic-d234`` → depths 2,3,4). So a
    new depth combination needs a dataset dir and no code at all.
    """
    fixed = DATASET_LOADERS.get(dataset_name)
    if fixed is not None:
        return fixed
    depths = justlogic_depths(dataset_name)
    if depths is None:
        return None
    return lambda: _load_justlogic(depths)


def loadable_dataset_names() -> list[str]:
    """Names a caller can offer today — the fixed cuts plus the JustLogic cuts that ship a
    dataset dir. The family is open, so this is a *listing*, never a validity test; ask
    :func:`dataset_loader` for that."""
    return [*sorted(DATASET_LOADERS), "justlogic", "justlogic-d23", "justlogic-d234"]


def resolve_dataset_items(
    stores: Stores,
    dataset_name: str,
    *,
    status: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Resolved rows → ``DATASET_LOADERS`` fallback (fetch + persist).

    Returns normalized item dicts (``Sample.model_dump()`` shape); ``[]`` only when
    no cached source exists AND no loader is registered. The single materialization
    seam shared by the run-time bootstrap (``_load_dataset_into_session``) and the
    webapp ingest-from-dataset path (``draft_from_dataset``), so both reach the same
    samples — a benchmark whose rows are absent on this machine is fetched +
    persisted here, exactly as a fresh clone would.

    The fetch lands in the TENANT tree, never beside the definition it materializes:
    under a wheel that definition sits in ``site-packages``
    (``store/dataset_access.py::readable_dataset_rows``).
    """
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
    """Measurement-batch dict ready for ``Stores.archive.save()``.
    ``dataset_name`` is required (keyword-only); ``None`` permitted only for forensic writes.
    ``human_intervened`` marks a babysat cycle's run non-clean (grade ``C``).
    ``pipeline_schema`` is required: it picks the ``sp_hash`` algorithm behind
    ``prompt_fields_id`` and supplies ``node_configs``, so a batch written without one
    is filed under a second identity for the same searchpoint."""
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
