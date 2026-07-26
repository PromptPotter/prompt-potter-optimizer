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
| `promptpotter` | `promptpotter.py` | `{query, meta_prompt_overrides}` → in-process inner cycle (`in_process_run` → `runner/inner/cycle.py`) | Noop (no remote service) | Optimizer-of-the-optimizer (L4) |

> **`llm_only` is a NODE name, never a connector.** The six single-node benchmarks
> declare an `llm_only` node inside a `termnorm` pipeline and route over HTTP to the
> server like any other. A connector of that name once existed (the no-server "Feature
> A" case) and was **deleted** — it had zero dataset adopters, and its in-process answer
> extraction merely duplicated what TermNorm's `_step_llm_only` already does over the
> wire. Do not re-add it: the single-node case is served by the TermNorm connector
> accepting an `llm_only` pipeline.

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
or `in_process` (runs in this process, no HTTP). `BackendClient.run_query`
**dispatches on this declared mode, never on the connector name** — so a new
backend's transport is a capability it declares, not a branch in the core loop.

**The `in_process` arm is wired (SHIPPED).** `run_query` calls the
connector-supplied `Connector.in_process_run(query, payload) -> {"data": {…}}` —
the same shape the scorer parses from an HTTP `/matches` body. The registry guard
(`__init__.py`) enforces the pairing: an `in_process` connector MUST supply
`in_process_run`, a `remote_http` one MUST NOT. One connector rides the seam today:

- **`promptpotter` (Feature B, SHIPPED)** — `in_process_run` is a thin delegate to
  `application/runner/inner/cycle.py::run_inner_cycle` (running a whole inner
  campaign is heavy orchestration — it belongs in `application/runner`, not the
  connector). That runner calls `run_optimization` in its **own `asyncio.Task`**
  (the three per-task ContextVars — `_CYCLE_LEDGER`, `_CURRENT_ROUND`,
  `_ABORT_CHECK` — isolate per task, not per call) under **sandboxed stores in a
  flat per-cycle registry `<workspace>/.inner/<spawn_cycle_id>/`**
  (`init_services(store=…)`; no active-pointer / capacity-1 collision). It is
  named by (owned by) the spawning cycle but kept **flat, not physically nested** —
  physical nesting (`…/.runtime/inner/…/.runtime/inner/…`) blows past Windows'
  260-char `MAX_PATH` at depth 1; a flat registry stays shallow at every depth, so
  the **re-entrant** invariant holds (task spawns at every level → L5+ nests). The
  spawning cycle publishes its context via `publish_inner_spawn_context` (runner
  seam, every cycle) so this context-free hook can find where to sandbox + which
  inner benchmark to run; the outer L1's meta-prompt mutations apply to the inner
  `_optimizer/` prompts through a per-run override ContextVar
  (`set_optimizer_prompt_overrides`, set inside the inner task). One process, no
  networking. The localhost-endpoint option is retained only as the future
  hosted/multi-tenant worker mode: a new `execution` value, dispatched on
  uniformly, with no core-loop edit.

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
- **The credential rides the connector.** `Connector.auth_token() -> str | None`
  is the ONLY route by which a bearer token reaches the wire, and
  `build_backend_client(connector, base_url)`
  (`infrastructure/backend.py`) is the ONLY place a `BackendClient` is
  constructed — it reads the token off the connector it was handed. Never name a
  credential at a construction site: four sites once passed
  `settings.TERMNORM_TOKEN` to whatever connector had been resolved, so a second
  `remote_http` backend would have had TermNorm's secret POSTed to its host. An
  `in_process` connector has no wire, so declaring a token on one fails the
  registry guard at import.
