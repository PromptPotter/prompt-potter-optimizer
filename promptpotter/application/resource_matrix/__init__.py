"""Resource matrix — the operator-set (target-model × dataset) capability grid.

The foundation of the L4 panel: which model×dataset cells carry optimizable signal
(in-band) vs are floored/saturated. `measure_cells` scores origin-only per cell;
`matrix.py` holds the verdict models + band classification + on-disk artifact IO.
"""

from promptpotter.application.resource_matrix.matrix import (
    CellVerdict,
    ResourceMatrix,
    classify_band,
    matrix_artifact_path,
    read_matrix,
    upsert_cells,
    write_matrix,
)
from promptpotter.application.resource_matrix.measure import measure_cells

__all__ = [
    "CellVerdict",
    "ResourceMatrix",
    "classify_band",
    "matrix_artifact_path",
    "measure_cells",
    "read_matrix",
    "upsert_cells",
    "write_matrix",
]
