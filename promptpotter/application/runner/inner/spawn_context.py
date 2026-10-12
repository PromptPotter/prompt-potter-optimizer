"""Apart from ``spawn.py``: the runner publishes this on its way past, while a spawn reaches back DOWN into it."""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    InnerTasks,
    inner_tasks_path,
    resolve_inner_cells,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.layout import inner_sandbox_dir
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.shared.identity import IdentityContext

__all__ = [
    "InnerSpawnContext",
    "inner_optimizer",
    "inner_spawn_context",
    "publish_inner_spawn_context",
    "retarget_inner_spawn",
    "set_inner_rulers",
]


@dataclass(frozen=True)
class InnerSpawnContext:
    """``shared_root`` stays the REAL workspace root: sandboxing the shared caches re-scores every inner origin."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext
    shared_root: Path
    spawn_campaign_id: str
    spawn_cycle_id: str
    # Outermost first, ending at the asker's: a pause on any of their ledgers stops the cell.
    enclosing: tuple[Path, ...]
    # Resolved ONCE at publish: a per-cell read lets an edit split one run's cells.
    cells: InnerCells | None = None
    # Empty until a scale can be identified, the cold path where a cell self-fits.
    rulers: Mapping[str, DeltaRuler] = field(default_factory=dict)


# No default: unbound is "nobody published"; a published ``None`` is a run with no cycle to spawn under.
_INNER_SPAWN: Annotated[
    contextvars.ContextVar[InnerSpawnContext | None], shapes_optimizer_prompt
] = contextvars.ContextVar("promptpotter_inner_spawn")


def _resolve_outer_panel(session: Session, dataset_dir: Path) -> InnerTasks | None:
    """``None`` where the dataset owns no panel: owning one IS what makes a dataset outer."""
    if not inner_tasks_path(dataset_dir).is_file():
        return None
    # The workload's panel, never a second read of the file the samples and fingerprint came from.
    return InnerTasks.model_validate(session.backend_client.workload.experiment)


def publish_inner_spawn_context(session: Session) -> None:
    cycle_id = session.state.cycle_id
    dataset_dir = session.dataset_config_dir
    if not cycle_id or dataset_dir is None or not session.campaign_id:
        _INNER_SPAWN.set(None)
        return
    # Never this store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    panel = _resolve_outer_panel(session, Path(dataset_dir))
    cells = None if panel is None else resolve_inner_cells(session.store, panel)
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_campaign_id=session.campaign_id,
            spawn_cycle_id=cycle_id,
            enclosing=(*session.control.enclosing, session.store.campaigns.cycle_dir(session.hop)),
            cells=cells,
        )
    )


@shapes_optimizer_prompt
def inner_spawn_context() -> InnerSpawnContext | None:
    try:
        return _INNER_SPAWN.get()
    except LookupError:
        raise RuntimeError(
            "no inner-spawn context is published, so whether this run is an L4 outer is unknown: "
            "the entry driving it calls `publish_inner_spawn_context` first"
        ) from None


@shapes_optimizer_prompt
def inner_optimizer() -> SelectedOptimizer | None:
    """The manifest an L4 outer's inner cells select, never the outer's own; ``None`` off the recursion."""
    ctx = inner_spawn_context()
    return None if ctx is None or ctx.cells is None else ctx.cells.optimizer


def set_inner_rulers(ctx: InnerSpawnContext) -> None:
    _INNER_SPAWN.set(ctx)


def retarget_inner_spawn(session: Session) -> None:
    """Moves only the asker; the sandbox owner never follows a fork."""
    ctx = inner_spawn_context()
    cycle_id = session.state.cycle_id
    if ctx is None or not cycle_id:
        return
    asking_dir = session.store.campaigns.cycle_dir(session.hop)
    if ctx.enclosing[-1] == asking_dir:
        return
    _INNER_SPAWN.set(replace(ctx, enclosing=(*ctx.enclosing[:-1], asking_dir)))
