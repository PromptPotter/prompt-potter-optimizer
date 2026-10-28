from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, get_args

from promptpotter.domain.run_records import CycleRecord
from promptpotter.infrastructure.store.read_model import RecordClasses, feed_fold

# By name, so a handler's module may import its record for typing alone.
_ARMS: dict[str, type[CycleRecord]] = {
    arm.__name__: arm for arm in get_args(get_args(CycleRecord)[0])
}
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

    def feed(self, record: CycleRecord) -> None:
        handler = self._routes.get(type(record))
        if handler is not None:
            handler(self, record)

    def on_record(self, record: CycleRecord, offset: int, *, ledger: Path) -> None:
        self.at_offset = offset
        feed_fold(self, record, ledger=ledger, offset=offset)

    def drain(self) -> None:
        """Settle buffered state on teardown; a no-op where every event already flushes."""
