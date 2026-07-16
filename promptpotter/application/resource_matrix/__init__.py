"""Resource matrix — the operator-set (target-model × dataset) capability grid.

The foundation of the L4 panel: which model×dataset cells carry optimizable signal
(in-band) vs are floored/saturated. `measure_cells` scores origin-only per cell;
`matrix.py` holds the verdict models + band classification + on-disk artifact IO.
"""
