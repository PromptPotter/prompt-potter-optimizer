"""Resource matrix — the (target-model × dataset) capability grid.

The operator-set foundation of the L4 panel: each cell is one (target-model, dataset)
pair, classified from its measured ORIGIN accuracy as **floor** (too low — noise),
**in-band** (headroom to optimize into), or **saturated** (already acing — no room).
Absolute origin is a coarse triage; the exact number + Wilson CI ride each cell so the
operator judges reachable headroom and hand-picks the in-band cells for the panel.

Bands per ``docs/operations/dataset-selection-rationale.md`` (origin 15–40% in-band,
reachable ceiling 50–75%): saturated is ≥0.75, and the floor is whichever is HIGHER of an
absolute noise floor and the cell's own constant-answer floor — see ``constant_answer_floor``.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.domain.strict_model import StrictModel

FLOOR_MAX = 0.15  # an origin below this separates nothing from nothing, whatever the labels
SATURATED_MIN = 0.75  # at/above this, the model already aces the task — no headroom
# How much MORE concentrated on one label the pipeline may be than the ground truth before it
# is called collapsed. Measured against the truth's own skew, not an absolute share, so a task
# whose labels are genuinely lopsided is not punished for a model that correctly tracks them.
# Calibrated on the justlogic depth sweep: at depths 5-7 the model answers one label 88-96% of
# the time against a ~40%-skewed truth, yet still cleared its constant by a lucky sample or two
# and was banded in-band. Beating a stub by one sample does not make you not a stub.
COLLAPSE_EXCESS = 0.30

BAND_FLOOR = "floor"
BAND_IN = "in-band"
BAND_SATURATED = "saturated"
BAND_ERROR = "error"


def constant_answer_floor(results: list[Any] | None) -> float:
    """What a stub that ignores the input and emits the commonest ground-truth label would score.

    The real noise floor of a classification cell, and it is not a constant: on a 3-class set
    whose majority label holds 40% of the bank, a pipeline that has collapsed onto one word
    scores 0.40. An absolute cutoff cannot see that — it reads 0.40 as a healthy origin with
    headroom, which is exactly how a degenerate cell earns a place in the panel. Measured live
    on justlogic: a model answering "Uncertain" to 24 of 25 samples scored the floor exactly.

    0.0 when there are no labels to count, and ~0 on free-text answers (every ground truth its
    own bucket) — where there is no collapse to detect and ``FLOOR_MAX`` governs as before.
    """
    labels = [
        str(gt)
        for r in results or []
        if isinstance(r, dict) and (gt := r.get("ground_truth")) not in (None, "")
    ]
    if not labels:
        return 0.0
    return Counter(labels).most_common(1)[0][1] / len(labels)


def answer_concentration(results: list[Any] | None) -> tuple[str, float]:
    """The pipeline's most-emitted label and the share of answers it took. ``("", 0.0)`` if none.

    The peer of ``constant_answer_floor`` on the PREDICTION side: that one measures how
    concentrated the truth is, this one how concentrated the model is. A model far more
    concentrated than reality has stopped reading the input.
    """
    said = Counter(
        str(p)
        for r in results or []
        if isinstance(r, dict) and (p := r.get("predicted")) not in (None, "")
    )
    if not said:
        return "", 0.0
    label, n = said.most_common(1)[0]
    return label, n / sum(said.values())


def classify_band(
    origin_accuracy: float | None,
    constant_floor: float = 0.0,
    predicted_share: float = 0.0,
) -> str:
    """Triage a cell against the two ways it can fail to be a measurement.

    1. It does not BEAT a stub — accuracy at or under its constant-answer floor. Nothing can be
       optimized out of it, because the pipeline is not reading the input.
    2. It IS a stub that got lucky — its answers are far more concentrated on one label than the
       ground truth is, so the couple of points it clears the constant by are noise, not signal.
       Test (1) alone misses this: a pipeline answering one label 96% of the time can still land
       a sample or two above the floor and read as healthy.
    """
    if origin_accuracy is None:
        return BAND_ERROR
    if origin_accuracy <= max(FLOOR_MAX, constant_floor):
        return BAND_FLOOR
    if predicted_share - constant_floor >= COLLAPSE_EXCESS:
        return BAND_FLOOR
    if origin_accuracy >= SATURATED_MIN:
        return BAND_SATURATED
    return BAND_IN


class CellVerdict(StrictModel):
    """One (target-model, dataset) cell's measured capability verdict."""

    dataset: str
    target_model: str
    provider: str | None
    origin_accuracy: float | None
    n: int
    wilson_lo: float
    wilson_hi: float
    # The score a constant single-label answer earns on this cell's bank — the floor
    # ``origin_accuracy`` has to clear to mean anything. Rides the cell so the operator
    # reads the two numbers side by side and never hand-picks a stub into the panel.
    constant_floor: float = 0.0
    # Share of this cell's answers that went to its single most-emitted label. Against
    # ``constant_floor`` (the truth's own concentration) this is what separates a pipeline
    # that reasons from one that got lucky while emitting the same word.
    predicted_share: float = 0.0
    band: str
    active_in_panel: bool = False
    measured_at: str
    note: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.dataset, self.target_model, self.provider or "")


class ResourceMatrix(StrictModel):
    """The full capability grid — merged/upserted across ``matrix measure`` runs."""

    generated_at: str
    cells: list[CellVerdict]


def matrix_artifact_path(dataset_dir: Path) -> Path:
    """The committed matrix artifact under the pp-self dataset dir."""
    return dataset_dir / "resource_matrix.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_matrix(dataset_dir: Path) -> ResourceMatrix | None:
    path = matrix_artifact_path(dataset_dir)
    if not path.is_file():
        return None
    try:
        return ResourceMatrix.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def upsert_cells(dataset_dir: Path, measured: list[CellVerdict]) -> ResourceMatrix:
    """Merge freshly-measured cells into the on-disk matrix (upsert by cell key)."""
    existing = read_matrix(dataset_dir)
    by_key: dict[tuple[str, str, str], CellVerdict] = {}
    if existing is not None:
        for cell in existing.cells:
            by_key[cell.key] = cell
    for cell in measured:
        # Preserve an operator's active_in_panel choice across a re-measure.
        prior = by_key.get(cell.key)
        if prior is not None and prior.active_in_panel and not cell.active_in_panel:
            cell = cell.model_copy(update={"active_in_panel": True})
        by_key[cell.key] = cell
    cells = sorted(by_key.values(), key=lambda c: (c.dataset, c.target_model, c.provider or ""))
    return ResourceMatrix(generated_at=now_iso(), cells=cells)


def write_matrix(dataset_dir: Path, matrix: ResourceMatrix) -> Path:
    path = matrix_artifact_path(dataset_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(matrix.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
