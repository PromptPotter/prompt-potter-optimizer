# application/views/ — the emit contract

**View builders live here, the views in `promptpotter/domain/phase_views.py`**; an absent measurement renders `—` ([conventions.md](../../../docs/developer/conventions.md) § Code shape, "Absent is not zero").
**The readout is every cycle's artifact, never a terminal's**: an entry point builds no `readout.py::ReadoutProjection` and hands at most a line sink to `build_run_observers`. <!-- probe: git grep -cE "ReadoutProjection" -- promptpotter/presentation/*.py == 0 -->
