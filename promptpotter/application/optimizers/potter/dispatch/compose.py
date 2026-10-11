from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimizers.fence import (
    FENCE_CLOSE,
    FENCE_OVERHEAD,
    fence_untrusted,
)

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.dispatch.bundle import Item

SECTION_SEP = "\n\n"

# Held back DURING selection for the dropped-count line: appended after, it would breach the ceiling.
COUNT_LINE_ALLOWANCE = 56


@dataclass(frozen=True)
class PanelCoverage:
    """``produced == 0`` is the panel saying it had nothing, distinct from its absence from the layout."""

    produced: int
    placed: int
    chars: int

    @property
    def dropped(self) -> int:
        return self.produced - self.placed


def _fence_runs(items: list[Item]) -> int:
    return sum(
        1 for i, it in enumerate(items) if not it.trusted and (i == 0 or items[i - 1].trusted)
    )


def _emit(items: list[Item], *, produced: int) -> str:
    if not items:
        return ""
    parts: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            parts.append(fence_untrusted(SECTION_SEP.join(run)))
            run.clear()

    for item in items:
        if item.trusted:
            flush()
            parts.append(item.text)
        else:
            run.append(item.text)
    flush()
    if (dropped := produced - len(items)) > 0:
        # What was DROPPED, not "N of M": a panel's items include its header.
        parts.append(f"[{dropped} more did not fit this prompt]")
    return SECTION_SEP.join(parts)


def select(
    rendered: dict[str, list[Item]],
    order: list[str],
    budget: int,
    *,
    exempt: frozenset[str] = frozenset(),
    mandatory: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, PanelCoverage]]:
    """*budget* bounds the DISCRETIONARY panels alone: *mandatory* ones are admitted whatever they cost, charged SEPARATELY."""
    pools = {name: list(items) for name in order if (items := rendered.get(name))}
    taken: dict[str, list[Item]] = {name: [] for name in pools}
    cursor = dict.fromkeys(pools, 0)
    reserved: set[str] = set()
    spent = 0

    def cost(name: str, chunk: list[Item]) -> int:
        placed = taken[name]
        extra = sum(len(i.text) + len(SECTION_SEP) for i in chunk)
        opened = _fence_runs(placed + chunk) - _fence_runs(placed)
        extra += opened * (FENCE_OVERHEAD + len(FENCE_CLOSE))
        if name not in reserved and name not in exempt and len(pools[name]) > 1:
            extra += COUNT_LINE_ALLOWANCE
        return extra

    def serve(names: list[str], *, bounded: bool) -> None:
        nonlocal spent
        for name in names:
            if name in pools and name in exempt:
                whole = pools[name]
                if not bounded or spent + cost(name, whole) <= budget:
                    spent += cost(name, whole)
                    taken[name] = list(whole)
                cursor[name] = len(whole)

        placed_any = True
        while placed_any:
            placed_any = False
            for name in names:
                items = pools.get(name)
                if items is None or name in exempt:
                    continue
                at = cursor[name]
                if at >= len(items):
                    continue
                # A panel's FIRST turn buys its header and first row together, or neither.
                chunk = items[at : at + 2] if at == 0 and len(items) > 1 else items[at : at + 1]
                if bounded and spent + cost(name, chunk) > budget:
                    continue
                spent += cost(name, chunk)
                reserved.add(name)
                taken[name].extend(chunk)
                cursor[name] = at + len(chunk)
                placed_any = True

    serve([n for n in order if n in mandatory], bounded=False)
    spent = 0
    serve([n for n in order if n not in mandatory], bounded=True)

    # EVERY name in the layout gets an entry, silent ones as "": the caller indexes by placeholder.
    out = {name: _emit(taken.get(name, []), produced=len(pools.get(name, []))) for name in order}
    coverage = {
        name: PanelCoverage(
            produced=len(pools.get(name, [])),
            placed=len(taken.get(name, [])),
            chars=len(out[name]),
        )
        for name in order
    }
    return out, coverage
