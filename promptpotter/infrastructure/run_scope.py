"""The run's scope: ONE frozen value on ONE ``ContextVar``, bound where a run opens
(`runner/entry.py::run_optimization`) and where a verb outside one opens its own; a reader takes a
field, never a variable of its own. A field holds what a send or a record reads and no argument
reaches; a value its caller holds (a node's `NodeContext.round_num`) rides the caller."""

from __future__ import annotations

import contextvars
import enum
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.campaign import DeterminismClamp, Purpose
from promptpotter.domain.cycle_paths import CyclePath, Subject
from promptpotter.domain.l4.cells import InnerCells
from promptpotter.domain.spend import TokenUsageKind
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.shared.measurement_context import MeasuredPass

if TYPE_CHECKING:
    from promptpotter.infrastructure.llm.send_pacing import SendBudget
    from promptpotter.infrastructure.llm.spend_book import SpendBook
    from promptpotter.infrastructure.store.archive_queries import MemoryScope
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
    from promptpotter.infrastructure.store.stores import Stores


class Unbound(enum.Enum):
    """Nobody bound the field, which a bound ``None`` is not."""

    UNBOUND = "unbound"


UNBOUND: Literal[Unbound.UNBOUND] = Unbound.UNBOUND


@dataclass(frozen=True)
class InnerSpawnContext:
    # The spawner's, so a cell resolves its dataset where the spawner does and its sandbox is built
    # by the one builder (`stores.py::sandbox_store`), on the REAL shared root.
    stores: Stores
    # The chain the cell's sandbox sits in, ending at its owner: the spawner's cycle.
    inside: CyclePath
    # Outermost first, ending at the asker's: a pause filed on any of them stops the cell.
    enclosing: tuple[Subject, ...]
    # The commands the spawner's passes act for: a `cancel` of one stops the cell.
    acting: tuple[str, ...]
    # What the spawner is FOR: each cell declares it, so withholds what the spawner would.
    purpose: Purpose
    # The ruler version every cell of the spawner's cycle reads, plus its own lines since: the
    # scale the cycle's round 0 was read on (`runner/inner/tasks.py::inner_spawn_of`).
    ruler: int
    # Resolved ONCE at publish: a per-cell read lets an edit split one run's cells.
    cells: InnerCells | None = None


@dataclass(frozen=True)
class InstrumentMode:
    """An L4 inner cell's: ``root_round`` is its root's round, ``enclosed_as`` the spawner's kind,
    which its sends' `RootFiling` names; the cell's own ledger files each call as itself (L4 is one
    level deep, `docs/concepts/l4.md` § Depth)."""

    root_round: int | None
    enclosed_as: TokenUsageKind | None


@dataclass(frozen=True)
class RunScope:
    # The ledger every `append_record` lands on, lent to its cycle's store door; a bound ``None`` is
    # the declared mute of a probe render, every door's.
    ledger: CycleEventLog | None | Literal[Unbound.UNBOUND] = UNBOUND
    # The round a record is measured in: a run binds 0 for its whole span, each round its own inside it.
    round: int | None = None
    # The bucket a send's bill lands in, where a bench pass files it apart from the search's.
    filed_as: TokenUsageKind | None = None
    # The campaign whose cycles' ledgers name the cells (`ReplayFeed.cell_key`) and calls
    # (`call:{reuse key}`) it priced: a re-read of one prices nothing, per CAMPAIGN.
    priced: Path | None = None
    determinism: DeterminismClamp | None = None
    # A grader's replies, apart from the loop's (`infrastructure/store/CLAUDE.md` § Stores).
    judge_cache: LLMReuseCache | None = None
    # What the archive remembers for the run; ``None`` is a scope nobody named, which a reader refuses.
    memory: MemoryScope | None = None
    # An L4 outer's spawn; a bound ``None`` is a run with no cycle to spawn under.
    spawn: InnerSpawnContext | None | Literal[Unbound.UNBOUND] = UNBOUND
    # The sender: whose keys, coupon, charges and rate share a send draws, and the book admitting it.
    account: Stores | None = None
    book: SpendBook | None = None
    # The run's pause flag, polled by every wait a send can sit in.
    abort_check: Callable[[], bool] | None = None
    # Per unit of work (a cell, an optimizer call, a grading): its resends, its stall and its hold.
    send_budget: SendBudget | None = None
    stall_sink: Callable[[float], None] | None = None
    # The open cell's hold on the account, which its sends draw on.
    reservation: str | None = None
    # An L4 inner cell's mode, laid at the cell's entry inside its own task and never undone
    # there, so it covers FINALIZE: a late bill names its root's round too.
    instrument: InstrumentMode | None = None
    # The scoring pass a walk's cells measure, laid at its INGRESS (`scope_context`), or a
    # re-entering asker inherits it stale; ``None`` is an origin pass.
    measured: MeasuredPass | None = None


_EMPTY = RunScope()
_SCOPE: contextvars.ContextVar[RunScope] = contextvars.ContextVar("run_scope")


def run_scope() -> RunScope:
    return _SCOPE.get(_EMPTY)


@contextmanager
def scoped(**fields: Any) -> Iterator[RunScope]:
    """*fields*, `RunScope`'s, laid over the scope standing for the block alone: its token resets
    it on every exit."""
    scope = replace(run_scope(), **fields)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def rescope(**fields: Any) -> None:
    """*fields* laid over the scope standing for the rest of its binding, whose token resets this
    too: a fork's ledger. Refused where no binding stands to bound it."""
    if (scope := _SCOPE.get(None)) is None:
        raise RuntimeError(
            f"rescoping {sorted(fields)} outside every run scope: nothing would reset it, so it "
            "would outlive its run in this task"
        )
    _SCOPE.set(replace(scope, **fields))


def scope_context(**fields: Any) -> contextvars.Context:
    """A copy of this context with *fields* laid over its scope, for a task to run in: one loop
    launches every walk's cells, so a block's binding would cover them all."""
    context = contextvars.copy_context()
    context.run(_SCOPE.set, replace(run_scope(), **fields))
    return context
