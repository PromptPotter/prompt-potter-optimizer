"""A cell fitting its own δ scale derives it from the arms under test, so it is fit here, once."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.difficulty import calibrate_delta_ruler
from promptpotter.application.datasets.authored import dataset_scorer
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.intelligence.rasch import extend_ruler
from promptpotter.application.runner.inner.spawn_context import (
    inner_spawn_context,
    set_inner_rulers,
)
from promptpotter.domain.ruler import RulerScope
from promptpotter.infrastructure.store.dataset_access import resolve_dataset

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.rasch import Observation
    from promptpotter.domain.ruler import DeltaRuler

logger = logging.getLogger(__name__)

__all__ = ["anchor_or_extend", "refresh_inner_rulers"]


def refresh_inner_rulers(
    session: Session, campaign_config: CampaignConfig, *, round_num: int
) -> None:
    """Called where the prior round's cells are all banked: run init and each outer round boundary.
    ONE scale per seat dataset: a dataset joining later anchors its own, never another's."""
    ctx = inner_spawn_context()
    if ctx is None or ctx.cells is None or not session.state.cycle_id:
        return
    optimization = campaign_config.optimization
    rulers: dict[str, DeltaRuler] = {}
    for name in sorted(ctx.cells.by_dataset):
        # The INNER dataset's own scorer: the outer formula names measurands these rows lack.
        scorer = dataset_scorer(resolve_dataset(session.store, name).directory)
        scope = RulerScope(scorer=scorer.id, dataset=name)
        held = session.store.campaigns.read_ruler(session.hop, scope=scope, moment=None)
        # An inner cell holds nothing out (`tasks.py::inner_instrument_config`).
        obs = build_archive_observations(
            session.store, dataset_name=name, scorer=scorer, admitted=None
        )
        ruler = anchor_or_extend(
            held,
            obs,
            n_min=optimization.elimination_n_min,
            enable_2pl=optimization.enable_2pl_graduation,
        )
        if ruler is None:
            continue
        if ruler != held:
            # `RulerRecord` is written WHOLE, so an append carrying no new cell would copy the scale.
            session.store.campaigns.write_ruler(
                session.hop, ruler, scope=scope, round_num=round_num
            )
            logger.info(
                "inner δ scale for %s %s at outer round %d: %d cells",
                name,
                "ANCHORED" if held is None else "EXTENDED",
                round_num,
                len(ruler.delta),
            )
        rulers[name] = ruler
    set_inner_rulers(replace(ctx, rulers=rulers))


def anchor_or_extend(
    held: DeltaRuler | None, obs: Sequence[Observation], *, n_min: int, enable_2pl: bool
) -> DeltaRuler | None:
    """``None`` while the bank is too thin to identify a scale; the next boundary re-attempts."""
    if not obs:
        return held
    if held is None:
        ruler, _ = calibrate_delta_ruler(None, n_min, enable_2pl=enable_2pl, archive_obs=list(obs))
        return ruler
    return extend_ruler(held, list(obs), history=[])
