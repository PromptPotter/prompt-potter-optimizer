# Event Stream — Profile A certified contract

The outbound half of the M12 Control-remote highway: how a client subscribes to a cycle's live ledger over Server-Sent Events, what frames it gets and in what order, and the guarantees the runtime makes about ordering, gap detection, and idle-keepalive.

Permanent contract: [`docs/adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md). Wire schema: [`docs/specs/events-asyncapi.yaml`](../specs/events-asyncapi.yaml). Codepath: [`promptpotter/infrastructure/projections/event_stream.py`](../../promptpotter/infrastructure/projections/event_stream.py) (`CycleLedgerTail`, tails the on-disk ledger) → `application/cycle_reads.py::cycle_event_frames` (the snapshot, then the tail) → [`promptpotter/presentation/api/routers/campaigns/events.py`](../../promptpotter/presentation/api/routers/campaigns/events.py) (`stream_cycle_events`).

## URL

```
GET /api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe
```

The `:subscribe` suffix follows the AsyncAPI / Google AIP-136 convention for non-CRUD actions. Path resolution is `(campaign_id, cycle_id)`; tenant scope rides `IdentityContext` ambient.

Response: `text/event-stream` (set by `EventSourceResponse`). The handler adds `X-Accel-Buffering: no` to defeat proxy buffering (nginx, Cloudflare, etc. buffer otherwise); `Cache-Control: no-store` is forced on every `/api/v1/*` response by `main.py::SecurityHeadersMiddleware`.

404 only when the cycle directory doesn't exist (unknown campaign/cycle). The stream tails the on-disk ledger **cross-process**, so a running, paused, or finished cycle all subscribe successfully — a finished cycle replays its snapshot then idles on heartbeats.

## Frame shape — `ProjectionEnvelope`

Every non-heartbeat frame is one of these:

```json
data: {"kind": "phase", "cycle_id": "cycle_abc123",
       "sequence": 42, "payload": {...}}
```

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Closed enum covering the **whole** `CycleRecord` union — `domain/projection_envelope.py::ProjectionKind`, which raises at import on drift in either direction — plus the projection-only `stream_snapshot` synthesized by the tail. Coverage is not optional: see § Sequence semantics. |
| `cycle_id` | string | Target cycle. Redundant with the URL path, stamped per-frame so multi-cycle clients can demultiplex a fan-in subscription. |
| `sequence` | integer | Ledger offset. Snapshot frame carries the high-water mark the snapshot reflects; live tail strictly greater. Gap = missed frames. |
| `payload` | object | Per-kind body. For record-derived kinds, the record's `model_dump` content; for `stream_snapshot`, the cycle's served dashboard. |
| `activity` | object | The run's current state as of this frame: `status`, the one line saying what it is doing, `notices`, what was said that still holds, and `decision`, the choice it is held on — worded, with each answer as the command to send. Whole on every frame — see § Current state. |

Adding a new kind requires updating [`events-asyncapi.yaml`](../specs/events-asyncapi.yaml) **first** (closed-set policy — security box 1), then `ProjectionKind` in [`promptpotter/domain/projection_envelope.py`](../../promptpotter/domain/projection_envelope.py), then the record class on `CycleRecord` (or `_PROJECTION_ONLY`, for a kind the tail synthesizes rather than reads), then its answer in `domain/activity.py` — a case in `ActivityFeed._item` for the line it reads as, or a place in `SILENT_RECORDS` if no line is ever made of it, which is what `/ray` drops on. The record class raises at import if skipped and the answer fails the type check; the YAML enum is synced by hand (§ Testing).

## Subscription contract — snapshot-then-tail

The runtime guarantees, in order:

1. **Snapshot frame first.** The first message is a `stream_snapshot` envelope whose `payload` is the subscribed cycle's served dashboard — the body the dashboard route returns (`application/served_dashboard.py`). The envelope's `sequence` is the ledger offset the tail picks up at, and nothing in the payload repeats it.

   For a cycle still at check-in — no launch has declared what it runs with — the payload is the warming shape (`"warming_up": true`) and the client renders a "campaign initialising" placeholder.

2. **Live tail.** Every subsequent `CycleRecord` appended to the ledger is broadcast as one envelope. `sequence` matches the record's ledger offset; envelopes from the snapshot's `sequence` onward arrive in append order.

3. **Heartbeat.** Every 15 s the server emits an SSE **comment** line (`EventSourceResponse`'s ping — currently `: ping - <timestamp>`). The exact text is not consumed: browsers' `EventSource` and proxies key on its *arrival*, not its content, so they can tell "no events" from "stream broken." Heartbeats do not advance `sequence`.

4. **Idle after teardown.** The stream tails a file, so when the runner finalizes the cycle there's nothing to close — the tail simply stops seeing new lines and the connection idles on heartbeats. The client stays subscribed (and disconnects when the operator navigates away).

## Current state — served, never folded by a client

A subscriber wants to know what the run is doing now, not to replay its log. So the tail reads each record once (`domain/activity.py::ActivityFeed`), folds it, and stamps the result on the frame: every `ActivityKind` declares how long it stays (`LIFETIME` — `status` is replaced by the next status and ends when the runner declares a run phase, `round` lasts until the runner enters another round, `run` until it declares another launch). The snapshot frame's `activity` is that same fold over the ledger up to where the tail picks up — one fold of one file, never a second reading off the dashboard. So any client — the webapp, an MCP tool, another agent's UI — that keeps only the newest frame's `activity` is always current.

## Sequence semantics + gap detection

`sequence` is the per-cycle ledger offset, monotonic and dense for live tail (no holes between consecutive records). A client observing `sequence` jumping from N to N+2 missed offset N+1. Recovery: re-subscribe; the new snapshot covers the gap.

**Density depends on the `kind` enum covering every ledger `record_type`, and that is the whole reason coverage is mandatory.** `CycleLedgerTail.read_new` advances `_line_index` for every line it reads, including one it cannot map — so a record whose kind is missing from the enum is *silently skipped while consuming an offset*, which reaches the client as a gap and drives the reconnect above. With full coverage, `_to_envelope` returns `None` only for a genuinely malformed line — which is a gap worth noticing.

The `Last-Event-ID` header is reserved for a future profile's resume-from-sequence semantics (declared in the AsyncAPI HTTP binding, not yet honored by the handler).

## History lives on the ray, not here

This stream has **no replay**, by construction: `snapshot_frame` parks the tail one past the offset its dashboard is a fold of (`at_offset`) — at end-of-file only for a warming shape, which carries none — so a subscriber starts where its snapshot ends and never receives what came before.

That is correct, and it is correct because history has its own home — `GET /campaigns/{c}/cycles/{cy}/ray`, the **time-ray**: one merged chronology across a course, its forks, and its inner runs, windowed and paged backwards. Its items carry the same reader's line for that record (`activity`) and a `path` the envelope cannot carry (an inner `cycle_id` repeats across sibling sandboxes, so it does not identify a cycle in a family).

**The difference in SHAPE between the two follows from the difference in scope.** This stream hands over one record at a time as it lands, so it hands over the whole thing; a ray window is up to `MAX_RAY_LIMIT` records at once, so it serves the reading and never the record — each record's bulk (an LLM's prompt and response, a sample's query and prediction, a phase's whole view) stays with the surface built for it, every one of which is fetched one round at a time. The reader is a validator input on the ray's ETag for the same reason the drop set is: it decides the body and it moves on deploy rather than on a write.

**Do not add a `since=` parameter to this stream.** A second replay mechanism is exactly what the ray exists to avoid. The two objects have different scopes and that is deliberate: the tail is per-cycle and live, the ray is family-wide and historical. A client joins them on `(path, offset)` — a live frame's `sequence` IS a ray item's `offset`, because both are the physical line index of that cycle's own ledger file.

## Writer / reader split

The **ledger is the writer**: `CycleEventLog.append` serializes every `CycleRecord` to `.runtime/ledger.jsonl` (one JSON object per line; line index = offset). The **SSE stream is a reader**: `CycleLedgerTail` tails that file, mapping each line to a `ProjectionEnvelope` (`kind` = the record's `record_type`, `sequence` = line index) and reading `dashboard.json` for the leading snapshot. No projection synthesizes frames; the on-disk ledger is the single medium.

## Cross-process by construction

Because the stream reads a file, it works from any process that shares the filesystem — the API server, the CLI runner, a spawned subprocess, a future MCP "watch this run" client. There is no in-memory registry and no requirement that the run live in the reader's process. The same medium the `dashboard.json` poll already crosses processes on.

## Efficiency

Reads are incremental: the tail tracks a byte cursor and seeks past everything already streamed, so a long ledger is never re-scanned. The handler polls every 0.5 s and runs each file read via `asyncio.to_thread`, so the event loop never blocks on disk I/O. A trailing partial line (a write mid-flight) is left for the next poll, so a torn read never yields a malformed frame.

## Client obligations

A client applies the leading `stream_snapshot`, then requires each subsequent `sequence` to be exactly
one past the last; any other value is a gap and the recovery is to close and re-subscribe for a fresh
snapshot. Clients **MUST NOT** assume a mutation succeeded before the corresponding `command_ack` frame
arrives — a Profile B contract enforced from this stream. The webapp's implementation is
`webapp/lib/chat/useCycleEvents.ts`; smoke-test with
`curl -N http://localhost:8001/api/v1/campaigns/{cid}/cycles/{cyid}/events:subscribe`.

## Testing

No standing test (the structural/contract suite was cut to the silent-harm core — see
[`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)). The stream fails loud: a broken
tail/snapshot/heartbeat path stops chat activity updating, which is visible in use. Three things are
kept in sync by hand, each drifting loud rather than silent — the `ProjectionKind` Literal against the
AsyncAPI `kind` enum (an unknown kind raises on dispatch), every YAML-required envelope field against
the Python model, and a registered FastAPI route at the declared channel address.
