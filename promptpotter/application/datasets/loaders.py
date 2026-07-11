"""Ground-truth loaders + dataset registry.

Loaders return ``list[Sample]``; ``DATASET_LOADERS`` is the name → loader map;
``build_dataset_run_data`` builds the measurement-batch dict."""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.domain.sample import Sample
from promptpotter.shared import GSM8K_ANSWER_RE
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)


def samples_from_dicts(items: list[dict[str, Any]]) -> list[Sample]:
    """Convert dicts → Samples; assigns positional ``id`` when missing, ignores extras."""
    return [Sample.from_dict(item, fallback_id=i) for i, item in enumerate(items)]


def sample_dataset(dataset: list[Sample], sample_size: int) -> list[Sample]:
    """Top-``sample_size`` slice; datasets already shuffled at creation (deterministic prefix, no second RNG)."""
    if sample_size <= 0:
        raise ValueError(f"sp_budget_ttest must be > 0, got {sample_size}")
    return dataset[:sample_size]


def load_gsm8k(
    split: str = "train",
    sample_size: int = 0,
    seed: int = 42,
) -> list[Sample]:
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

    if sample_size > 0 and len(samples) > sample_size:
        samples = random.Random(seed).sample(samples, sample_size)

    logger.info("Loaded GSM8K %s: %d items", split, len(samples))
    return samples


def load_aime_2025(
    sample_size: int = 0,
    seed: int = 42,
) -> list[Sample]:
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

    if sample_size > 0 and len(samples) > sample_size:
        samples = random.Random(seed).sample(samples, sample_size)

    logger.info("Loaded AIME 2025: %d items", len(samples))
    return samples


# --- Dataset loader registry ---


def load_bbeh(sample_size: int = 0, seed: int = 42) -> list[Sample]:
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

    if sample_size > 0 and len(samples) > sample_size:
        samples = random.Random(seed).sample(samples, sample_size)

    logger.info("Loaded BBEH mini: %d items", len(samples))
    return samples


_JUSTLOGIC_DEPTHS: tuple[int, ...] = (6, 7)
_JUSTLOGIC_TRAIN_PER_DEPTH: int = 200


def load_justlogic(
    split: str = "train",
    sample_size: int = 0,
    seed: int = 42,
) -> list[Sample]:
    """Load JustLogic (`michaelchenkj/JustLogic`) at reasoning depths 6-7.

    Authors (Chen 2025, arXiv 2501.14851) ship one HF split (`train`,
    4,900 rows, 700 per depth × 7 depths). The canonical test set is
    deliberately withheld to prevent benchmark leakage — regenerate via
    `create_split.py` (seed=0, 70/15/15 stratified per-depth) for a
    leakage-free held-out, or request from authors. HF `train` IS the
    public training fold per authors' intent.

    Operator-blessed cut (this loader): filter to **depths 6 and 7
    only** (1,400 rows total — the recon-measured in-band stratum at
    ~44% origin on `gpt-oss-20b @ low`). Per included depth,
    deterministic seed=42 shuffle → first 200 = ``train`` (400
    total), rest = ``test`` (1,000 total). NOT canonical (authors'
    test set is withheld); resulting numbers are non-leaderboard-
    comparable. Documented in ``datasets/justlogic/dataset.md``.

    Each row's query is formatted as
    ``Premises:\\n{paragraph}\\n\\nClaim: {question}\\n\\nIs the claim
    TRUE, FALSE, or Uncertain given the premises?``. Ground truth is
    one of ``TRUE`` / ``FALSE`` / ``Uncertain`` — pair with the
    existing ``exact_match`` scorer + the BBEH-style ``**X**`` answer
    convention in ``prompts/default.json``.
    """
    if split not in ("train", "test"):
        raise ValueError(f"load_justlogic split must be 'train' or 'test', got {split!r}")
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
        if row["depth"] in _JUSTLOGIC_DEPTHS:
            by_depth[row["depth"]].append(row)

    samples: list[Sample] = []
    for depth in sorted(by_depth):
        rows = by_depth[depth]
        indices = list(range(len(rows)))
        random.Random(seed).shuffle(indices)
        cut = _JUSTLOGIC_TRAIN_PER_DEPTH
        picked = indices[:cut] if split == "train" else indices[cut:]
        for src_idx in picked:
            row = rows[src_idx]
            query = (
                f"Premises:\n{row['paragraph']}\n\n"
                f"Claim: {row['question']}\n\n"
                f"Is the claim TRUE, FALSE, or Uncertain given the premises?"
            )
            samples.append(
                Sample(
                    id=len(samples),
                    query=query,
                    ground_truth=str(row["label"]),
                )
            )

    if sample_size > 0 and len(samples) > sample_size:
        samples = random.Random(seed).sample(samples, sample_size)

    logger.info(
        "Loaded JustLogic %s: %d items (operator-defined cut, depths %s, %d/depth)",
        split,
        len(samples),
        list(_JUSTLOGIC_DEPTHS),
        _JUSTLOGIC_TRAIN_PER_DEPTH,
    )
    return samples


DATASET_LOADERS: dict[str, Callable[..., list[Sample]]] = {
    "gsm8k": load_gsm8k,
    "aime_2025": load_aime_2025,
    "bbeh": load_bbeh,
    "justlogic": load_justlogic,
}
"""Map dataset name → loader. ``load_potter_traces`` (prior-run replay) ships
extra non-Sample fields and is direct-import only, not registry-routed."""


def resolve_dataset_items(
    stores: Stores,
    dataset_name: str,
    *,
    status: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Tenant cache → backend cache → ``DATASET_LOADERS`` fallback (fetch + persist).

    Returns normalized item dicts (``Sample.model_dump()`` shape); ``[]`` only when
    no cached source exists AND no loader is registered. The single materialization
    seam shared by the run-time bootstrap (``_load_dataset_into_session``) and the
    webapp ingest-from-dataset path (``draft_from_dataset``), so both reach the same
    samples — a benchmark whose ``cache.json`` is gitignored/absent on the server is
    fetched + re-persisted here, exactly as a fresh run would.
    """
    ds: dict[str, Any] | None = stores.tenant_datasets.load_dataset(dataset_name)
    if not (ds and ds.get("items")):
        ds = stores.backends.load_dataset(dataset_name)
    if not (ds and ds.get("items")) and dataset_name in DATASET_LOADERS:
        if status:
            status(f"Loading dataset '{dataset_name}' from registry ...")
        loader_items = DATASET_LOADERS[dataset_name]()
        stores.backends.save_dataset(dataset_name, loader_items)
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
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, Any]:
    """Measurement-batch dict ready for ``Stores.archive.save()``.
    ``dataset_name`` is required (keyword-only); ``None`` permitted only for forensic writes."""
    from promptpotter.domain.measurement_provenance import grade_run

    rendered_prompt = search_point.render()
    sp_h = search_point.sp_hash(pipeline_schema)
    measurements = list(results)
    provenance = grade_run(source, measurements, pipeline_schema)
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
    if pipeline_schema and search_point.pipeline_params:
        data["node_configs"] = pipeline_schema.node_configs(search_point.pipeline_params)
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    return data
