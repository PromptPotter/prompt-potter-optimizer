"""Resource matrix — the (target-model × dataset) capability grid.

The operator-set foundation of the L4 panel: each cell is one (target-model, dataset)
pair, classified from its measured ORIGIN accuracy as **floor** (too low — noise),
**in-band** (headroom to optimize into), or **saturated** (already acing — no room).
Absolute origin is a coarse triage; the exact number + Wilson CI ride each cell so the
operator judges reachable headroom and hand-picks the in-band cells for the panel.

Bands per ``docs/operations/dataset-selection-rationale.md`` (origin 15–40% in-band,
reachable ceiling 50–75%): here the coarse cutoffs are floor <0.15 and saturated ≥0.75,
with the whole middle "in-band" — the operator narrows within it on the real number.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

FLOOR_MAX = 0.15  # below this, origin is in the noise floor — no separable signal
SATURATED_MIN = 0.75  # at/above this, the model already aces the task — no headroom

BAND_FLOOR = "floor"
BAND_IN = "in-band"
BAND_SATURATED = "saturated"
BAND_ERROR = "error"


def classify_band(origin_accuracy: float | None) -> str:
    """Coarse triage of a cell from its origin accuracy alone."""
    if origin_accuracy is None:
        return BAND_ERROR
    if origin_accuracy < FLOOR_MAX:
        return BAND_FLOOR
    if origin_accuracy >= SATURATED_MIN:
        return BAND_SATURATED
    return BAND_IN


class CellVerdict(BaseModel):
    """One (target-model, dataset) cell's measured capability verdict."""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    target_model: str
    provider: str | None
    origin_accuracy: float | None
    n: int
    wilson_lo: float
    wilson_hi: float
    band: str
    active_in_panel: bool = False
    measured_at: str
    note: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.dataset, self.target_model, self.provider or "")


class ResourceMatrix(BaseModel):
    """The full capability grid — merged/upserted across ``matrix measure`` runs."""

    model_config = ConfigDict(extra="forbid")

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
