# Backend Integration

PromptPotter optimizes any backend that speaks three endpoints. It reads the pipeline shape once and derives every optimization axis from it — zero backend-specific constants.

## Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/matches` | POST | Run one query under given `pipeline_params`; returns prediction, ranked candidates, and `diagnostics.warnings[]` (`{step, code, message}` — the self-healing signal). |
| `/pipeline` | GET | Full schema: nodes, types, parameters, allowed value sets, LLM-node prompt templates, named sequences. Read once at init. |
| `/status` | GET | Liveness; 200 when up. |

Wire shapes: [`../developer/node-standard.md`](../developer/node-standard.md). Tested with [TermNorm](https://github.com/runfish5/TermNorm-excel); PromptPotter's own API roster is `/docs` (`openapi.generated.json`).

**A gateway (OmniRoute, OpenRouter) is a backend like any other, and a campaign points at a pinned model — never at a router that chooses one per request.** Both choosing on the eval path means the rows were not measured on one model, and no fork or `verify` recovers that campaign.

**Pinning the model does not pin the HOST, and that is what `served_by` is for.** Hosts of one model disagree systematically, so a backend may report per LLM node which upstream host answered; it is banked on `TokenUsageRecord.served_by`.

### Optional `step_tokens` fields — read, never required

Every key is ABSENT when the backend reported nothing, and absence reads as "not reported". **An older backend that sends none degrades what can be asked afterwards and breaks nothing**, so no version gate exists between the repos. **The roster is `domain/spend.py::StepUsage`**; an in-tree connector hands over its `wire()`. **Absent is the only soft case:** a present key holds its declared type or the read raises — `"1200"` is refused, never coerced.

| Key | Missing ⇒ |
|---|---|
| `cost_usd` | the node bills nothing: its tokens are priced at our rate table, filed as `rate_priced_usd` — counted by every ceiling, never called spent |
| `model` | spend buckets by the provider slug instead |
| `served_by` | host attribution unavailable. **No in-repo connector writes it** — it is the remote backend's to answer |
| `finish_reason` | a truncation cannot be told from an empty response; every empty terminal grades as fatal `empty_response` |
| `reasoning` | the thinking share is unreadable, so a slow node reads as a slow provider |
| `cache_read` | the prefix-cache reading is `unreported` — rendered `c?`, distinct from `c0%` (`domain/spend.py::prefix_reading`) |
| `unreported_attempts` | a resend that left and reported no usage bills nothing; each counted one is held at one attempt's bound (`scoring/sample_measurement.py::_unreported_attempts`) |

### What a backend owes a campaign under a spend ceiling — required, not optional

A ceiling is enforced before a cell is SENT, at the most the cell may cost. Without these, a campaign under a ceiling has every cell refused. A backend whose limits are all ones we send derives the bound in its connector (`Connector.sent_spend_bound`).

- **`/pipeline` serves `spend_bound` on every node that can bill** (`domain/pipeline_schema.py::NodeSpendBound`) — `{kind: "llm", attempts, input_bytes, max_tokens}` plus `hosts` where `allow_fallbacks: false`; `{kind: "web", queries, usd_per_query}`. They are COUNTS the backend enforces, never prices: PromptPotter prices them at the dearest host (`infrastructure/llm/pricing.py::rate_ceiling`); `token_bound_of_bytes` is the one bytes→tokens conversion.
- **The backend enforces them**: no request past `input_bytes`, no retry lifting `max_tokens`, no more than `attempts` requests.
- **Every billed attempt is reported** in `step_tokens`, a failed one too (`data.step_tokens` on the error envelope). None reported leaves the cell held at its whole bound. A backend that retries on its own also owes `unreported_attempts`.
- **A 4xx releases its bound** — except a 408 (`infrastructure/llm/spend_book.py::replied`) and a relay's 422 for a reply that failed validation (`infrastructure/backend.py::_ANSWERED_STATUSES`): that model generated.
- **Search spend is reported in dollars**, as `data.web_cost.usd`.

## Connection security

The endpoint and token reach the wire only through the connector (`Connector.endpoint` / `Connector.auth_token`; TermNorm's read `TERMNORM_URL` / `TERMNORM_TOKEN`):

- **Transport** — `https://` verifies the server cert; `http://` is cleartext.
- **Auth** — a set `TERMNORM_TOKEN` sends `Authorization: Bearer …`; empty sends no header. The backend decides whether to require it (`TERMNORM_REQUIRE_AUTH=1`; mismatch → 401). The Linux deploy provisions a shared token and requires it **even on loopback** — defense-in-depth against a co-located compromised process. One token per install.
- **Resilience** — a 429 is the run's backpressure (`infrastructure/llm/send_pacing.py::Backpressure`, one shared cooldown); a 5xx or a connection broken before any reply backs off 1→2→4→8 s unless the body says `detail.retryable: false`, which halts the cell; a connection never made waits for `GET /status` up to `infrastructure/backend.py::_OUTAGE_WAIT_S`, so a backend restart costs nothing; a cell open past the smaller of `cell_envelope_s` and the launch's wall clock left is cut `HALTED`; **a read timeout is never resent**, because the backend is still working — and billing — the first request.

## Self-healing a node

A node emits `diagnostics.warnings[]`; PromptPotter synthesizes a `RuntimeFailure` on the candidate (`application/scoring/arm_states.py::read_breakage`), which the next round's generation is shown. The trip rate is `optimization.degradation_threshold` (0 disables). Who heals which wound: [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).

## Web-search strategy — a swept axis with a cost signal

TermNorm's `web_search` exposes `strategy` (`snippets` / `scrape` / `hybrid`) as an axis. All three issue exactly **one** metered query per match, so sweeping it holds search cost fixed and varies evidence depth, latency and LLM tokens; read the winner off accuracy against `web_cost` (`usd` is what PromptPotter bills). Contract: `TermNorm-excel/backend-api/docs/WEB_SEARCH_STRATEGY.md`.

## Debugging the highway

- **Diagnose from the code path, not by restarting.** A backend whose `/status` times out almost always has a **blocking call in an `async def` request path** — one sync call freezes the single uvicorn worker. Grep the handler for sync I/O first; offload via `asyncio.to_thread`. **No sync I/O on the event loop** is a standing check.
- **The highway IS a cross-repo contract — change one side, fix both.** TermNorm's error envelope is `{status, message, code}`, not FastAPI's `{detail}`; session self-heal keys on `code: "no_session"` and resend policy on `detail.retryable` — codes, never substring guessing — both read by `connectors/termnorm.py::TermNormSession`, never by the agnostic `BackendClient`. When you touch a response field, grep the other repo for its consumer.
- **`--reload` wipes TermNorm's in-memory sessions on every backend edit**; PP self-heals (re-`POST /sessions` + retry), and must keep doing so.
- **Provider latency is the recurring root, and the guards are bounds.** Bound optimizer reasoning and keep request timeouts under `Connector.cell_envelope_s`; an unbounded optimizer node blows `OPTIMIZER_CALL_DEADLINE_S` before round 1. Which provider a campaign runs on is the operator's knob — don't flip it unprompted.
