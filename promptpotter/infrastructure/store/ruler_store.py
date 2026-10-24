"""The tenant's ONE δ ruler: each election's rows, folded after it; the line count is the version."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from promptpotter.domain.ruler import RulerFold, RulerOwn, RulerReach, RulerRow, RulerScope
from promptpotter.infrastructure.store.read_model import LineIndex, append_row


class _Folds:
    def __init__(self) -> None:
        self._folds: list[RulerFold | None] = []

    def feed(self, offset: int, line: dict[str, Any]) -> None:
        # An unreadable line still holds its version, or every later version would shift.
        self._folds.extend([None] * (offset - len(self._folds)))
        self._folds.append(RulerFold.model_validate(line))

    def value(self) -> tuple[RulerFold | None, ...]:
        return tuple(self._folds)


class RulerStore:
    """Rooted at the TENANT, never the archive home, which accounts may share."""

    def __init__(self, tenant_dir: Path) -> None:
        self.path = tenant_dir / "ruler.jsonl"

    def _folds(self) -> tuple[RulerFold | None, ...]:
        return LineIndex.of(self.path, (_Folds,)).view(_Folds)

    def version(self) -> int:
        return len(self._folds())

    def rows(self, scope: RulerScope, reach: RulerReach) -> list[RulerRow]:
        """Each individual's LAST answer to a cell within *reach*, so a re-measured parent is one row per cell."""
        last: dict[tuple[str, str], RulerRow] = {}
        for line, fold in enumerate(self._folds()[: reach.version]):
            if fold is None or fold.scope != scope or not reach.holds(line, fold):
                continue
            for row in fold.rows:
                last[(row[0], row[1])] = row
        return list(last.values())

    def fold(
        self, scope: RulerScope, rows: Iterable[RulerRow], *, campaign: str, own: RulerOwn | None
    ) -> None:
        """Only what moves the ruler the folding run reads: a re-closed round or a re-measured
        parent ticks no version."""
        reach = RulerReach(version=self.version(), own=own)
        now = {(a, key): y for a, key, y in self.rows(scope, reach)}
        held = list(
            {(a, key): (a, key, y) for a, key, y in rows if now.get((a, key)) != y}.values()
        )
        if not held:
            return
        append_row(
            self.path,
            RulerFold(scope=scope, rows=held, campaign=campaign).model_dump(mode="json"),
        )
