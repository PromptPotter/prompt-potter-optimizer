# application/initialization/ — run init

**Run init is an ordered chain** — `init_services` → `populate_session_scoring` → `init_cycle` → `init_optimization_loop` → `finalize_loop_state`; each step's preconditions are the prior step's postconditions. Every verb scoring outside the loop takes `arm_diagnostic_scoring` through `loop_start.py::open_diagnostic`, never a copy per verb.

## Registries complete at a declared step

**A registry whose members import back up the stack completes in `wiring.py::complete_registries`, never at import**; every process calls it once at entry.

## A schemaless pipeline is a 422

**A missing `backend_type`, no nodes or an unreachable backend raises a typed 422, never a 500** — a 500 reads as `transient` and the webapp retries a config only the operator can fix.
