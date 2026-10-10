"""Apart from ``spawn.py``: the runner publishes this on its way past, while a spawn reaches back DOWN into it."""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.optimizer_manifest import bind_inner_optimizer, select_optimizer
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    InnerTasks,
    inner_tasks_path,
    resolve_inner_cells,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.layout import inner_sandbox_dir

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = [
    "InnerSpawnContext",
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
    asking_cycle_id: str
    # Outermost first, ending at the asker's: a pause on any of their ledgers stops the cell.
    enclosing: tuple[Path, ...]
    # Resolved ONCE at publish: a per-cell read lets an edit split one run's cells.
    cells: InnerCells | None = None
    # Empty until a scale can be identified, the cold path where a cell self-fits.
    rulers: Mapping[str, DeltaRuler] = field(default_factory=dict)


_INNER_SPAWN: contextvars.ContextVar[InnerSpawnContext | None] = contextvars.ContextVar(
    "promptpotter_inner_spawn", default=None
)


def _resolve_outer_panel(
    session: Session, campaign_config: CampaignConfig, dataset_dir: Path
) -> InnerTasks | None:
    """``None`` where the dataset owns no panel: owning one IS what makes a dataset outer."""
    panel_path = inner_tasks_path(dataset_dir)
    if not panel_path.is_file():
        return None
    # The workload's panel, never a second read of the file the samples and fingerprint came from.
    panel = InnerTasks.model_validate(session.backend_client.workload.experiment)
    # A draw BELOW the panel narrows it silently, and a resubsetting sampler moves the cells per round.
    selected = select_optimizer(campaign_config.optimization)
    if (drawn := selected.round_cells(len(panel.tasks))) != len(panel.tasks):
        raise ValueError(
            f"{dataset_dir.name} declares a {len(panel.tasks)}-cell inner panel "
            f"({panel_path.name}) but its optimizer's sampler `{selected.sampler.name}` draws "
            f"{drawn} per round. The outer panel is a CENSUS, not a sample: every candidate "
            "must run every cell or the comparison is not paired. Size the sampler to the "
            "cell count, or change the panel."
        )
    return panel


def publish_inner_spawn_context(session: Session, campaign_config: CampaignConfig) -> None:
    cycle_id = session.state.cycle_id
    dataset_dir = session.dataset_config_dir
    if not cycle_id or dataset_dir is None or not session.campaign_id:
        return
    # Never this store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    panel = _resolve_outer_panel(session, campaign_config, Path(dataset_dir))
    cells = None if panel is None else resolve_inner_cells(session.store, panel)
    bind_inner_optimizer(None if cells is None else cells.optimizer)
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_campaign_id=session.campaign_id,
            spawn_cycle_id=cycle_id,
            asking_cycle_id=cycle_id,
            enclosing=(*session.control.enclosing, session.store.campaigns.cycle_dir(session.hop)),
            cells=cells,
        )
    )


def inner_spawn_context() -> InnerSpawnContext | None:
    return _INNER_SPAWN.get()


def set_inner_rulers(ctx: InnerSpawnContext) -> None:
    _INNER_SPAWN.set(ctx)


def retarget_inner_spawn(session: Session) -> None:
    """Moves only the asker; the sandbox owner never follows a fork."""
    ctx = _INNER_SPAWN.get()
    cycle_id = session.state.cycle_id
    if ctx is None or not cycle_id or ctx.asking_cycle_id == cycle_id:
        return
    _INNER_SPAWN.set(
        replace(
            ctx,
            asking_cycle_id=cycle_id,
            enclosing=(*ctx.enclosing[:-1], session.store.campaigns.cycle_dir(session.hop)),
        )
    )
    logger.info(
        "inner spawn provenance now names %s; sandbox stays owned by %s",
        cycle_id,
        ctx.spawn_cycle_id,
    )
