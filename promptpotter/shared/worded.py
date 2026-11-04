from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from typing import Any


class Worded[K, V](Mapping[K, V]):
    """A closed set's words, in display order, served whole under the name its module declares.

    Building one registers it, and a table is built at module scope or refused here, so each
    registers once, on its module's import. `scripts/build_ts_types.py` imports the app as its boot
    does and exports every table registered by then under the annotated module binding that declares
    it: a table a served module builds is exported, never left out, and one no annotated binding
    declares is refused. A module the app never imports registers nothing, so nothing here claims
    every table anywhere is exported.
    """

    def __init__(self, rows: Mapping[K, V]) -> None:
        builder = sys._getframe(1).f_code
        if builder.co_name != "<module>":
            raise TypeError(
                f"a Worded table is built at module scope, never in {builder.co_qualname} "
                f"({builder.co_filename}:{builder.co_firstlineno})"
            )
        self._rows = dict(rows)
        _REGISTERED.append(self)

    def __getitem__(self, key: K) -> V:
        return self._rows[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


_REGISTERED: list[Worded[Any, Any]] = []


def registered() -> tuple[Worded[Any, Any], ...]:
    """Every table built so far, in build order."""
    return tuple(_REGISTERED)
