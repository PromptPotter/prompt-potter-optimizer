"""Cross-panel selection under a node's ceiling — the one place that owns the composed TOTAL.

Panels hand back :class:`Item` s; this decides which of them the node's ceiling can afford, in the
layout's own priority order, and only then turns them into text. Three things are ITS to do and
never a renderer's, because a panel cannot see the budget it is being fitted into:

* **The budget.** A panel that sizes itself is guessing against a number that is not the real one,
  and the sum of such guesses is nobody's. At item granularity there is nothing left to guess.
* **The fence.** Applied around each contiguous untrusted run at emit time, so a selection cannot
  leave one open — structural rather than asserted afterwards, and ten untrusted rows pay for one
  fence rather than ten.
* **The count.** A panel states what it HAS; this states what it SHOWED, being the only one that
  can see the selection.

**It selects; it never cuts.** An item is placed whole or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.bundle import (
    FENCE_CLOSE,
    FENCE_OVERHEAD,
    fence_untrusted,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.dispatch.bundle import Item

SECTION_SEP = "\n\n"

# Reserved for the "showed N of M" line this module appends when a panel is thinned. Held back
# during selection rather than added after it: a line appended past the ceiling would break the one
# guarantee this module exists to make. A panel that ends up whole never spends its reserve, which
# leaves the composition under budget — the safe direction.
COUNT_LINE_ALLOWANCE = 56


@dataclass(frozen=True)
class PanelCoverage:
    """What a panel offered and what the composition could afford. ``produced == 0`` is the panel
    saying it had nothing — a reading, and the one the ledger cannot otherwise tell apart from
    "nobody put it in the layout"."""

    produced: int
    placed: int
    chars: int

    @property
    def dropped(self) -> int:
        return self.produced - self.placed


def _fence_runs(items: list[Item]) -> int:
    """Fences ``_emit`` will open for *items* — one per CONTIGUOUS untrusted run."""
    return sum(
        1 for i, it in enumerate(items) if not it.trusted and (i == 0 or items[i - 1].trusted)
    )


def _emit(items: list[Item], *, produced: int) -> str:
    """Items → text, fencing each contiguous untrusted run once and reporting any shortfall.

    Closing the run as it is emitted is what leaves no path that dangles a tag, and grouping is
    what makes row-granular untrusted evidence affordable — ten rows pay for one open and close."""
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
    if produced - len(items) > 0:
        # A fact about the selection, so the panel cannot state it — it has already returned.
        # Plainly, so the model reads these rows as a sample rather than the whole story.
        parts.append(f"[showed {len(items)} of {produced} — the rest did not fit this prompt]")
    return SECTION_SEP.join(parts)


def select(
    rendered: dict[str, list[Item]],
    order: list[str],
    budget: int,
    *,
    exempt: frozenset[str] = frozenset(),
    mandatory: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, PanelCoverage]]:
    """Round-robin over *order*, taking one item per panel per pass until *budget* is spent.

    Layout order IS the priority among peers. Every panel places its first item before any places
    its second, which is the whole anti-dominance property: size alone cannot crowd the package. A
    panel whose next item will not fit is SKIPPED, not stopped, so a smaller one behind it still
    gets its turn.

    *budget* bounds the DISCRETIONARY panels alone. *mandatory* names the ones the node cannot
    operate without: they are admitted whatever they cost — a first claim on a budget is not the
    same promise as being present — and their only bound is ``char_cap`` at render. They are served
    first but charged SEPARATELY, so the allowance a discretionary panel competes for is the same
    number at every depth rather than whatever the floor happened to leave.

    *exempt* names the panels placed whole or not at all; the caller derives it from
    ``InjectionKind.divisible`` rather than this looking it up, so this stays a pure allocator.
    """
    pools = {name: list(items) for name in order if (items := rendered.get(name))}
    taken: dict[str, list[Item]] = {name: [] for name in pools}
    cursor = dict.fromkeys(pools, 0)
    reserved: set[str] = set()
    spent = 0

    def cost(name: str, chunk: list[Item]) -> int:
        """What placing *chunk* adds. Both surcharges price what ``_emit`` will write: the fence
        per contiguous untrusted RUN, the count-line reserve on the panel's first placement."""
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
                cursor[name] = len(whole)  # whole or nothing; never half a panel carrying state

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
                # A panel's opening item is its header, and a header alone teaches nothing — it
                # promises rows that the budget then refused. So a panel's FIRST turn buys its
                # header and its first row together, or buys neither.
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

    # EVERY name in the layout gets an entry, silent ones as "". The caller indexes this by
    # placeholder, and a panel missing from it is a KeyError rather than an empty slot.
    out = {name: _emit(taken.get(name, []), produced=len(pools.get(name, []))) for name in order}
    # Silence is a READING, reported at zero — a panel absent from the breakdown is the one
    # reading nobody can act on.
    coverage = {
        name: PanelCoverage(
            produced=len(pools.get(name, [])),
            placed=len(taken.get(name, [])),
            chars=len(out[name]),
        )
        for name in order
    }
    return out, coverage
