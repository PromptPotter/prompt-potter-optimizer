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
| `GET /api/v1/backends` | List registered backends |
| `GET …/backends/{id}/health` | Backend health |
| `GET …/campaigns` · `…/campaigns/{id}` | List / detail |
| `GET …/health` | Service health |
| `POST …/commands/{kind}` | Mutations — register / sync a backend, stop / pause / steer a run (ADR-0001 command highway) |

```bash
uvicorn promptpotter.main:app --port 8001 --reload   # Swagger: /docs
```

## Currently tested with

[TermNorm](https://github.com/runfish5/TermNorm-excel) — terminology normalization, 6-node pipeline (cache · fuzzy · web search · entity profiling · token matching · llm ranking).

## Debugging the highway (hard-won 2026-06-16 — read before flailing)

A long debug session taught these; future-me: be systematic and code-first, not operational-first.

- **Diagnose from the code path, not by restarting.** When the backend "goes down" — `/status` itself times out, scoring stalls — the cause is almost always a **blocking call in an `async def` request path**, not a crash / SQLite lock / double-start. Symptom→action: grep the handler for sync I/O (`requests`, `ThreadPoolExecutor.map`, `time.sleep`, blocking DB) FIRST. Killing/restarting the worker and theorizing about ports/timeouts is the slow path and hid the real bug for an hour. Root found: `web_generate_entity_profile` (async) ran `_brave_search` + `list(executor.map(scrape_url…))` synchronously, freezing the single uvicorn worker for the whole web step → every concurrent request (incl. `/status`) stalled. Fix = offload via `asyncio.to_thread` / `run_in_executor`. **Backend async hygiene is a standing check: no sync I/O on the event loop.**
- **The highway IS a cross-repo contract — change one side, fix both.** PP consumes TermNorm response *shapes*, so a shape change on either side silently breaks the other. Known coupling points: the error envelope is TermNorm's `{status, message, code}` (a global handler in `main.py`), **not** FastAPI's `{detail}` — PP must read `message`. Session-loss self-heal keys on a stable machine-readable `code: "no_session"` (prefer codes over substring/shape guessing). The web_search warning `stats` dict keys are read by PP's display. When you touch a response field, grep the *other* repo for its consumer.
- **`--reload` wipes the in-memory session every backend code edit.** TermNorm holds sessions in `user_sessions = {}` (process memory). Any backend edit → uvicorn reload → in-flight PP runs hit `400 no_session`. PP now self-heals (re-`POST /sessions` + retry); keep it that way — a developer editing the backend mid-run must not abort the campaign.
- **openrouter latency is the recurring root.** The same provider slowness hit (a) the optimizer (`datasets/_optimizer/pipeline.json` loop nodes at `reasoning_effort=high` + `max_tokens=20000` on openrouter/gpt-oss-120b → blew the 360s `OPTIMIZER_CALL_DEADLINE_S`×2 deadline → `OPTIMIZER_TIMEOUT` before round 1) and (b) `entity_profiling` (openrouter/gpt-oss-20b, ~20 tok/s, 47s tails). Survival guards: bounded optimizer reasoning (`medium`) + request timeouts under PP's 120s `QUERY_TIMEOUT`. The durable fix is provider (groq is far faster) — but that's the operator's daily-volume knob; don't flip it unprompted.
- **The `web_search` hang was fixed structurally, not with a bigger timeout (2026-06-17).** The old multi-minute scrape freeze is retired by making evidence depth a strategy axis: `scrape` runs under a hard `scrape_budget` deadline, `snippets` never hangs, `hybrid` (default) falls back per source. Contract, `web_cost` fields, and how to sweep it → § Web-search strategy above. PP-side overlay wiring in `datasets/lca-termnorm/pipeline.json` still pending.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| "No synced experiment data" | sync experiments for the backend |
| "No llm_ranking prompt found" | init the prompt registry before syncing |
| "No queries have entity_profile" | re-run replay with `entity_profiling` in the pipeline |
| Connection refused | backend down/unreachable — check `curl http://127.0.0.1:8000/status` |

User-level: [`../manual/05-troubleshooting.md`](../manual/05-troubleshooting.md).
