# application/views/ — the emit contract

**View builders live here, the views in `promptpotter/domain/phase_views.py`**; an absent measurement renders `—` ([conventions.md](../../../docs/developer/conventions.md) § Code shape, "Absent is not zero").
**The readout is every cycle's artifact, never a terminal's**: only `build_run_observers` builds `readout.py::ReadoutProjection`; an entry point hands at most a line sink.
