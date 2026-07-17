# Event Stream — Profile A certified contract

The outbound half of the M12 Control-remote highway: how a client subscribes to a cycle's live ledger over Server-Sent Events, what frames it gets and in what order, and the guarantees the runtime makes about ordering, gap detection, and idle-keepalive.

Permanent contract: [`docs/adr/0001-m12-control-plane.md`](../adr/0001-m12-control-plane.md). Wire schema: [`docs/specs/m12-events-asyncapi.yaml`](../specs/m12-events-asyncapi.yaml). Codepath: [`promptpotter/infrastructure/projections/event_stream.py`](../../promptpotter/infrastructure/projections/event_stream.py) (`CycleLedgerTail`, tails the on-disk ledger) → [`promptpotter/presentation/api/routers/campaigns/events.py`](../../promptpotter/presentation/api/routers/campaigns/events.py) (`stream_cycle_events`).

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
| `kind` | string | Closed enum. One of seven record-derived kinds (`decision`, `phase`, `snapshot`, `token_usage`, `llm_call_start`, `llm_call_progress`, `llm_call`) or three projection-only kinds (`stream_snapshot`, `command`, `command_ack`). |
| `version` | integer | Envelope shape version. Bumps only on a breaking restructure (which requires a §0 amendment). Profile A ships v1. |
| `cycle_id` | string | Target cycle. Redundant with the URL path, stamped per-frame so multi-cycle clients can demultiplex a fan-in subscription. |
| `sequence` | integer | Ledger offset. Snapshot frame carries the high-water mark the snapshot reflects; live tail strictly greater. Gap = missed frames. |
| `payload` | object | Per-kind body. For record-derived kinds, the record's `model_dump` content; for `stream_snapshot`, `dashboard.json` + `snapshot_at_offset`. |

Adding a new kind requires updating [`m12-events-asyncapi.yaml`](../specs/m12-events-asyncapi.yaml) **first** (closed-set policy — security box 1), then `ProjectionKind` in [`promptpotter/domain/projection_envelope.py`](../../promptpotter/domain/projection_envelope.py), then the record class on `CycleRecord` (or the `_PROJECTION_ONLY_KINDS` allowlist in [`projection_envelope.py`](../../promptpotter/domain/projection_envelope.py)). Keep the YAML enum and the Python Literal in sync by hand — drift fails loud (an unknown kind raises on dispatch); no standing test (see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## Subscription contract — snapshot-then-tail

The runtime guarantees, in order:

1. **Snapshot frame first.** The first message is a `stream_snapshot` envelope whose `payload` is the subscribed cycle's current `dashboard.json` content plus `snapshot_at_offset` — the ledger offset the snapshot reflects. The envelope's `sequence` equals `snapshot_at_offset`.

   When `dashboard.json` doesn't exist yet (fresh campaign before origin's first flush), the payload is `{"warming_up": true, "snapshot_at_offset": N}` and the client renders a "campaign initialising" placeholder.

2. **Live tail.** Every subsequent `CycleRecord` appended to the ledger is broadcast as one envelope. `sequence` matches the record's ledger offset; envelopes for `offset > snapshot_at_offset` arrive in append order.

3. **Heartbeat.** Every 15 s the server emits an SSE **comment** line (`EventSourceResponse`'s ping — currently `: ping - <timestamp>`). The exact text is not consumed: browsers' `EventSource` and proxies key on its *arrival*, not its content, so they can tell "no events" from "stream broken." Heartbeats do not advance `sequence`.

4. **Idle after teardown.** The stream tails a file, so when the runner finalizes the cycle there's nothing to close — the tail simply stops seeing new lines and the connection idles on heartbeats. The client stays subscribed (and disconnects when the operator navigates away).

## Sequence semantics + gap detection

`sequence` is the per-cycle ledger offset, monotonic and dense for live tail (no holes between consecutive records). A client observing `sequence` jumping from N to N+2 missed offset N+1. Recovery: re-subscribe; the new snapshot covers the gap.

The `Last-Event-ID` header is reserved for a future profile's resume-from-sequence semantics (declared in the AsyncAPI HTTP binding, not yet honored by the handler).

## Writer / reader split

The **ledger is the writer**: `CycleEventLog.append` serializes every `CycleRecord` to `.runtime/ledger.jsonl` (one JSON object per line; line index = offset). The **SSE stream is a reader**: `CycleLedgerTail` tails that file, mapping each line to a `ProjectionEnvelope` (`kind` = the record's `record_type`, `sequence` = line index) and reading `dashboard.json` for the leading snapshot. No projection synthesizes frames; the on-disk ledger is the single medium. (This replaces the old in-memory `EventStreamView` fan-out, which only existed in the runner's own process.)

## Cross-process by construction

Because the stream reads a file, it works from any process that shares the filesystem — the API server, the CLI runner, a spawned subprocess, a future MCP "watch this run" client. There is no in-memory registry and no requirement that the run live in the reader's process (the bug that left a webapp chat blank against a CLI-launched run). The same medium the `dashboard.json` poll already crosses processes on.

## Efficiency

Reads are incremental: the tail tracks a byte cursor and seeks past everything already streamed, so a long ledger is never re-scanned. The handler polls every 0.5 s and runs each file read via `asyncio.to_thread`, so the event loop never blocks on disk I/O. A trailing partial line (a write mid-flight) is left for the next poll, so a torn read never yields a malformed frame.

## Testing

No standing test (the structural/contract suite was cut to the silent-harm
core — see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)). The event stream
fails loud: a broken tail/snapshot/heartbeat path stops the chat activity
updating, which is visible in use. Keep these in sync by hand —
each drifts loud, not silent:
- `ProjectionKind` Literal matches the AsyncAPI `kind` enum exactly (unknown kind raises on dispatch).
- Every YAML-required envelope field exists in the Python model.
- A FastAPI route is registered at the AsyncAPI-declared channel address.

## Client integration cheatsheet

```bash
# Curl smoke test (replace ids; the cycle just has to exist):
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
