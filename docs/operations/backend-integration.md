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

Request: a query + `pipeline_params` (node-keyed config dicts plus a `steps` list). Response: prediction, ranked candidates, and `diagnostics.warnings[]` — per-query warnings with `{step, code, message}` PromptPotter uses for self-healing. See [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md) + [`../developer/node-standard.md`](../developer/node-standard.md).

### `GET /pipeline`

Returns the full pipeline schema — nodes with types, parameters, allowed value sets, prompt templates (for LLM nodes), and composition into named pipeline sequences. PromptPotter reads this once at init and uses it to discover optimization axes. **Zero backend-specific constants in PromptPotter** — everything is derived here.

JSON shape: [`../developer/node-standard.md § Pipeline declaration format`](../developer/node-standard.md).

### `GET /status`

Returns 200 OK when the backend is up. Used by `init` and by `/potter-run`'s audit phase.

## Connection security

PromptPotter's `BackendClient` (`promptpotter/infrastructure/backend.py`) talks to every registered backend over HTTPS or HTTP, with optional bearer-token auth. There is one shared model for all connectors — `BackendConnection` (`promptpotter/domain/backend.py`): `{id, name, backend_type, base_url, created_at}`.

| Layer | What happens | How to configure |
|---|---|---|
| **Transport (TLS)** | `httpx.AsyncClient` with default `verify=True`. `https://` base URLs verify the server certificate against the system trust store; `http://` URLs send everything cleartext. | Pick the scheme when you register the backend (`POST /backends` body `base_url`). Local dev → `http://127.0.0.1:8000`. Remote → use `https://` and a real cert. |
| **Auth (bearer token)** | Every request carries `Authorization: Bearer <token>` when the token is non-empty. Empty token → no header sent. The header is added by `BackendClient._get_http` (`backend.py:86`). | Set env var `TERMNORM_TOKEN` (read in `promptpotter/config/settings.py:137`). Wired through `application/bootstrap/wiring.py` and the `/backends/{id}/sync` + `/pipeline` routes. |
| **Backend-side gate** | The backend itself decides whether to *require* a token. TermNorm checks the `TERMNORM_REQUIRE_AUTH` flag (`backend-api/config/middleware.py` in the [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) repo). If the backend requires auth and you have no token, requests 401. | Same env var on the backend's side: set `TERMNORM_REQUIRE_AUTH=1` and `TERMNORM_TOKEN=<same-value>` on both sides. |
| **Handshake** | On first query the connector calls `POST /sessions` with the terms array (`infrastructure/backend.py:159`). On 400-with-`session` in the error detail, the client recovers by re-calling `POST /sessions` and retrying once. `GET /status` is used as a liveness probe by `init` and the dashboard. | None — happens automatically once `base_url` + token are set. |
| **Retries** | 429 honors `Retry-After`. 5xx and transport errors use 1 → 2 → 4 → 8 s exponential backoff (`MAX_429_ATTEMPTS = 5`). On every retry, an `on_warning` callback fires for ledger telemetry. | Tune via `BackendClient(timeout=…)` at construction; no env var. |

**Setup checklist (remote backend):**

1. On the **backend host**, set `TERMNORM_REQUIRE_AUTH=1` and `TERMNORM_TOKEN=<long-random-string>` in its env.
2. On the **PromptPotter host**, add the same `TERMNORM_TOKEN=<long-random-string>` to `.env`.
3. Register the backend with the `https://` URL:
   ```bash
   curl -X POST http://localhost:8001/promptpotter/v1/backends \
     -H 'Content-Type: application/json' \
     -d '{"name":"TermNorm Prod","backend_type":"termnorm","base_url":"https://termnorm.example.com"}'
   ```
4. Verify: `curl https://termnorm.example.com/status -H "Authorization: Bearer $TERMNORM_TOKEN"` should return 200.

**Local dev shortcut:** if backend and optimizer run on the same machine, use `http://127.0.0.1:8000` and leave `TERMNORM_TOKEN` unset on both sides. The header is omitted, the backend doesn't enforce auth, and no traffic leaves the loopback interface.

**What is *not* secured yet** (M12 control-plane scope, [`docs/specs/m12-control-plane.md`](../specs/m12-control-plane.md)): mutual-TLS for connector traffic; rotating the bearer token without restarting the optimizer; per-tenant token isolation in a multi-user webapp install (today the whole install shares one `TERMNORM_TOKEN`).

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
