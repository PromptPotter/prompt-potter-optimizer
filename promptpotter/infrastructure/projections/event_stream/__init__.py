"""Cross-process SSE projection — tail the on-disk cycle ledger.

The outbound event stream is served by *tailing* the cycle's
``.runtime/ledger.jsonl`` (the append-only source of truth), so it works in any
process — the API server, the CLI, or a spawned runner. :class:`CycleLedgerTail`
turns the file into a sequence of ``ProjectionEnvelope`` frames (a leading
``stream_snapshot`` from ``dashboard.json`` + the live tail). The same primitive
backs the SSE route today and a future MCP "watch this run" resource.

The certified Profile-A contract (snapshot-then-tail semantics, heartbeat
cadence, sequence-gap detection) is documented in
``docs/developer/event-stream.md``.
"""

from promptpotter.infrastructure.projections.event_stream.tail import CycleLedgerTail

__all__ = ["CycleLedgerTail"]
