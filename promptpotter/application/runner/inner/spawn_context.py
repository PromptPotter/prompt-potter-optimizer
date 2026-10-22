"""Apart from ``spawn.py``: the runner publishes this on its way past, while a spawn reaches back DOWN into it."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from promptpotter.application.bench.difficulty import ruler_clock
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    is_self_optimization,
    parse_resolved,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.store.archive_queries import (
    CampaignWalks,
    CellOf,
    bind_memory_scope,
)
from promptpotter.infrastructure.store.layout import inner_sandbox_dir
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import enter_instrument_mode

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign import Purpose
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.shared.identity import IdentityContext


@dataclass(frozen=True)
class InnerSpawnContext:
    """``shared_root`` stays the REAL workspace root: sandboxing the shared caches re-scores every inner origin."""

    inner_sandbox_root: Path
    identity: IdentityContext
    shared_root: Path
    spawn_campaign_id: str
    spawn_cycle_id: str
    # Outermost first, ending at the asker's: a pause on any of their ledgers stops the cell.
    enclosing: tuple[Path, ...]
    # What the spawner is FOR: each cell declares it, so withholds what the spawner would.
    purpose: Purpose
    # The tenant ruler version the spawner's open round reads, which every cell it spawns reads too.
    ruler_version: int
    # Resolved ONCE at publish: a per-cell read lets an edit split one run's cells.
    cells: InnerCells | None = None


# No default: unbound is "nobody published"; a published ``None`` is a run with no cycle to spawn under.
_INNER_SPAWN: Annotated[
    contextvars.ContextVar[InnerSpawnContext | None], shapes_optimizer_prompt
] = contextvars.ContextVar("promptpotter_inner_spawn")


def _outer_cells(session: Session) -> InnerCells | None:
    """``None`` off the recursion. The workload's panel, never a second read of the file the
    samples and fingerprint came from."""
    if not is_self_optimization(session.backend_id):
        return None
    return parse_resolved(session.backend_client.workload.experiment).panel.resolve_cells(
        session.store
    )


def publish_inner_spawn_context(session: Session) -> None:
    cycle_id = session.cycle_id
    if not cycle_id or session.dataset is None or not session.campaign_id:
        _INNER_SPAWN.set(None)
        return
    # Never this store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    cells = _outer_cells(session)
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_campaign_id=session.campaign_id,
            spawn_cycle_id=cycle_id,
            enclosing=(
                *session.state.control.enclosing,
                session.store.campaigns.cycle_dir(session.hop),
            ),
            purpose=session.purpose,
            ruler_version=ruler_clock(session),
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
def inner_cells() -> InnerCells | None:
    """The cells an L4 outer's arms are measured on, each running its own manifest, never the
    outer's own; ``None`` off the recursion."""
    ctx = inner_spawn_context()
    return None if ctx is None else ctx.cells


def pin_inner_ruler(version: int) -> None:
    """At each outer round's open, so every cell the round spawns reads the version it reads."""
    ctx = inner_spawn_context()
    if ctx is not None and ctx.ruler_version != version:
        _INNER_SPAWN.set(replace(ctx, ruler_version=version))


def enter_inner_cell(ctx: InnerSpawnContext, campaigns: CampaignStore, campaign_id: str) -> None:
    """The archive stays the cell's CACHE, and MEMORY only for its campaign's walks."""
    # Before the cell's run binds its own round, so this still reads the spawner's.
    enter_instrument_mode(spawned_in=current_round())
    bind_memory_scope(CellOf(CampaignWalks(campaigns, campaign_id), ctx.ruler_version))


def retarget_inner_spawn(session: Session) -> None:
    """Moves only the asker; the sandbox owner never follows a fork."""
    ctx = inner_spawn_context()
    cycle_id = session.cycle_id
    if ctx is None or not cycle_id:
        return
    asking_dir = session.store.campaigns.cycle_dir(session.hop)
    if ctx.enclosing[-1] == asking_dir:
        return
    _INNER_SPAWN.set(replace(ctx, enclosing=(*ctx.enclosing[:-1], asking_dir)))
