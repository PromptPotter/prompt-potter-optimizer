"""The ONE δ scale every inner cell of one outer round reads on.

A cell left to fit its own sees only the arms its evidence epoch leaves visible — which are its
own — so the scale comes out of the treatment under test and a re-measured cell returns a
different θ. Fitting it here instead makes it the same for every arm: cold it anchors, warm it
EXTENDS, so one anchor holds for the whole outer campaign.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.datasets.authored import dataset_cell_scorer
from promptpotter.application.runner.inner.spawn import inner_spawn_context, set_inner_rulers
from promptpotter.application.runner.inner.tasks import inner_tasks_path, load_inner_tasks
from promptpotter.infrastructure.store.dataset_access import readable_dataset_dir

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.ruler import DeltaRuler

logger = logging.getLogger(__name__)

__all__ = ["refresh_inner_rulers"]


def refresh_inner_rulers(
    session: Session, campaign_config: CampaignConfig, *, round_num: int
) -> None:
    """Fit-or-extend the shared scale of every inner dataset this campaign spawns, and publish it.

    At run init and each outer round boundary, where the prior round's cells are all banked. A
    no-op for a campaign that spawns nothing."""
    ctx = inner_spawn_context()
    if ctx is None or not session.state.cycle_id:
        return
    panel_path = inner_tasks_path(ctx.dataset_config_dir)
    if not panel_path.is_file():
        return
    panel = load_inner_tasks(panel_path)
    datasets = {panel.dataset_for(cell) for cell in panel.tasks}
    rulers = {
        name: ruler
        for name in sorted(datasets)
        if (ruler := _fit_or_extend(session, campaign_config, name, round_num)) is not None
    }
    set_inner_rulers(replace(ctx, rulers=rulers))


def _fit_or_extend(
    session: Session, campaign_config: CampaignConfig, dataset_name: str, round_num: int
) -> DeltaRuler | None:
    """This dataset's scale, grown onto everything the archive now carries. ``None`` while the
    bank is too thin to identify one — legitimate, and it re-attempts at the next boundary."""
    from promptpotter.application.intelligence.exploration import extend_ruler
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )
    from promptpotter.application.optimization.cycle import _calibrate_delta_ruler
    from promptpotter.shared.errors import RulerCoverageError

    # No `origin_sp_hash`: the outer origin is not an arm on THIS dataset, and the fit wants every
    # arm equally.
    # The INNER dataset's own scorer, never the outer session's: this scale grades justlogic cells,
    # while the outer formula is over whole inner CAMPAIGNS and names measurands these rows lack.
    scorer, scorer_id = dataset_cell_scorer(readable_dataset_dir(session.store, dataset_name))
    obs = build_archive_observations(
        session.store,
        dataset_name=dataset_name,
        scorer=scorer,
        scorer_id=scorer_id,
    )
    if not obs:
        return None
    held = session.store.campaigns.read_ruler(session.hop, dataset_name=dataset_name)
    if held is None:
        ruler, _ = _calibrate_delta_ruler(
            None,
            campaign_config.optimization.elimination_n_min,
            enable_2pl=campaign_config.optimization.enable_2pl_graduation,
            archive_obs=obs,
        )
        if ruler is None:
            return None
        logger.info(
            "inner δ scale for %s ANCHORED at outer round %d over %d cells / %d arms",
            dataset_name,
            round_num,
            len(ruler.delta),
            len({o.candidate_id for o in obs}),
        )
    else:
        try:
            ruler = extend_ruler(held, obs)
        except RulerCoverageError:
            # A permanent provisional δ is worse than the cell staying off the scale; the inner
            # cycle's own extension reaches it once its round grades exist.
            logger.info(
                "inner δ scale for %s kept at %d cells — the archive's new ones have no arm to "
                "equate through yet",
                dataset_name,
                len(held.delta),
            )
            return held
        if ruler == held:
            # `RulerRecord` is written WHOLE, so an append that carries no new cell is a copy of
            # the scale already on the ledger.
            return held
        logger.info(
            "inner δ scale for %s EXTENDED to %d cells (+%d) at outer round %d",
            dataset_name,
            len(ruler.delta),
            len(ruler.delta) - len(held.delta),
            round_num,
        )
    session.store.campaigns.write_ruler(
        session.hop, ruler, dataset_name=dataset_name, round_num=round_num
    )
    return ruler
