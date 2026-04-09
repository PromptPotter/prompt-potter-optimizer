"""Campaign setup, resume detection, and finalization.

Configuration identity hashing, observability initialization,
campaign resume/create logic, and campaign finalization.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.campaign.config import CampaignConfig, RunConfig
from promptpotter.shared.constants import DATASET_NAME
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign.state import LoopState

logger = logging.getLogger(__name__)

__all__ = [
    "TUNING_KEYS",
    "cycle_config_identity",
    "finalize_campaign",
    "init_campaign",
]

# Tuning keys — excluded from cycle identity in experiment mode.
#
# These control *how* the optimizer runs, not *what problem* it solves.
# In experiment mode (default), changing any of these between runs does NOT
# create a new cycle — cached candidates and dataset_run results carry over.
#
# In strict mode (for publication), all params are included in the hash so
# any deviation creates a distinct experiment for reproducibility.
#
# Also used by resume logic to hot-update stored configs on existing cycles.
TUNING_KEYS = frozenset(
    {
        # Loop control — how long/aggressively the loop runs
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        # Optimization strategy — tweakable between runs
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
        "seed",
    }
)


def cycle_config_identity(
    config: RunConfig,
    baseline_rendered: str,
    dataset: list[dict],
    *,
    strict: bool = False,
) -> str:
    """Compute a stable identity hash for a feedback cycle configuration.

    Two-tier system:

    **Experiment mode** (``strict=False``, default):
        Cycle identity is based only on what defines the *problem*:
        active pipeline steps, baseline prompt, and dataset.  Everything
        else — optimizer model, seed, n_variants, creativity, patience,
        thresholds — is excluded (see ``TUNING_KEYS``).  This means you
        can freely tweak optimization strategy, switch between ``--round``
        and ``--auto``, change the optimizer model, or interrupt and resume
        without creating a new cycle.  Cached candidates and dataset_run
        results carry over across invocations.

    **Strict mode** (``strict=True``):
        Every parameter is included in the identity hash.  Changing
        anything — even ``max_rounds`` — creates a new cycle with fresh
        candidates.  Use this only for publication experiments where exact
        reproducibility is required — the same config must always produce the
        same cycle, and any deviation must be flagged as a distinct experiment.
        Enable via ``"strict_cycle_identity": true`` in campaign.json.

    Infrastructure fields (backend_url, project_root, etc.) are always
    excluded in both modes.
    """
    payload_dict: dict[str, Any] = {
        "max_rounds": config.max_rounds,
        "l1_patience": config.l1_patience,
        "n_variants": config.n_variants,
        "creativity": config.creativity,
        "improvement_threshold": config.improvement_threshold,
        "model": config.model,
        "sp_budget_ttest": config.sp_budget_ttest,
        "seed": config.seed,
        "l2_patience": config.l2_patience,
        "l3_patience": config.l3_patience,
        "degradation_threshold": config.degradation_threshold,
        "active_steps": list(config.pipeline_schema.active_steps) if config.pipeline_schema else [],
        "baseline_rendered": baseline_rendered,
        "dataset_pairs": sorted((d.get("query", ""), d.get("ground_truth", "")) for d in dataset),
    }
    if not strict:
        for k in TUNING_KEYS:
            payload_dict.pop(k, None)
    payload = json.dumps(payload_dict, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def resolve_active_campaign_id(
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None,
    baseline_prompt_fields: dict | None,
    dataset: list[dict],
) -> str | None:
    """Compute the cycle ID matching the current config, or None on failure.

    Used by display layer to detect which stored campaign matches the active
    notebook/CLI configuration.
    """
    try:
        config = RunConfig.from_campaign_config(
            campaign_config,
            pipeline_schema=pipeline_schema,
        )
        bl_rendered = ""
        if baseline_prompt_fields:
            bl_rendered = OptSearchPoint.from_prompt_fields(baseline_prompt_fields).render()
        return cycle_config_identity(
            config, bl_rendered, dataset, strict=config.strict_cycle_identity
        )
    except Exception:
        logger.debug("Could not compute active campaign ID", exc_info=True)
        return None


def init_campaign(
    config: RunConfig,
    dataset: list[dict],
    current_ps: dict,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    started_at: str,
    *,
    cycle_id_override: str | None = None,
    langfuse_session_id: str | None = None,
) -> tuple[Any | None, str | None, int, Any | None, str]:
    """Resume/create campaign + init observability in one call.

    Returns:
        (campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id)
    """
    from promptpotter.services.tracing.observability_logger import ObsLogger  # lazy: heavy dep

    # --- Resume detection ---
    campaign_store = None
    cycle_id: str | None = None
    resumed_from_round = 0

    if config.project_root and config.backend_id:
        try:
            from promptpotter.services.stores.campaign_store import CampaignStore

            store_base = Path(config.project_root)
            campaign_store = CampaignStore(store_base)

            if cycle_id_override:
                cycle_id = cycle_id_override
            else:
                bl_ps = baseline_prompt_fields if baseline_prompt_fields is not None else current_ps
                bl_osp = (
                    OptSearchPoint.from_prompt_fields(bl_ps) if isinstance(bl_ps, dict) else bl_ps
                )
                cycle_id = cycle_config_identity(
                    config, bl_osp.render(), dataset, strict=config.strict_cycle_identity
                )
            logger.debug("Cycle identity: %s", cycle_id)

            existing = campaign_store.load(config.backend_id, cycle_id)
            if existing is not None:
                if cycle_id_override:
                    stored_cfg = existing.get("config", {})
                    if stored_cfg:
                        _validate_config_match(config, stored_cfg, cycle_id)
                        current_cfg = config.model_dump(mode="json")
                        cfg_updated = False
                        for k in TUNING_KEYS:
                            if stored_cfg.get(k) != current_cfg.get(k):
                                stored_cfg[k] = current_cfg.get(k)
                                cfg_updated = True
                        if cfg_updated:
                            campaign_store.update(
                                config.backend_id,
                                cycle_id,
                                {"config": stored_cfg},
                            )
                            logger.info("Updated loop-control config for %s", cycle_id)
                resumed_from_round = len(existing.get("trials", []))
                if resumed_from_round:
                    logger.debug(
                        "Resuming cycle %s — %d prior round(s) on disk",
                        cycle_id,
                        resumed_from_round,
                    )
            else:
                campaign_store.create(
                    config.backend_id,
                    cycle_id,
                    {
                        "type": "optimization_loop",
                        "config": config.model_dump(mode="json"),
                        "baseline_accuracy": baseline_accuracy,
                    },
                )
        except ValueError:
            raise
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            campaign_store, cycle_id, resumed_from_round = None, None, 0

    # --- Derive obs campaign ID ---
    if not langfuse_session_id and cycle_id:
        langfuse_session_id = cycle_id
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"

    # --- Observability init ---
    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        with graceful("Failed to create ObsLogger"):
            obs = ObsLogger(config.project_root, config.backend_id)

    if obs:
        with graceful("ObsLogger.log_campaign_start failed"):
            obs.log_campaign_start(
                campaign_id=obs_campaign_id,
                config=config.model_dump(mode="json"),
                baseline_accuracy=baseline_accuracy,
                session_id=langfuse_session_id,
            )
        with graceful("Dataset registration failed"):
            dataset_item_map = obs.register_dataset(DATASET_NAME, dataset)
            if dataset_item_map:
                logger.debug(
                    "Registered %d dataset items for '%s'",
                    len(dataset_item_map),
                    DATASET_NAME,
                )

    return campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id


def _validate_config_match(
    config: RunConfig,
    stored_cfg: dict,
    cycle_id: str,
) -> None:
    """Validate current config matches stored campaign config.

    Raises ValueError with a clear diff if key optimization fields differ.
    This enforces the EXPERIMENT_ID invariant: no silent config drift.
    """
    # Only data-affecting fields — loop-control fields (max_rounds,
    # patience, l2_patience, l3_patience, degradation_threshold) can
    # change freely between runs of the same experiment.
    check_keys = [
        "n_variants",
        "creativity",
        "improvement_threshold",
        "model",
        "sp_budget_ttest",
    ]
    mismatches = []
    for k in check_keys:
        cv = getattr(config, k, None)
        sv = stored_cfg.get(k)
        if cv != sv:
            mismatches.append(f"  {k}: stored={sv} vs current={cv}")
    if mismatches:
        raise ValueError(
            f"Config mismatch for experiment {cycle_id}.\n"
            f"Set EXPERIMENT_ID = None to start a new experiment, "
            f"or update campaign_config to match.\n" + "\n".join(mismatches)
        )


def finalize_campaign(
    campaign_store,
    cycle_id: str | None,
    config: RunConfig,
    state: LoopState,
    stop_reason: str,
    finished_at: str,
    obs,
    obs_campaign_id: str,
    *,
    status: str = "completed",
) -> str | None:
    """Mark campaign on disk and finalize observability.

    Returns:
        Cloud Langfuse trace ID (or None).
    """
    if campaign_store and cycle_id:
        with graceful("Campaign completion update failed"):
            campaign_store.update(
                config.backend_id,
                cycle_id,
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "best_accuracy": state.best_accuracy,
                    "best_round": state.best_round,
                    "n_rounds": len(state.rounds),
                    "finished_at": finished_at,
                },
            )

    cloud_trace_id: str | None = None
    if obs:
        with graceful("ObsLogger campaign end failed"):
            obs.log_campaign_end(
                campaign_id=obs_campaign_id,
                best_accuracy=state.best_accuracy,
                n_rounds=len(state.rounds),
                stop_reason=stop_reason,
                best_round=state.best_round,
            )
            obs.flush()
            cloud_trace_id = obs.get_cloud_trace_id(obs_campaign_id)

    return cloud_trace_id
