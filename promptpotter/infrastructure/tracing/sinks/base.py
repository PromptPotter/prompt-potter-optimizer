"""Base class for tracing sinks — shared event dispatch via a class-level table.

Each sink subclass declares ``_HANDLERS: ClassVar[dict[type[Event], str]]``
mapping event types to method names. The base ``handle()`` looks up by
``type(event)`` and calls the bound method; events absent from the table are
silently ignored (sinks intentionally don't subscribe to every event).
"""

from __future__ import annotations

from typing import ClassVar

from promptpotter.infrastructure.tracing.events import Event


class EventSink:
    """Dispatches ``Event`` instances to ``_on_<event>`` methods via ``_HANDLERS``."""

    _HANDLERS: ClassVar[dict[type[Event], str]] = {}

    @property
    def enabled(self) -> bool:
        return True

    def handle(self, event: Event) -> None:
        if not self.enabled:
            return
        method_name = self._HANDLERS.get(type(event))
        if method_name is not None:
            getattr(self, method_name)(event)

    def flush(self) -> None:
        return
