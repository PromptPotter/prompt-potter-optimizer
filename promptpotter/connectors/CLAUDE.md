# connectors/ — backend-specific hook bundles

Each connector packages everything PromptPotter needs to talk to one
backend kind. A connector is one file under this package exporting a
``Connector(...)`` binding (`protocol.py`), registered via the dict in
`__init__.py`. Adding a connector is intentionally local — no edits to
`application/campaign_config.py` or `infrastructure/backend.py`. Operating one is
local too: model/provider switches go in `datasets/{name}/pipeline.yaml::
nodes.{name}.config`, never in the backend's repo.

## Registered connectors

| Name | File | Wire shape | Session | Use |
|---|---|---|---|---|
| `termnorm` | `termnorm.py` | `{query, steps, node_config}` posted to `/matches` | `POST /sessions` handshake with terms array | TermNorm production backend |
| `promptpotter` | `promptpotter.py` | `{query, optimizer_prompt_overrides}` → in-process inner cycle (`in_process_run` → `runner/inner/spawn.py`) | Noop (no remote service) | Optimizer-of-the-optimizer (L4) |

> **`llm_only` is a NODE name, never a connector.** Every single-node benchmark
> declares an `llm_only` node inside a `termnorm` pipeline and routes over HTTP to the
> server like any other. A connector of that name once existed (the no-server "Feature
> A" case) and was **deleted** — it had zero dataset adopters, and its in-process answer
> extraction merely duplicated what TermNorm's `_step_llm_only` already does over the
> wire. Do not re-add it: the single-node case is served by the TermNorm connector
> accepting an `llm_only` pipeline.

## What the second connector taught the boundary

Adding `promptpotter` exercised the abstraction and the protocol held unmodified. Three
things the next connector should heed. **Wire payload shape is connector-specific** — each
decides its own outer key (`termnorm` flattens `pipeline_params` into `node_config`,
`promptpotter` nests under `optimizer_prompt_overrides`) and the protocol just carries the
dict through. **The session contract works for in-process backends via a noop**
(`PromptPotterSession` no-ops `set_terms`/`recover`), at the cost of the HTTP shape leaking
into the rest of `BackendClient`. And **`extract_experiment` is the impedance-match seam**:
both connectors yield `(queries, index_terms)` from very different bodies, so **a new
connector shapes its `experiment_data` to fit the loader, never the reverse**.

## TermNorm is not a third party

**A structural bug whose cause sits in TermNorm's code gets fixed in TermNorm — never
papered over on this side.** It lives at `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel`
(backend under `backend-api/`), the same project as PromptPotter, split into a separate
repo for security reasons only; folding it back in is the goal. That makes it the
exception to "backends are read-only" — and to nothing else: per-dataset config still
rides the overlay, backend *behaviour* still earns a TermNorm root-fix, and which one
you have is decided by which side actually holds the cause. **Cross-repo edits are
authorized:** edit the local repo directly (runfish5 authors it); if unavailable,
coordinate with **runfish5 on GitHub**. The PP↔TermNorm highway is a shape contract —
touch one side, fix both. Debugging war-stories →
[`../../docs/operations/backend-integration.md`](../../docs/operations/backend-integration.md)
§ Debugging the highway.

## Execution mode — declared, never name-branched

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
  `application/runner/inner/spawn.py::run_inner_cycle` (running a whole inner
  campaign is heavy orchestration — it belongs in `application/runner`, not the
  connector). That runner calls `run_optimization` in its **own `asyncio.Task`**
  (the three per-task ContextVars — `_CYCLE_LEDGER`, `_CURRENT_ROUND`,
  `_ABORT_CHECK` — isolate per task, not per call; the child gets a COPY, which is
  how `_ABORT_CHECK` carries the outer's pause into the inner run) under **sandboxed stores in a
  flat per-cycle registry `<workspace>/.inner/<key>/`**
  (`init_services(store=…)`; no active-pointer / capacity-1 collision). It is
  named by (owned by) the spawning cycle but kept **flat, not physically nested** —
  physical nesting (`…/.runtime/inner/…/.runtime/inner/…`) blows past Windows'
  260-char `MAX_PATH` at depth 1; a flat registry stays shallow at every depth, so
  the **re-entrant** invariant holds (task spawns at every level → L5+ nests). The
  spawning cycle publishes its context via `publish_inner_spawn_context` (runner
  seam, every cycle) so this context-free hook can find where to sandbox + which
  inner benchmark to run. **Owner and asker are two facts, and a fork splits them:**
  once the cycle id is final `retarget_inner_spawn` moves only the *asker* an inner run
  stamps as `spawned_by.outer_cycle_id`, while the sandbox owner never follows a fork —
  a repaired cell CONTINUING the campaign the parent banked is the whole point, and one
  field meaning both filed every measurement a fork paid for under the cycle it
  superseded. The outer L1's optimizer prompt mutations apply to the inner
  `assets/optimizer/pipeline.yaml` prompts through a per-run override ContextVar
  (`set_optimizer_prompt_overrides`, set inside the inner task). One process, no
  networking. The localhost-endpoint option is retained only as the future
  hosted/multi-tenant worker mode: a new `execution` value, dispatched on
  uniformly, with no core-loop edit.

## Registering a connector

**Declare a built-in as a data row in the `_BUILTIN` dict in `__init__.py` — never a
`register()` call, and never an append to `CONNECTORS`.** `CONNECTORS` is not that dict:
it is what `_load()` returns after merging `_BUILTIN` with the `promptpotter.connectors`
entry points and running `_validate` over both. Appending to it post-import registers a
connector that was never validated, which is the one thing the module exists to prevent.
A connector shipped from **another** package declares the entry point instead and touches
nothing here ([`stable-api.md`](../../docs/developer/stable-api.md) §1).

## A connector is trusted code, not sandboxed — and that is stated, not implied

Loading one imports its module into this process, where it sees the provider API keys, the
tenant tree and the identity store, exactly as a module we ship does. Entry points do not
weaken that boundary (anything that can install a distribution into this environment can
already run code here), but they do make the trust *explicit*: installing a connector package
is trusting its publisher completely, and this repo's capability scoping (ADR-0005) governs
API principals, not in-process code. **`CONNECTOR_ORIGINS` is the audit surface** — it names
the distribution behind every registered key, including the ones that are ours.

Two rules follow, both enforced in `_load` / `_validate`. **A plugin may not shadow a
built-in:** `CONNECTORS["promptpotter"]` is read by name by the L4 inner runner
(`application/runner/inner/tasks.py`), so which object answers that key is not a third
party's call. **A broken plugin is fatal, never skipped:** skipping would trade a loud error
naming the package for `connector 'x' not registered` at mint time, with nothing pointing at
the cause.

**Discovery is two paths; validation is one. Deliberately.** Declaring our own two as entry
points would be the tidier "single path", and it is wrong here: it makes
`import promptpotter.connectors` depend on this distribution's installed metadata, so a plain
source-tree run would find zero backends. The property worth protecting — a half-wired
connector fails at import, never mid-campaign — lives in the validator, not in the channel it
arrived through.

## The credential rides the connector

**`Connector.auth_token() -> str | None` is the ONLY route by which a bearer token reaches
the wire, and `build_backend_client(connector, base_url)` (`infrastructure/backend.py`) is
the ONLY place a `BackendClient` is constructed** — it reads the token off the connector it
was handed. Never name a credential at a construction site: four sites once passed
`settings.TERMNORM_TOKEN` to whatever connector had been resolved, so a second `remote_http`
backend would have had TermNorm's secret POSTed to its host. An `in_process` connector has
no wire, so declaring a token on one fails the registry guard at import.

## Conventions

- Wire adapters are pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` returns `(queries, index_terms)` — the index_terms
  list may be empty for connectors with no retrieval index.
- **Revision pinning is opt-in.** A connector can set
  `Connector.expected_revision` (the backend SHA/version this rev was
  developed against) and a `Connector.version_check(http, base_url) -> str | None`
  hook reading the backend's self-reported revision. Init
  (`application/initialization/wiring.py::_verify_connector_revision`)
  WARNs on drift; no-op when either field is `None`. Pattern motive:
  the pre-flight gate's debug-state bullet, reaching across a repo
  boundary — cross-repo dependency
  drift becomes visible at session start, not weeks later in spend
  accounting. The same shape works for any future connector.
