"""Process-wide ``(campaign_id, cycle_id) -> EventStreamView`` registry.

The SSE endpoint at ``GET /campaigns/{c}/cycles/{cy}/events:subscribe``
looks up the active cycle's projection through this registry. A cycle's
``EventStreamView`` is registered by ``build_run_observers`` at cycle
start and deregistered by ``drain_all`` at teardown — the same lifecycle
as the ledger itself.

Idle cycles (no live ledger) return ``None``; the SSE endpoint serves
a 404 so clients distinguish "stream broken" from "cycle not running."
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.event_stream.view import EventStreamView

__all__ = ["deregister_event_stream", "get_event_stream", "register_event_stream"]


_LOCK = threading.Lock()
_REGISTRY: dict[tuple[str, str], EventStreamView] = {}


def register_event_stream(campaign_id: str, cycle_id: str, view: EventStreamView) -> None:
    """Make ``view`` discoverable by the SSE handler. Overwrites any prior entry."""
    with _LOCK:
        _REGISTRY[(campaign_id, cycle_id)] = view


def deregister_event_stream(campaign_id: str, cycle_id: str) -> None:
    """Drop the registry entry. Closes all live subscribers as a side effect."""
    with _LOCK:
        view = _REGISTRY.pop((campaign_id, cycle_id), None)
    if view is not None:
        view.close_all_subscribers()


def get_event_stream(campaign_id: str, cycle_id: str) -> EventStreamView | None:
    """Look up the active stream for one cycle; ``None`` when idle."""
    with _LOCK:
        return _REGISTRY.get((campaign_id, cycle_id))
