# Backend Integration

PromptPotter works with any backend that implements a small contract. The pipeline can be anything from a single LLM call to a multi-step pipeline with retrieval, enrichment, and ranking nodes. PromptPotter discovers the pipeline structure via `GET /pipeline` and optimizes the parameters it finds there.

---

## Backend contract

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Evaluate a query with pipeline parameters |
| `/pipeline` | GET | Pipeline schema (nodes, parameters, node types) |
| `/status` | GET | Health check |

### `POST /matches`

The request carries a query plus `pipeline_params` (nested dicts keyed by node name). The response carries the prediction, any ranked candidates, and `diagnostics.warnings[]` — per-query warnings with `{step, code, message}` that PromptPotter uses for self-healing and optimizer intelligence. See [../developer/self-healing-internals.md](../developer/self-healing-internals.md) and [../developer/node-standard.md](../developer/node-standard.md).

### `GET /pipeline`

Returns the full pipeline schema — nodes with their types, parameters, allowed value sets, prompt templates (for LLM nodes), and composition into named pipeline sequences. PromptPotter reads this once at init and uses it to discover optimization axes. Zero backend-specific constants in PromptPotter — everything is derived from this endpoint.

See [../developer/node-standard.md § Pipeline declaration format](../developer/node-standard.md) for the JSON shape.

### `GET /status`

Returns 200 OK when the backend is up. Used by `init` and by the `/potter-run` skill's audit phase.

---

## Currently tested with

[TermNorm-excel](https://github.com/runfish5/TermNorm-excel) — AI terminology normalization. 5-node active pipeline: cache, fuzzy matching, web search, entity profiling, token matching. LLM ranking exists but is excluded due to bugs.

---

## REST API endpoints (PromptPotter's own)

For integrating PromptPotter's read-only API into other services:

| Endpoint | Description |
|----------|-------------|
| `POST /promptpotter/v1/backends` | Register a new backend connection |
| `GET /promptpotter/v1/backends` | List registered backends |
| `POST /promptpotter/v1/backends/{id}/sync` | Sync experiments from backend |
| `GET /promptpotter/v1/backends/{id}/pipeline` | Dynamic pipeline view (30s cache) |
| `GET /promptpotter/v1/campaigns` | List optimization campaigns |
| `GET /promptpotter/v1/campaigns/{id}` | Campaign detail with trial summaries |
| `GET /promptpotter/v1/health` | Service health check |

Start the API server with:

```bash
uvicorn promptpotter.main:app --port 8001 --reload
```

Swagger docs at `http://localhost:8001/docs`.

---

## Troubleshooting integration issues

**"No synced experiment data"** — Run `await client.sync_experiments(store, backend_id)` to sync from the backend.

**"No llm_ranking prompt found"** — The backend needs to expose prompts in experiment data. Ensure the prompt registry is initialized before syncing.

**"No queries have entity_profile"** — Re-run replay with `entity_profiling` in the pipeline nodes.

**Backend connection refused** — Check the backend is running and reachable at the URL passed to `--backend-url`. Verify with `curl http://127.0.0.1:8000/status`.

For user-level troubleshooting, see [../manual/05-troubleshooting.md](../manual/05-troubleshooting.md).
