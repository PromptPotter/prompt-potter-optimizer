"""``EventStreamView`` — sole writer of outbound SSE projection frames.

Subscribes to one cycle's ledger, broadcasts every record as a
``ProjectionEnvelope`` to all live HTTP subscribers. Per-cycle scope;
constructor takes ``CycleDir`` (audit-side, not the session root) so a
fork's stream stays isolated from siblings.

The Profile-A certified contract (snapshot-then-tail semantics, heartbeat
cadence, sequence-gap detection) is documented in
``docs/developer/event-stream.md``.
"""

from promptpotter.infrastructure.projections.event_stream.registry import (
    deregister_event_stream,
    get_event_stream,
    register_event_stream,
)
from promptpotter.infrastructure.projections.event_stream.view import (
    EventStreamSubscriber,
    EventStreamView,
)

__all__ = [
    "EventStreamSubscriber",
    "EventStreamView",
    "deregister_event_stream",
    "get_event_stream",
    "register_event_stream",
]
