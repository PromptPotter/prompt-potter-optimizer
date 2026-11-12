# Backend integration

PromptPotter optimizes any backend registered as a `Connector`; every axis is derived from the pipeline that connector declares — served at `GET /pipeline` by an HTTP backend, built by the connector, or, where it declares neither, the `pipeline:` block of the dataset's own `dataset.yaml`, which overlays the other two.

## The HTTP default — TermNorm's endpoints

A `remote_http` connector that keeps `Connector`'s defaults speaks two of the endpoints [TermNorm](https://github.com/runfish5/TermNorm-excel) serves: `/matches` and `GET /pipeline`. `/status` is TermNorm's own declared probe; a connector that declares none probes nothing.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Run one query under given `pipeline_params`; returns prediction, ranked candidates, and `diagnostics.warnings[]` (`{step, code, message}` — the self-healing signal). |
| `/pipeline` | GET | Full schema: nodes, types, parameters, allowed value sets, LLM-node prompt templates, named sequences. Read live at run init and on every draft read. |
| `/status` | GET | TermNorm's liveness and version probe; 200 when up. |

Wire shapes: [`../developer/node-standard.md`](../developer/node-standard.md). PromptPotter's own API roster is `/docs` (`openapi.generated.json`).

**A gateway (OmniRoute, OpenRouter) is a backend like any other, and a campaign points at a pinned model, never at a router that chooses one per request.** Pinning the model does not pin the HOST; a backend may report per LLM node which host answered (`TokenUsageRecord.served_by`).

### Optional `step_tokens` fields — read, never required

A key is ABSENT when the backend reported nothing; a present key holds its declared type or the read raises — `"1200"` is refused, never coerced.

| Key | Missing ⇒ |
|---|---|
| `cost_usd` | the node's tokens are priced at our rate table as `rate_priced_usd` — counted by every ceiling, never called spent |
| `model` | spend buckets by the provider slug |
| `served_by` | host attribution unavailable; no in-repo connector writes it |
| `finish_reason` | a truncation cannot be told from an empty response; every empty terminal grades fatal `empty_response` |
| `reasoning` | the thinking share is unreadable, so a slow node reads as a slow provider |
| `cache_read` | the prefix-cache reading is `unreported` — rendered `c?`, distinct from `c0%` |
| `unreported_attempts` | a resend that reported no usage bills nothing; each counted one is held at one attempt's bound |

### What a backend owes a campaign under a spend ceiling — required

A ceiling is enforced before a cell is SENT, at the most the cell may cost; without these, every cell is refused. A backend whose limits are all ones we send derives the bound in its connector. A connector whose cells no rate bounds declares `cells_priced=False` (n8n): a launch under a dollar ceiling, the run's or the host's allowance, is refused at the mint, `budget_unenforceable`.

- **`/pipeline` serves `spend_bound` on every node that can bill** — `{kind: "llm", attempts, input_bytes, max_tokens}` plus `hosts` where `allow_fallbacks: false`; `{kind: "web", queries, usd_per_query}`. COUNTS the backend enforces, never prices: PromptPotter prices them at the dearest host.
- **Every billed attempt is reported** in `step_tokens`, a failed one too (`data.step_tokens` on the error envelope); none reported holds the cell at its whole bound. A backend that retries on its own owes `unreported_attempts`.
- **A 4xx releases its bound** — except a 408 and a relay's 422 for a reply that failed validation: that model generated.
- **Search spend is reported in dollars**, as `data.web_cost.usd`.

## Connection security

An HTTP connector's endpoint, token and header reach the wire only through the connector (`Authorization: Bearer` unless declared; TermNorm's read `TERMNORM_URL` / `TERMNORM_TOKEN`, n8n's `N8N_HARNESS_URL` / `N8N_HARNESS_TOKEN`). An empty `TERMNORM_TOKEN` sends no header; the backend decides whether to require it (`TERMNORM_REQUIRE_AUTH=1`; mismatch → 401), and the Linux deploy provisions one shared token per install, required even on loopback.

**Resilience** — a 429 is the run's backpressure (one shared cooldown); a 5xx or a connection broken before any reply backs off 1→2→4→8 s unless the body says `detail.retryable: false`; a connection never made waits for the connector's own probe up to an outage bound; a link dropped mid-wait surfaces through TCP keepalive as a broken connection, resent; a cell open past the smaller of `cell_envelope_s` and the launch's wall clock left is cut `HALTED`; **a read timeout is never resent** — the backend is still working, and billing, the first request.

## A webhook backend — the n8n eval harness

`connectors/n8n.py` drives one n8n workflow holding a copy of a production LLM node behind an authenticated webhook: it POSTs to the URL itself, reads a reply with no `data` envelope and no timings, and declares its own pipeline (one prompt node, `harness`).

| Variable | Holds |
|---|---|
| `N8N_HARNESS_URL` | the harness webhook, e.g. `https://<instance>/webhook/eval-harness-classify`; one per install |
| `N8N_HARNESS_TOKEN` | the secret the webhook's header-auth credential holds |
| `N8N_HARNESS_AUTH_HEADER` / `N8N_HARNESS_AUTH_PREFIX` | the header it rides in and what precedes it; default `X-Eval-Secret`, no prefix |

- **Request** `{case_id, input, expected, prompt, params}` — `Sample.key`, the row's query, its gold (informational), the rendered candidate (`""` asks for the production text), the node's `output_schema` / `answer_fields`. **Reply** `{case_id, answer, raw}` — `answer` is graded (a label, or the graded fields as an object or its JSON string); an HTTP error, a timeout or an empty `answer` is an errored cell, never a wrong answer.
- **The model is the n8n node's.** Cells are keyed on a hash of `N8N_HARNESS_URL` less any trailing `/`, so a model changed inside n8n is a new dataset. **No usage is reported**, so a dollar or token ceiling refuses it (`NO_RATE`).
- **Each `output_schema` property's description is a search axis the harness must apply**; one answer field opens the `response_format` toggle, whose `text` arm sends no `params`. Several graded fields: [`../research/structured-output.md`](../research/structured-output.md) § Several graded fields.

```yaml
source:
  recipe: rows
  items:
  - query: '{"from": "billing@example.com", "subject": "Your plan renewed", "body": "No action needed."}'
    ground_truth: "false"
pipeline:
  backend_type: n8n
campaign:
  scoring: label_match(predicted, ground_truth)
```

## Self-healing a node

A node emits `diagnostics.warnings[]`; PromptPotter synthesizes a `RuntimeFailure` on the candidate, shown to the next round's generation ([`../architecture.md`](../architecture.md) § Errors heal upward). Which advisories are fatal: `domain/results_health.py::classify_result`. TermNorm's `web_search` exposes `strategy` (`snippets` / `scrape` / `hybrid`) as an axis; each issues one metered query per match.

## Package cache — a containerized cell measures the agent, never the network

Three mechanisms, none changing measurement identity:

1. **Fetch once per machine** — one cache container, `promptpotter-package-cache` (`apt-cacher-ng` on port 3142), reached at `host.docker.internal:3142` (on Linux the compose overlay maps it with `host-gateway`; firewalld must let bridge traffic reach the port). `docker logs promptpotter-package-cache` shows hits; `docker rm -f` stops it; `docker volume rm` drops the downloads. apt only; pip and npm are HTTPS-only.
2. **An infrastructure failure is never a measurement.** A registry, mirror or DNS failure, or a setup or verifier out of clock, is retried with bounded backoff, then banks as `ErrorCategory.CONNECTION` (carrying every attempt's spend) and halts the walk `BACKEND_UNREACHABLE`; a provider out of credit is not retried (`PROVIDER_CREDIT`). Either is a hole `resume` re-measures.
3. **No registry call once a task image exists** — a kept `hb__<hash>` tag starts as a prebuilt image.

**A stale index reads as the backend unreachable** — the cache answers `200`, apt refuses the signature: delete `InRelease`, `Release` and `Packages` under the cache and restart, keeping the `.deb`s.

**Scope.** A dataset opts in with `package_cache: <scope>`; a connector declares its scopes and run init refuses any other. Harbor's `verifier` scope is unobservable by the agent; `agent` is not a scope — the install runs at image build time, the cache a build argument Docker keeps out of the image.

## Debugging the highway

- A backend whose `/status` times out almost always has a blocking call in an `async def` request path: offload the sync I/O via `asyncio.to_thread`.
- **The highway is a cross-repo contract — change one side, fix both.** TermNorm's error envelope is `{status, message, code}`, not FastAPI's `{detail}`; session self-heal keys on `code: "no_session"` and resend policy on `detail.retryable` — codes, never substrings — both read by TermNorm's connector, never by the agnostic client. `--reload` wipes TermNorm's in-memory sessions; PP self-heals (re-`POST /sessions` + retry).
- Keep request timeouts under `Connector.cell_envelope_s`; an unbounded optimizer node blows `OPTIMIZER_CALL_DEADLINE_S` before round 1.
