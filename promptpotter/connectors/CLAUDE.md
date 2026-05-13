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

## Inner-cycle execution (still pending)

`promptpotter_wire_adapter` shapes the inner-cycle payload but the actual
"run an inner cycle" path is not yet wired — see
`docs/specs/m12-promptpotter-as-connector.md` § Deliverables. Two design
options for that follow-up:

- **Localhost endpoint.** Add `POST /inner/matches` to the FastAPI app
  (`promptpotter.main:app`). Outer cycle's `BackendClient` posts there;
  endpoint runs the inner cycle synchronously, returns a `/matches`-shaped
  response. Cleanest boundary; requires uvicorn running alongside.
- **In-process dispatch.** Branch `BackendClient.run_query` on the
  connector name; for `promptpotter`, dispatch to `runner.run_optimization`
  directly with isolated stores under `.runtime/inner/`. Faster, no extra
  process; introduces a fork in `BackendClient`.

Decision deferred until after the architectural skeleton is reviewed.

## Conventions

- Self-register via `register(Connector(...))` at import. No import-side
  effects beyond the registry write.
- Wire adapters are pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` returns `(queries, index_terms)` — the index_terms
  list may be empty for connectors with no retrieval index.
- `resolve_ground_truth(experiment_data, query)` returns `str | None` —
  used by trace-ingestion flows; safe to return `None`.
