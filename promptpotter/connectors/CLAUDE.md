# connectors/ — backend-specific hook bundles

Each connector packages everything PromptPotter needs to talk to one
backend kind. A connector is one file under this package exporting a
``Connector(...)`` binding (`protocol.py`), registered via the dict in
`__init__.py`. Adding a connector is intentionally local — no edits to
`application/config.py` or `infrastructure/backend.py`. Operating one is
local too: model/provider switches go in `datasets/{name}/pipeline.json::
nodes.{name}.config`, never in the backend's repo.

## Registered connectors

| Name | File | Wire shape | Session | Use |
|---|---|---|---|---|
| `termnorm` | `termnorm.py` | `{query, steps, node_config}` posted to `/matches` | `POST /sessions` handshake with terms array | TermNorm production backend |
| `promptpotter` | `promptpotter.py` | `{query, meta_prompt_overrides}` (in-process inner cycle) | Noop (no remote service) | Optimizer-of-the-optimizer (M12) |

## What the second connector taught the boundary

Adding `promptpotter` exercised the abstraction; the protocol held without
modification. Three observations the next connector should heed:

1. **Wire payload shape is connector-specific.** `termnorm` flattens
   `pipeline_params` into a `node_config` block; `promptpotter` keeps the
   nested-by-node shape under `meta_prompt_overrides`. The protocol
   carries the dict through; each connector decides its own outer key.
2. **Session contract works for in-process backends with a noop.**
   `PromptPotterSession` no-ops `set_terms`/`recover` so non-HTTP
   connectors can fully implement the protocol without a transport
   layer — but they pay the cost of the HTTP shape leaking into the
   rest of `BackendClient`.
3. **`extract_experiment` is the impedance-match seam.** TermNorm reads
   `mappings` + `runs[0].evaluation_results`; PromptPotter-self reads a
   simple `tasks` list. Both produce `(queries, index_terms)` for the
   downstream pipeline. New connectors design their `experiment_data`
   shape to fit the loader, not the other way around.

## Execution mode (declared) + inner-cycle run (Lane C3)

A connector declares **how its backend runs** via `Connector.execution`
(`ConnectorExecution`): `remote_http` (default — posts to a live `/matches`)
or `in_process` (runs an inner PromptPotter cycle, L4). `BackendClient.run_query`
**dispatches on this declared mode, never on the connector name** — so a new
backend's transport is a capability it declares, not a branch in the core loop.
`promptpotter` declares `execution="in_process"`; an `in_process` connector
loads + validates normally, then `run_query` raises a pointed
`NotImplementedError` on the first match request (instead of a confusing
transport error against a backend that isn't there).

What remains is the **inner-cycle run itself** — consuming the wire payload,
running the inner cycle, producing the three proxy metrics. That is Lane C3,
**decided in** [`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md):
`run_query`'s `in_process` arm dispatches to a connector-supplied
`in_process_run`, which calls `runner.run_optimization` in its **own asyncio
task** (the three per-task ContextVars — `_CYCLE_LEDGER`, `_CURRENT_ROUND`,
`_ABORT_CHECK` — isolate per task, not per call) under **isolated stores at
`.runtime/inner/`** (no active-pointer / capacity-1 collision). One process,
no networking. The same seam yields a second connector — in-process `llm_only`
(no TermNorm server for the basic case). The localhost-endpoint option is
retained only as the future hosted/multi-tenant worker mode: a new `execution`
value, dispatched on uniformly, with no core-loop edit.

## Conventions

- Declare the connector as a data row in the `CONNECTORS` dict in
  `__init__.py` — no `register()` call, no import-side effects.
- Wire adapters are pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` returns `(queries, index_terms)` — the index_terms
  list may be empty for connectors with no retrieval index.
- **Revision pinning is opt-in.** A connector can set
  `Connector.expected_revision` (the backend SHA/version this rev was
  developed against) and a `Connector.version_check(http, base_url) -> str | None`
  hook reading the backend's self-reported revision. Bootstrap
  (`application/bootstrap/wiring.py::_verify_connector_revision`)
  WARNs on drift; no-op when either field is `None`. Pattern motive:
  pre-flight gate Q6 extended to debug state — cross-repo dependency
  drift becomes visible at session start, not weeks later in spend
  accounting. The same shape works for any future connector.
