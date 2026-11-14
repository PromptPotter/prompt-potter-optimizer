from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from promptpotter.domain.record_kinds import RECORDS
from promptpotter.domain.records import CycleRecord
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses

# By name, so a handler's module may import its record for typing alone.
_ARMS = {arm.__name__: arm for arm in RECORDS.registered().values()}
_HANDLER = "_handle_"


class Projection:
    """A `LedgerFold` that writes: its `records` are the arms its `_handle_*(record: Arm)` methods annotate."""

    #: The ``Cut`` folded to, stamped by its feeder. ``-1`` = nothing folded yet.
    at_offset: int = -1
    records: ClassVar[RecordClasses] = ()
    _routes: ClassVar[dict[type[CycleRecord], Callable[[Any, Any], None]]] = {}

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        routes = dict(cls._routes)
        for name, handler in vars(cls).items():
            if not name.startswith(_HANDLER):
                continue
            named = handler.__annotations__["record"]
            if named not in _ARMS:
                raise RuntimeError(f"{cls.__name__}.{name} handles {named}, no CycleRecord arm")
            routes[_ARMS[named]] = handler
        cls._routes = routes
        cls.records = tuple(routes)
        LedgerIndex.readers(cls)

    def feed(self, record: CycleRecord) -> None:
        handler = self._routes.get(type(record))
        if handler is not None:
            handler(self, record)

    def drain(self) -> None:
        """Settle buffered state on teardown; a no-op where every event already flushes."""
