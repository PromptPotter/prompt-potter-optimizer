"""The run's scope: ONE frozen value on ONE ``ContextVar``, bound where a run opens
(`runner/entry.py::run_optimization`) and where a verb outside one opens its own; a reader takes a
field, never a variable of its own."""

from __future__ import annotations

import enum
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, TypedDict, Unpack

if TYPE_CHECKING:
    # Annotations alone: each field's type is its owner's, and no layer below reads one it does not own.
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.runner.inner.tasks import InnerSpawnContext
    from promptpotter.domain.campaign import DeterminismClamp
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.archive_queries import MemoryScope
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache


class Unbound(enum.Enum):
    """Nobody bound the field, which a bound ``None`` is not."""

    UNBOUND = "unbound"


UNBOUND: Literal[Unbound.UNBOUND] = Unbound.UNBOUND


@dataclass(frozen=True)
class RunScope:
    # The ledger every `emit_*` lands on; a bound ``None`` is the declared mute of a probe render.
    ledger: CycleEventLog | None | Literal[Unbound.UNBOUND] = UNBOUND
    # The launch's one selection, an L4 cell's edit laid into its overlay at spawn.
    optimizer: SelectedOptimizer | None = None
    determinism: DeterminismClamp | None = None
    # A grader's replies, apart from the loop's (`infrastructure/store/CLAUDE.md` § Stores).
    judge_cache: LLMReuseCache | None = None
    # What the archive remembers for the run; ``None`` is a scope nobody named, which a reader refuses.
    memory: MemoryScope | None = None
    # An L4 outer's spawn; a bound ``None`` is a run with no cycle to spawn under.
    spawn: InnerSpawnContext | None | Literal[Unbound.UNBOUND] = UNBOUND


class ScopeFields(TypedDict, total=False):
    ledger: CycleEventLog | None
    optimizer: SelectedOptimizer | None
    determinism: DeterminismClamp | None
    judge_cache: LLMReuseCache | None
    memory: MemoryScope | None
    spawn: InnerSpawnContext | None


_EMPTY = RunScope()
_SCOPE: ContextVar[RunScope] = ContextVar("run_scope")


def run_scope() -> RunScope:
    return _SCOPE.get(_EMPTY)


@contextmanager
def scoped(**fields: Unpack[ScopeFields]) -> Iterator[RunScope]:
    """*fields* laid over the scope standing, for the block alone: its token resets it on every exit."""
    scope = replace(run_scope(), **fields)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def rescope(**fields: Unpack[ScopeFields]) -> None:
    """*fields* laid over the scope standing for the rest of its binding, whose token resets this
    too: a fork's ledger. Refused where no binding stands to bound it."""
    if (scope := _SCOPE.get(None)) is None:
        raise RuntimeError(
            f"rescoping {sorted(fields)} outside every run scope: nothing would reset it, so it "
            "would outlive its run in this task"
        )
    _SCOPE.set(replace(scope, **fields))
