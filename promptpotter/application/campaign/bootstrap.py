"""Campaign cycle bootstrapping — decide resume-vs-create, derive cycle_id.

This module owns the orchestration that used to live on ``CampaignStore``:
domain imports (``cycle_config_identity``, ``TUNING_KEYS``), the
resume-vs-create decision, and the hot-update-keys merge policy. The store
stays pure I/O — read/write/list/archive primitives — and this module
composes them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["bootstrap_cycle", "resume_or_create"]


def bootstrap_cycle(
    config: Any,
    session: Any,
    baseline_render: str,
    baseline_accuracy: float,
    dataset: list,
    active_steps: list[str],
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[CampaignStore | None, str | None, int]:
    """Open the store and resume/create the cycle in one shot.

    Returns ``(store, cycle_id, resumed_from_round)``.  All-None on
    missing project_root/backend_id or any resume failure.

    ``parent_session_id`` is recorded on a newly created campaign; ignored
    on resume.  When ``resume_from_round_override`` is set, trials for
    rounds > N are archived into ``archived/resumed_at_<ts>/`` and the
    trial index is rebuilt before resume.
    """
    from promptpotter.domain.cycle_identity import TUNING_KEYS, cycle_config_identity
    from promptpotter.infrastructure.store.campaign_store import CampaignStore

    if not (session.project_root and session.backend_id):
        return None, None, 0
    try:
        # session.project_root is the tenant base dir (str(store.base_dir));
        # CampaignStore nests campaigns/ directly under it.
        store = CampaignStore(Path(session.project_root))
        resolved = cycle_id_override or cycle_config_identity(
            config,
            baseline_render,
            dataset,
            active_steps,
            strict=config.optimization.strict_cycle_identity,
        )
        if resume_from_round_override is not None:
            store.rewind_to_round(
                session.backend_id,
                resolved,
                resume_from_round_override,
            )
        resumed_from = resume_or_create(
            store,
            session.backend_id,
            resolved,
            config_snapshot=config.model_dump(mode="json"),
            baseline_accuracy=baseline_accuracy,
            hot_update_keys=TUNING_KEYS if cycle_id_override else frozenset(),
            parent_session_id=parent_session_id,
        )
        return store, resolved, resumed_from
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, None, 0


def resume_or_create(
    store: CampaignStore,
    backend_id: str,
    cycle_id: str,
    *,
    config_snapshot: dict[str, Any],
    baseline_accuracy: float,
    hot_update_keys: frozenset[str] = frozenset(),
    parent_session_id: str = "",
) -> int:
    """Resume an existing cycle or create a new one.

    Returns ``resumed_from_round`` — the number of trial files already on
    disk (0 for a fresh cycle). If the cycle exists and ``hot_update_keys``
    is non-empty, merge those keys from ``config_snapshot`` into the stored
    config before returning. ``parent_session_id`` is stamped only on fresh
    creation.
    """
    existing = store.load(backend_id, cycle_id)
    if existing is not None:
        if hot_update_keys:
            stored_cfg = existing.get("config", {})
            if stored_cfg:
                cfg_updated = False
                for k in hot_update_keys:
                    if stored_cfg.get(k) != config_snapshot.get(k):
                        stored_cfg[k] = config_snapshot.get(k)
                        cfg_updated = True
                if cfg_updated:
                    store.update(backend_id, cycle_id, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", cycle_id)
        resumed_from_round = len(existing.get("trials", []))
        if resumed_from_round:
            logger.debug(
                "Resuming cycle %s — %d prior round(s) on disk",
                cycle_id,
                resumed_from_round,
            )
        return resumed_from_round

    # Fresh campaign — create index.json with metadata + parent link.
    store.create(
        backend_id,
        cycle_id,
        {
            "type": "optimization_loop",
            "config": config_snapshot,
            "baseline_accuracy": baseline_accuracy,
            "parent_session_id": parent_session_id,
        },
    )
    return 0
