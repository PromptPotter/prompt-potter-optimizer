# Run initialization

What happens between `python -m promptpotter new|resume` and the first round —
the phase the operator sees as `INIT` (terminal: `✓ Initialized`; webapp:
"campaign is initialising"). Four functions in `application/initialization/`,
ordered. Each step's preconditions are the prior step's postconditions —
calling them out-of-order leaves the session under-wired.

```
┌──────────────────────────────────────────────────────────────────────┐
│  init_services             (wiring.py)                               │
│    ├─ resolve identity     (default_identity() at Stage-0;           │
│    │                        Stage-1 swaps in OIDC verification)      │
│    ├─ build_stores(identity, projects_root=…)                        │
│    ├─ connector resolve    (datasets/{name}/pipeline.yaml)           │
│    ├─ BackendClient        (wire + session adapter from connector)   │
│    ├─ GET /pipeline        (merged with dataset overlay)             │
│    ├─ register backend     (idempotent)                              │
│    ├─ Session(...)         (store, client, schema, identity)         │
│    └─ load dataset          OR  sync experiment extract              │
│    ↓                                                                 │
│  postcondition: session has store + client + schema + samples        │
│                 (no scoring yet, no cycle yet)                       │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│  populate_session_scoring  (loop_start.py)                           │
│    ├─ compile_scorer(formula)         → session.scoring.scorer       │
│    ├─ auto_scorer_id(formula)         → session.scoring.scorer_id    │
│    ├─ compile_round_scorer(formula)?  → session.scoring.round_scorer │
│    ├─ session.state.obs = obs                                        │
│    └─ session.source = source                                        │
│    ↓                                                                 │
│  postcondition: session.scoring fully populated; obs wired           │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│  init_cycle                (loop_start.py)                           │
│    ├─ resolve cycle_id      (from override or content-hash)          │
│    ├─ store.load(hop)                                                │
│    ├─ rewind_to_round?      (when --from N)                          │
│    ├─ HIT  → resumed_from_round = N+1  (snapshot refresh lives in    │
│    │           resume_with_divergence_check, classifier-driven)      │
│    └─ MISS → store.create(...) at round 0; resumed_from_round = 1    │
│    ↓                                                                 │
│  postcondition: cycle exists in store; resumed_from_round is next L1 │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│  init_optimization_loop    (loop_start.py)                           │
│    ├─ origin scoring        (JobSearchPoint from config)             │
│    ├─ Cycle(...)            (ledger, escalation state, axes index)   │
│    ├─ fork-on-divergence?   (sibling mint via inherit_from)          │
│    ├─ emit INIT.exit        (operator-visible boundary)              │
│    └─ run_round_loop(cycle) (the L1/L2/L3 loop proper)               │
└──────────────────────────────────────────────────────────────────────┘
```

## What each step reads and writes

| Step | Reads | Writes |
|---|---|---|
| `init_services` | `datasets/{name}/pipeline.yaml`, `.env` (provider keys), backend `/pipeline` | `Session.{store, backend_client, pipeline_schema, samples, index_terms, tenant, langfuse}` |
| `populate_session_scoring` | `CampaignConfig.scoring` | `Session.{scoring, state.obs, source}` |
| `init_cycle` | `Session.{store, backend_id, samples}`, `origin_jsp` | the cycle's `campaigns/{campaign_id}/cycles/{cycle_id}/` directory; returns `(cycle_id, resumed_from_round)` |
| `init_optimization_loop` | everything above + `origin_accuracy` | `Cycle.{ledger, escalation, opt_sp, tracking, axes}`; emits INIT.exit |

## The line between init and runner

Init writes to `Session`. The runner reads `Session` and the
`CycleEventLog`; it never writes back to `Session` outside the per-round
state-tracking bundle (`session.state`).

If a piece of state must survive across `new` / `resume` invocations, it goes
through the ledger (or its projections), not `Session`. If it's
per-process per-cycle, it lives on `Session.state` or `Cycle`. The
separation fails loud (state that skips the ledger doesn't survive a
restart — visible immediately); no standing test, see
[`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

## When the chain breaks

- **`init_services` raises `pipeline_config_invalid` (422)** → the dataset's
  `pipeline.yaml` declares no `backend_type`, or neither it nor the backend
  yields a parseable schema. Typed rather than bare because every measurement is
  keyed on that schema, so there is no degraded run to fall through to — and a
  500 here reads as `transient` to the webapp, which would retry a config only
  the operator can fix.
- **`populate_session_scoring` raises** → bad scoring formula in
  `campaign.json`. Diagnostic is in the exception; check the formula DSL
  at [`/developer/stable-api.md`](stable-api.md) §2.
- **`init_cycle` returns `(None, 1)`** → silent fallback when
  `session.backend_id` is empty. Means the runner runs without
  per-cycle persistence — surprising in `new` / `resume`.
- **`init_optimization_loop` fails origin scoring** → the backend
  doesn't accept the origin pipeline_params. Check
  `datasets/{name}/pipeline.yaml::nodes.{name}.config`.
