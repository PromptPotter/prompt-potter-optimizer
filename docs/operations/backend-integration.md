# Backend Integration

PromptPotter optimizes any backend that speaks three endpoints. It reads the pipeline shape once and derives every optimization axis from it — zero backend-specific constants.

## Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Run one query under given `pipeline_params`; returns prediction, ranked candidates, and `diagnostics.warnings[]` (`{step, code, message}` — the self-healing signal). |
| `/pipeline` | GET | Full schema: nodes, types, parameters, allowed value sets, LLM-node prompt templates, named sequences. Read once at init to discover optimization axes. |
| `/status` | GET | Liveness; 200 when up. |

Wire shapes: [`../developer/node-standard.md`](../developer/node-standard.md).

## Connection security

The client talks to each backend over HTTP(S) with optional bearer-token auth. Four layers, all set at registration time:

- **Transport** — `https://` verifies the server cert; `http://` is cleartext. Pick the scheme in the registered `base_url`.
- **Auth** — set `TERMNORM_TOKEN` and every request carries `Authorization: Bearer …`; empty token → no header.
- **Backend gate** — the backend decides whether to *require* a token (`TERMNORM_REQUIRE_AUTH=1`). Mismatch → 401.
- **Resilience** — the session handshake auto-recovers; 429 honors `Retry-After`; 5xx/transport errors back off 1→2→4→8 s.

**Remote:** set `TERMNORM_REQUIRE_AUTH=1` + matching `TERMNORM_TOKEN` on both hosts, register with the `https://` URL, verify `curl https://…/status` returns 200. **Local:** same machine → `http://127.0.0.1:8000`, token unset; nothing leaves loopback.

**Not yet secured** (M12 control-plane, [ADR-0001](../adr/0001-m12-control-plane.md)): mutual-TLS, hot token rotation, per-tenant token isolation — one `TERMNORM_TOKEN` per install today.

## Self-healing a node

A node emits `diagnostics.warnings[]`; PromptPotter counts them, synthesizes a `RuntimeFailure` on the offending candidate, and L2 steers L1 away next round (persistent pattern → L3 replans). To wire a new node: emit the warning and set `degradation_threshold` in campaign config (0 disables). Routing strategy + anomaly detector are optional (default routes to L2). Mechanics: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Web-search strategy — a swept axis with a cost signal

TermNorm's `web_search` node gathers the evidence `entity_profiling` turns into a profile.
It exposes a `strategy` parameter (`snippets` / `scrape` / `hybrid`) as an optimization axis
(`/pipeline` → `web_search.optimizer.param_keys` + `param_allowed_values`). All three issue
exactly **one** metered Brave query per match, so sweeping `strategy` holds search cost
fixed and varies only evidence depth, latency, and LLM token cost. Sweep it on the
LCA ground-truth set and read the winner off accuracy vs the per-match cost block.

Each `/matches` response (and a langfuse `web_search` observation) now carries `web_cost`:
`{strategy, brave_queries, scrape_attempts, scrape_ok, scrape_failed, evidence_chars}`.
`brave_queries` is the metered cost (==1 on a live search, 0 when skipped/precomputed) and
is the free-tier ceiling; `evidence_chars` + `scrape_failed` are the efficiency/reliability
signal to weigh against accuracy. The status/sources/warning-`kind` contract is unchanged —
existing display and self-healing keep working. Backend rationale:
`TermNorm-excel/backend-api/docs/WEB_SEARCH_STRATEGY.md`.

## PromptPotter's own REST API

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/backends` · `…/backends/{id}` | List / detail |
| `GET …/backends/{id}/experiments` · `…/experiments/{exp_id}` | Backend experiments |
| `GET …/backends/{id}/pipeline` | Pipeline view (30 s cache) |
| `GET …/backends/{id}/health` | Backend health |
| `GET …/campaigns` · `…/campaigns/{id}` | List / detail |
| `GET …/health` | Service health |
| `POST …/commands/{kind}` | Mutations — register / sync a backend, stop / pause / steer a run (ADR-0001 command highway) |

```bash
uvicorn promptpotter.main:app --port 8001 --reload   # Swagger: /docs
```

## Currently tested with

[TermNorm](https://github.com/runfish5/TermNorm-excel) — terminology normalization, 6-node pipeline (cache · fuzzy · web search · entity profiling · token matching · llm ranking).

## Troubleshooting

| Symptom | Action |
|---------|--------|
| "No synced experiment data" | sync experiments for the backend |
| "No llm_ranking prompt found" | init the prompt registry before syncing |
| "No queries have entity_profile" | re-run replay with `entity_profiling` in the pipeline |
| Connection refused | backend down/unreachable — check `curl http://127.0.0.1:8000/status` |

User-level: [`../manual/05-troubleshooting.md`](../manual/05-troubleshooting.md).
