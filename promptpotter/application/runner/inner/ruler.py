"""A cell fitting its own δ scale derives it from the arms under test, so it is fit here, once."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.difficulty import calibrate_delta_ruler
from promptpotter.application.datasets.authored import dataset_scorer
from promptpotter.application.intelligence.exploration import extend_ruler
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.runner.inner.spawn_context import (
    inner_spawn_context,
    set_inner_rulers,
)
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
    """Called where the prior round's cells are all banked: run init and each outer round boundary."""
    ctx = inner_spawn_context()
    if ctx is None or ctx.cells is None or not session.state.cycle_id:
        return
    rulers = {
        name: ruler
        for name in sorted(ctx.cells.by_dataset)
        if (ruler := _fit_or_extend(session, campaign_config, name, round_num)) is not None
    }
    set_inner_rulers(replace(ctx, rulers=rulers))


def _fit_or_extend(
    session: Session, campaign_config: CampaignConfig, dataset_name: str, round_num: int
) -> DeltaRuler | None:
    """``None`` while the bank is too thin to identify a scale; the next boundary re-attempts."""

    # The INNER dataset's own scorer: the outer formula names measurands these rows lack.
    obs = build_archive_observations(
        session.store,
        dataset_name=dataset_name,
        scorer=dataset_scorer(readable_dataset_dir(session.store, dataset_name)),
        # An inner cell holds nothing out (`tasks.py::inner_instrument_config`).
        sample_ids=None,
    )
    if not obs:
        return None
    held = session.store.campaigns.read_ruler(session.hop, dataset_name=dataset_name)
    if held is None:
        ruler, _ = calibrate_delta_ruler(
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
        ruler = extend_ruler(held, obs, history=[])
        if ruler == held:
            # `RulerRecord` is written WHOLE, so an append carrying no new cell would copy the scale.
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
