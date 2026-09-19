"""What a task will spawn inner cells UNDER — the contextvar, and the four verbs that move it.

Split from ``spawn.py`` because the two have opposite dependency directions. Publishing a context
is something the ordinary campaign runner does on its way past (``runner/entry``, ``noise_floor``,
``initialization/loop_start``); running an inner campaign is something that reaches back DOWN into
that runner. Together in one module those made ``entry <-> spawn`` mutual, which is what forced
twenty-four function-local imports across this package and ``seed_screen``.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.runner.inner.tasks import inner_tasks_path, load_inner_tasks
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.layout import inner_sandbox_dir

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.runner.inner.tasks import InnerTasks
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
    """``shared_root`` stays the REAL workspace root so every ``layout.py::SHARED_CACHE_DIRS`` tree
    remains tenant-global: sandboxing them re-scored every inner origin, injecting more noise than
    the lift."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext
    shared_root: Path
    spawn_campaign_id: str
    spawn_cycle_id: str
    asking_cycle_id: str
    # The panel this run measures on, resolved ONCE at publish. `None` for a dataset that owns no
    # `inner_tasks.yaml` — which is what makes it not an outer one. Carried rather than re-read
    # because `inner_tasks.yaml` is an EDITABLE file: three readers hitting disk at three moments
    # let an edit mid-run split one run's cells across two panels, the same per-run-state hole
    # `InProcessWorkload` closed for in-process connectors.
    panel: InnerTasks | None = None
    # The δ scale each inner dataset's cells read on, refreshed at every outer round boundary by
    # `ruler.py`. Empty until one can be identified, which is the cold path a cell self-fits.
    rulers: Mapping[str, DeltaRuler] = field(default_factory=dict)


_INNER_SPAWN: contextvars.ContextVar[InnerSpawnContext | None] = contextvars.ContextVar(
    "promptpotter_inner_spawn", default=None
)


def _resolve_outer_panel(campaign_config: CampaignConfig, dataset_dir: Path) -> InnerTasks | None:
    """Read the panel for the whole run, and census-check it on the same read.

    ``None`` where the dataset owns no panel: owning one IS what makes a dataset outer, and no
    name test recognises one. The observation-key half of the contract is now
    ``Connector.required_observation_keys``, verified for every connector at ``init_services``."""
    panel_path = inner_tasks_path(dataset_dir)
    if not panel_path.is_file():
        return None
    panel = load_inner_tasks(panel_path)
    # The panel (`inner_tasks.yaml`) and the round budget (`campaign.yaml::sp_budget_round`) are
    # ONE declaration in two files. A budget BELOW the panel narrows it silently, and under
    # `per_round_resubset` rounds then draw different cells — candidates compared on bases that
    # never matched. `_check_sp_budget_vs_dataset` warns in the other direction only.
    if campaign_config.sp_budget_round != len(panel.tasks):
        raise ValueError(
            f"{dataset_dir.name} declares a {len(panel.tasks)}-cell inner panel "
            f"({panel_path.name}) but budgets sp_budget_round="
            f"{campaign_config.sp_budget_round} per round. The outer panel is a CENSUS, not "
            "a sample: every candidate must run every cell or the comparison is not paired. "
            "Set sp_budget_round to the cell count, or change the panel."
        )
    return panel


def publish_inner_spawn_context(session: Session, campaign_config: CampaignConfig) -> None:
    cycle_id = session.state.cycle_id
    dataset_dir = session.dataset_config_dir
    if not cycle_id or dataset_dir is None or not session.campaign_id:
        return
    # Anchored on ``shared_root`` (the REAL workspace root, invariant across depth), never this
    # store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_campaign_id=session.campaign_id,
            spawn_cycle_id=cycle_id,
            asking_cycle_id=cycle_id,
            panel=_resolve_outer_panel(campaign_config, Path(dataset_dir)),
        )
    )


def inner_spawn_context() -> InnerSpawnContext | None:
    """What this task will spawn inner cells under, or ``None`` outside a campaign that spawns."""
    return _INNER_SPAWN.get()


def set_inner_rulers(ctx: InnerSpawnContext) -> None:
    """Publish a context carrying refreshed δ scales — ``ruler.py``'s half of the round boundary."""
    _INNER_SPAWN.set(ctx)


def retarget_inner_spawn(session: Session) -> None:
    ctx = _INNER_SPAWN.get()
    cycle_id = session.state.cycle_id
    if ctx is None or not cycle_id or ctx.asking_cycle_id == cycle_id:
        return
    _INNER_SPAWN.set(replace(ctx, asking_cycle_id=cycle_id))
    logger.info(
        "inner spawn provenance now names %s; sandbox stays owned by %s",
        cycle_id,
        ctx.spawn_cycle_id,
    )
