# Event Stream — Profile A certified contract

The outbound half of the M12 Control-remote highway: how a client subscribes to a cycle's live ledger over Server-Sent Events, what frames it gets and in what order, and the guarantees the runtime makes about ordering, gap detection, and idle-keepalive.

Permanent contract: [`docs/adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md). Wire schema: [`docs/specs/events-asyncapi.yaml`](../specs/events-asyncapi.yaml). Codepath: [`promptpotter/infrastructure/projections/event_stream.py`](../../promptpotter/infrastructure/projections/event_stream.py) (`CycleLedgerTail`, tails the on-disk ledger) → [`promptpotter/presentation/api/routers/campaigns/events.py`](../../promptpotter/presentation/api/routers/campaigns/events.py) (`stream_cycle_events`).

## URL

```
GET /api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe
```

The `:subscribe` suffix follows the AsyncAPI / Google AIP-136 convention for non-CRUD actions. Path resolution is `(campaign_id, cycle_id)`; tenant scope rides `IdentityContext` ambient.

Response: `text/event-stream` (set by `EventSourceResponse`). The handler adds `X-Accel-Buffering: no` to defeat proxy buffering (nginx, Cloudflare, etc. buffer otherwise); `Cache-Control: no-store` is forced on every `/api/v1/*` response by the `no_store_on_api` middleware.

404 only when the cycle directory doesn't exist (unknown campaign/cycle). The stream tails the on-disk ledger **cross-process**, so a running, paused, or finished cycle all subscribe successfully — a finished cycle replays its snapshot then idles on heartbeats.

## Frame shape — `ProjectionEnvelope`

Every non-heartbeat frame is one of these:

```json
data: {"kind": "phase", "version": 1, "cycle_id": "cycle_abc123",
       "sequence": 42, "payload": {...}}
```

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Closed enum covering the **whole** `CycleRecord` union — `domain/projection_envelope.py::ProjectionKind`, which raises at import on drift in either direction — plus the projection-only `stream_snapshot` synthesized by the tail. Coverage is not optional: see § Sequence semantics. |
| `version` | integer | Envelope shape version. Bumps only on a breaking restructure (which requires a §0 amendment). Profile A ships v1. |
| `cycle_id` | string | Target cycle. Redundant with the URL path, stamped per-frame so multi-cycle clients can demultiplex a fan-in subscription. |
| `sequence` | integer | Ledger offset. Snapshot frame carries the high-water mark the snapshot reflects; live tail strictly greater. Gap = missed frames. |
| `payload` | object | Per-kind body. For record-derived kinds, the record's `model_dump` content; for `stream_snapshot`, `dashboard.json` + `snapshot_at_offset`. |

Adding a new kind requires updating [`events-asyncapi.yaml`](../specs/events-asyncapi.yaml) **first** (closed-set policy — security box 1), then `ProjectionKind` in [`promptpotter/domain/projection_envelope.py`](../../promptpotter/domain/projection_envelope.py), then the record class on `CycleRecord` (or `_PROJECTION_ONLY`, for a kind the tail synthesizes rather than reads), then its `RENDERS_AS_ACTIVITY` answer beside it — can a feed item ever be made of this? — which is what `/ray` drops on. The last two raise at import if skipped. Keep the YAML enum and the Python Literal in sync by hand — drift fails loud (an unknown kind raises on dispatch); no standing test (see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## Subscription contract — snapshot-then-tail

The runtime guarantees, in order:

1. **Snapshot frame first.** The first message is a `stream_snapshot` envelope whose `payload` is the subscribed cycle's current `dashboard.json` content plus `snapshot_at_offset` — the ledger offset the snapshot reflects. The envelope's `sequence` equals `snapshot_at_offset`.

   When `dashboard.json` doesn't exist yet (fresh campaign before origin's first flush), the payload is `{"warming_up": true, "snapshot_at_offset": N}` and the client renders a "campaign initialising" placeholder.

2. **Live tail.** Every subsequent `CycleRecord` appended to the ledger is broadcast as one envelope. `sequence` matches the record's ledger offset; envelopes for `offset > snapshot_at_offset` arrive in append order.

3. **Heartbeat.** Every 15 s the server emits an SSE **comment** line (`EventSourceResponse`'s ping — currently `: ping - <timestamp>`). The exact text is not consumed: browsers' `EventSource` and proxies key on its *arrival*, not its content, so they can tell "no events" from "stream broken." Heartbeats do not advance `sequence`.

4. **Idle after teardown.** The stream tails a file, so when the runner finalizes the cycle there's nothing to close — the tail simply stops seeing new lines and the connection idles on heartbeats. The client stays subscribed (and disconnects when the operator navigates away).

## Sequence semantics + gap detection

`sequence` is the per-cycle ledger offset, monotonic and dense for live tail (no holes between consecutive records). A client observing `sequence` jumping from N to N+2 missed offset N+1. Recovery: re-subscribe; the new snapshot covers the gap.

**Density depends on the `kind` enum covering every ledger `record_type`, and that is the whole reason coverage is mandatory.** `CycleLedgerTail.read_new` advances `_line_index` for every line it reads, including one it cannot map — so a record whose kind is missing from the enum is *silently skipped while consuming an offset*, which reaches the client as a gap and drives the reconnect above. `candidate_minted` and `cycle_seed` sat outside the enum for exactly this reason and produced exactly this: two spurious holes per round. With full coverage, `_to_envelope` returns `None` only for a genuinely malformed line — which is a gap worth noticing.

The `Last-Event-ID` header is reserved for a future profile's resume-from-sequence semantics (declared in the AsyncAPI HTTP binding, not yet honored by the handler).

## History lives on the ray, not here

This stream has **no replay**, by construction: `snapshot_frame` unconditionally calls `_seek_to_eof()`, so a subscriber always starts at the current end of the ledger and never receives what came before.

That is correct, and it is correct because history has its own home — `GET /campaigns/{c}/cycles/{cy}/ray`, the **time-ray**: one merged chronology across a course, its forks, and its inner runs, windowed and paged backwards. This is the "later profile's replay endpoint" named in [`projection_envelope.py`](../../promptpotter/domain/projection_envelope.py); it is now that endpoint. Its items carry a `ProjectionEnvelope`'s `kind` and a subset of the same `payload`, so one client translator serves both, plus a `path` the envelope cannot carry (an inner `cycle_id` repeats across sibling sandboxes, so it does not identify a cycle in a family).

**The subset is the difference in SHAPE between the two, and it follows from the difference in scope.** This stream hands over one record at a time as it lands, so it hands over the whole thing; a ray window is up to `MAX_RAY_LIMIT` records at once, so it serves what a chronology reads — identity, address and the one-line reading — and leaves each record's bulk (an LLM's prompt and response, a sample's query and prediction, a phase's whole view) to the surface built for it, every one of which is fetched one round at a time. The declaration is `projection_envelope.py::RAY_PAYLOAD_FIELDS`, beside the by-kind one, and it is a validator input on the ray's ETag for the same reason the drop set is: it decides the body and it moves on deploy rather than on a write. **Adding a field to the ray means declaring it there** — a field the client reads and the projection omits is not an error anywhere; the step simply renders without it.

**Do not add a `since=` parameter to this stream.** A second replay mechanism is exactly what the ray exists to avoid. The two objects have different scopes and that is deliberate: the tail is per-cycle and live, the ray is family-wide and historical. A client joins them on `(path, offset)` — a live frame's `sequence` IS a ray item's `offset`, because both are the physical line index of that cycle's own ledger file.

## Writer / reader split

The **ledger is the writer**: `CycleEventLog.append` serializes every `CycleRecord` to `.runtime/ledger.jsonl` (one JSON object per line; line index = offset). The **SSE stream is a reader**: `CycleLedgerTail` tails that file, mapping each line to a `ProjectionEnvelope` (`kind` = the record's `record_type`, `sequence` = line index) and reading `dashboard.json` for the leading snapshot. No projection synthesizes frames; the on-disk ledger is the single medium. (This replaces the old in-memory `EventStreamView` fan-out, which only existed in the runner's own process.)

## Cross-process by construction

Because the stream reads a file, it works from any process that shares the filesystem — the API server, the CLI runner, a spawned subprocess, a future MCP "watch this run" client. There is no in-memory registry and no requirement that the run live in the reader's process (the bug that left a webapp chat blank against a CLI-launched run). The same medium the `dashboard.json` poll already crosses processes on.

## Efficiency

Reads are incremental: the tail tracks a byte cursor and seeks past everything already streamed, so a long ledger is never re-scanned. The handler polls every 0.5 s and runs each file read via `asyncio.to_thread`, so the event loop never blocks on disk I/O. A trailing partial line (a write mid-flight) is left for the next poll, so a torn read never yields a malformed frame.

## Client obligations

A client applies the leading `stream_snapshot`, then requires each subsequent `sequence` to be exactly
one past the last; any other value is a gap and the recovery is to close and re-subscribe for a fresh
snapshot. Clients **MUST NOT** assume a mutation succeeded before the corresponding `command_ack` frame
arrives — a Profile B contract enforced from this stream. The webapp's implementation is
`webapp/lib/chat/activity.ts` + `useCycleEvents`; smoke-test with
`curl -N http://localhost:8001/api/v1/campaigns/{cid}/cycles/{cyid}/events:subscribe`.

## Testing

No standing test (the structural/contract suite was cut to the silent-harm core — see
[`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)). The stream fails loud: a broken
tail/snapshot/heartbeat path stops chat activity updating, which is visible in use. Three things are
kept in sync by hand, each drifting loud rather than silent — the `ProjectionKind` Literal against the
AsyncAPI `kind` enum (an unknown kind raises on dispatch), every YAML-required envelope field against
the Python model, and a registered FastAPI route at the declared channel address.
