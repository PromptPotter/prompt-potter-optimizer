# application/initialization/ — run init

Every verb scoring outside the loop takes `loop_start.py::arm_diagnostic_scoring` (most through `open_diagnostic`), never a copy per verb.

## Registries complete at a declared step

**A registry whose members import back up the stack completes in `wiring.py::complete_registries`, never at import**; every process calls it once at entry. A CLI verb that parses no ledger, banked round or registered member declares `Verb.needs_registries=False`.

## A schemaless pipeline is a 422

A missing `backend_type` or no nodes raises a typed 422, never a 500 (a 500 reads as `transient` and the webapp retries); an unreachable backend is `BackendUnreachableError`, a 503, never a stand-in schema.
