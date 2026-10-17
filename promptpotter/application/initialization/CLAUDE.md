# application/initialization/ — run init

**Run init** — the ordered chain from a `new` / `resume` invocation to the first round: `init_services` (→ `Session`) → `populate_session_scoring` → `init_cycle` (resume or create) → `init_optimization_loop` (preflight, observability, `INIT.exit`, hand off to the round loop). **Each step's preconditions are the prior step's postconditions** — called out of order, the session is left under-wired. One member is deliberately NOT of that chain: `arm_diagnostic_scoring` is step 2 alone, the one path every verb that scores outside the loop takes — never a copy per verb.

## Registries complete at a declared step

**A registry whose members import back up the stack completes at a declared step, never at import:** `wiring.py::complete_registries` completes the connector, judge and optimizer registries and each optimizer runtime, and every process calls it once at its entry — so a half-wired member stops a process before it reads a ledger or spends.

## A schemaless pipeline is a 422

`init_services` raises `pipeline_config_invalid` — **422, typed rather than bare** — when the dataset's
`dataset.yaml::pipeline` names no `backend_type`, or the declaration (a backend's answer included) names no nodes
or does not parse, and `backend_unreachable` when a remote backend does not answer `GET /pipeline` or
answers a 5xx (`BackendClient.fetch_pipeline` classifies) — the local declaration overlays the backend's
schema, never stands in for it. Every measurement is keyed on that schema, so there is no degraded run to fall through to, and a 500 here reads as `transient` to the webapp,
which would retry a config only the operator can fix. The one SILENT arm is `init_cycle` returning
`(None, 1)` on an empty `session.backend_id`: the run continues with no per-cycle persistence.
