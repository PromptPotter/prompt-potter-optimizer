# Backend Integration

PromptPotter works with any backend that implements a small contract. The pipeline can be anything from a single LLM call to a multi-step retrieval/enrichment/ranking pipeline. PromptPotter discovers the structure via `GET /pipeline` and optimizes the parameters it finds.

```
┌──────────────────────┐                       ┌──────────────────────┐
│  Your Backend        │  GET  /pipeline   ──► │  PromptPotter        │
│  (any pipeline)      │                       │  Optimizer           │
│                      │  POST /matches    ◄── │                      │
│  runs the task       │   {prompt, params}    │  generates candidates│
│                      │                       │  scores + critiques  │
│                      │  → predictions    ──► │  iterates            │
└──────────────────────┘                       └──────────────────────┘
```

## Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Evaluate a query with pipeline parameters |
| `/pipeline` | GET | Pipeline schema (nodes, parameters, node types) |
| `/status` | GET | Health check |

### `POST /matches`

Request: a query + `pipeline_params` (nested dicts keyed by node name). Response: prediction, ranked candidates, and `diagnostics.warnings[]` — per-query warnings with `{step, code, message}` PromptPotter uses for self-healing. See [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md) + [`../developer/node-standard.md`](../developer/node-standard.md).

### `GET /pipeline`

Returns the full pipeline schema — nodes with types, parameters, allowed value sets, prompt templates (for LLM nodes), and composition into named pipeline sequences. PromptPotter reads this once at init and uses it to discover optimization axes. **Zero backend-specific constants in PromptPotter** — everything is derived here.

JSON shape: [`../developer/node-standard.md § Pipeline declaration format`](../developer/node-standard.md).

### `GET /status`

Returns 200 OK when the backend is up. Used by `init` and by `/potter-run`'s audit phase.

## Currently tested with

[TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization. 5-node active pipeline: cache, fuzzy matching, web search, entity profiling, token matching. LLM ranking exists but is excluded due to bugs.

## Wiring a new node into self-healing

Reference: `web_search`. Default chain works for any target node that emits warnings.

| Step | What | Required? |
|------|------|-----------|
| **1** | Emit `diagnostics.warnings[]` with `{step, code, message}` from the backend | **Yes** |
| **2** | Add routing strategy for `{step}:{code}` | No (defaults to L2) |
| **3** | Add anomaly detector | No |
| **4** | Set `degradation_threshold` in campaign config | **Yes** (0 = disabled) |

Example — adding `entity_profiling` error detection:

```json
{"step": "entity_profiling", "code": "schema_error", "message": "Failed to parse JSON"}
```

`DegradationCheck` counts the warning, synthesises a `RuntimeFailure` on the offending candidate, the round completes normally. L2 reads the failure next round and steers L1 away from the failing config region. Pattern persists → L3 replans. Mechanics: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## REST API (PromptPotter's own)

| Endpoint | Description |
|----------|-------------|
| `POST /promptpotter/v1/backends` | Register a new backend connection |
| `GET /promptpotter/v1/backends` | List registered backends |
| `POST /promptpotter/v1/backends/{id}/sync` | Sync experiments from backend |
| `GET /promptpotter/v1/backends/{id}/pipeline` | Dynamic pipeline view (30s cache) |
| `GET /promptpotter/v1/campaigns` | List campaigns |
| `GET /promptpotter/v1/campaigns/{id}` | Campaign detail with trial summaries |
| `GET /promptpotter/v1/health` | Service health check |

```bash
uvicorn promptpotter.main:app --port 8001 --reload
# Swagger: http://localhost:8001/docs
```

## Troubleshooting integration

| Symptom | Action |
|---------|--------|
| "No synced experiment data" | `await client.sync_experiments(store, backend_id)` |
| "No llm_ranking prompt found" | Initialize the prompt registry before syncing |
| "No queries have entity_profile" | Re-run replay with `entity_profiling` in pipeline nodes |
| Connection refused | Check the backend is running and reachable; verify `curl http://127.0.0.1:8000/status` |

User-level troubleshooting: [`../manual/05-troubleshooting.md`](../manual/05-troubleshooting.md).
