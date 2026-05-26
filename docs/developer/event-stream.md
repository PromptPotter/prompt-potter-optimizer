# Event Stream — Profile A certified contract

The outbound half of the M12 Control-remote highway: how a client subscribes to a cycle's live ledger over Server-Sent Events, what frames it gets and in what order, and the guarantees the runtime makes about ordering, gap detection, and idle-keepalive.

Permanent contract: [`docs/adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md). Wire schema: [`docs/specs/m12-events-asyncapi.yaml`](../specs/m12-events-asyncapi.yaml). Codepath: [`promptpotter/infrastructure/projections/event_stream/view.py`](../../promptpotter/infrastructure/projections/event_stream/view.py) (`EventStreamView`) → [`promptpotter/presentation/api/routers/campaigns/events.py`](../../promptpotter/presentation/api/routers/campaigns/events.py) (`stream_cycle_events`).

## URL

```
GET /api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe
```

The `:subscribe` suffix follows the AsyncAPI / Google AIP-136 convention for non-CRUD actions. Path resolution is `(campaign_id, cycle_id)`; tenant scope rides `IdentityContext` ambient.

Response: `text/event-stream`. Headers set `Cache-Control: no-cache` and `X-Accel-Buffering: no` to defeat proxy buffering (nginx, Cloudflare, etc. buffer otherwise).

404 when the cycle isn't actively running — there's no in-process `EventStreamView` registered. Start the run before subscribing.

## Frame shape — `ProjectionEnvelope`

Every non-heartbeat frame is one of these:

```json
data: {"kind": "phase", "version": 1, "cycle_id": "cycle_abc123",
       "sequence": 42, "payload": {...}, "emitted_at": "2026-05-26T12:00:00Z"}
```

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Closed enum. One of seven record-derived kinds (`decision`, `phase`, `snapshot`, `token_usage`, `llm_call_start`, `llm_call_progress`, `llm_call`) or three projection-only kinds (`stream_snapshot`, `command`, `command_ack`). |
| `version` | integer | Envelope shape version. Bumps only on a breaking restructure (which requires a §0 amendment). Profile A ships v1. |
| `cycle_id` | string | Target cycle. Redundant with the URL path, stamped per-frame so multi-cycle clients can demultiplex a fan-in subscription. |
| `sequence` | integer | Ledger offset. Snapshot frame carries the high-water mark the snapshot reflects; live tail strictly greater. Gap = missed frames. |
| `payload` | object | Per-kind body. For record-derived kinds, the record's `model_dump` content; for `stream_snapshot`, `dashboard.json` + `snapshot_at_offset`. |
| `emitted_at` | string | Server wall-clock at envelope mint. Debugging only — never load-bearing for ordering. |

Adding a new kind requires updating [`m12-events-asyncapi.yaml`](../specs/m12-events-asyncapi.yaml) **first** (closed-set policy — security box 1), then `ProjectionKind` in [`promptpotter/domain/projection_envelope.py`](../../promptpotter/domain/projection_envelope.py), then the record class on `CycleRecord` (or the projection-only allowlist in `tests/test_control_plane_drift.py`). Drift between the YAML enum and the Python Literal is a hard test failure.

## Subscription contract — snapshot-then-tail

The runtime guarantees, in order:

1. **Snapshot frame first.** The first message is a `stream_snapshot` envelope whose `payload` is the session's current `dashboard.json` content plus `snapshot_at_offset` — the ledger offset the snapshot reflects. The envelope's `sequence` equals `snapshot_at_offset`.

   When `dashboard.json` doesn't exist yet (fresh campaign before origin's first flush), the payload is `{"warming_up": true, "snapshot_at_offset": N}` and the client renders a "campaign initialising" placeholder.

2. **Live tail.** Every subsequent `CycleRecord` appended to the ledger is broadcast as one envelope. `sequence` matches the record's ledger offset; envelopes for `offset > snapshot_at_offset` arrive in append order.

3. **Heartbeat.** When the queue is idle for 15 s, the server emits an SSE comment line:
   ```
   : keepalive
   ```
   Heartbeats do not advance `sequence`. They exist solely so dumb proxies and clients can tell "no events" from "stream broken."

4. **Drain on cycle teardown.** When the runner finalizes the cycle, every live subscriber is closed cleanly. The handler exits its stream loop; the client sees the connection close and reconnects (which 404s until a new run starts).

## Sequence semantics + gap detection

`sequence` is the per-cycle ledger offset, monotonic and dense for live tail (no holes between consecutive records). A client observing `sequence` jumping from N to N+2 missed offset N+1. Recovery: re-subscribe; the new snapshot covers the gap.

The `Last-Event-ID` header is reserved for a future profile's resume-from-sequence semantics (declared in the AsyncAPI HTTP binding, not yet honored by the handler).

## Sole writer — security box 4

`EventStreamView.on_record` is the only code path that constructs `ProjectionEnvelope` frames and broadcasts them. The other projections (`LiveDashboardView`, `AuditTrailView`, `PoBBStreamView`) write their own on-disk artifacts; only this one emits onto the live HTTP fan-out. The handler at `stream_cycle_events` is a thin async adapter — it reads from subscriber queues and serializes; it doesn't synthesize frames except for the leading `stream_snapshot`.

This is the same "sole writer" rule that `LiveDashboardView._handle_token_usage` enforces for `dashboard.json::spend`.

## Backpressure

Each subscriber holds a bounded asyncio queue (default 1024 frames). On overflow the subscriber is closed; the client reconnects and the new snapshot covers everything it missed. This is the simplest correct behavior — alternatives (back-pressure on the ledger writer, dropping individual frames) violate either the runner's append-must-not-block invariant or the no-gap guarantee.

## Threading model

`CycleEventLog.append` is synchronous and runs on whatever thread (usually an asyncio task) the runner uses. `on_record` calls `subscriber.publish` directly, which uses `loop.call_soon_threadsafe` to hop to the SSE handler's loop. The asyncio queue is therefore always touched from a single loop; producer-side cross-thread coordination lives in `call_soon_threadsafe`.

## Process-wide registry

The handler finds the right `EventStreamView` via [`infrastructure/projections/event_stream/registry.py`](../../promptpotter/infrastructure/projections/event_stream/registry.py): `register_event_stream(campaign_id, cycle_id, view)` at `build_run_observers`, `deregister_event_stream(...)` at `drain_all`. `get_event_stream(...)` returns `None` when the cycle isn't active — the handler 404s.

The registry holds a process-global lock. Cycles can register and deregister concurrently; lookups are read-mostly.

## Testing

One bundled invariant test: [`tests/test_event_stream.py::test_event_stream_view_contract`](../../tests/test_event_stream.py). Exercises subscribe → publish → multi-subscriber fan-out → heartbeat → drain on a real `EventStreamView` (no HTTP).

Drift teeth in [`tests/test_control_plane_drift.py`](../../tests/test_control_plane_drift.py) assert:
- `ProjectionKind` Literal matches the AsyncAPI `kind` enum exactly.
- Every YAML-required envelope field exists in the Python model.
- A FastAPI route is registered at the AsyncAPI-declared channel address.

## Client integration cheatsheet

```bash
# Curl smoke test (replace ids; the cycle must be running):
curl -N http://localhost:8001/api/v1/campaigns/{cid}/cycles/{cyid}/events:subscribe
```

In the webapp (Profile E target):

```ts
const es = new EventSource(`/api/v1/campaigns/${cid}/cycles/${cyid}/events:subscribe`);
let lastSeq = -1;
es.onmessage = (ev) => {
  const frame = JSON.parse(ev.data);
  if (frame.kind === "stream_snapshot") {
    applySnapshot(frame.payload);
    lastSeq = frame.sequence;
    return;
  }
  if (lastSeq >= 0 && frame.sequence !== lastSeq + 1) {
    // Gap — re-subscribe for a fresh snapshot.
    es.close();
    return reconnect();
  }
  applyRecord(frame);
  lastSeq = frame.sequence;
};
```

Clients **MUST NOT** assume a mutation succeeded before the corresponding `command_ack` frame arrives — that's a Profile B contract enforced from this stream.
