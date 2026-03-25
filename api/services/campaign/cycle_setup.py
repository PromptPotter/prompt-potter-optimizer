"""Campaign setup, resume detection, and shared utilities.

Contains configuration identity hashing, observability initialization,
campaign resume/create logic, and small helpers used across the
feedback cycle, round execution, and escalation modules.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api.models.phase_event import PhaseEvent
from api.models.opt_search_point import OptSearchPoint, PROMPT_STRING_FIELDS
from api.config.settings import DATASET_NAME
from api.services.campaign.models import CycleConfig

if TYPE_CHECKING:
    from api.services.campaign.models import _LoopState

logger = logging.getLogger(__name__)


@contextmanager
def graceful(msg: str):
    """Suppress non-interrupt exceptions with a warning log."""
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.warning(msg, exc_info=True)


def _emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set."""
    if on_phase is None:
        return
    pe = PhaseEvent(phase=phase, event=event, round=round, data=data)
    on_phase(pe)


def _get_obs_trace(
    state: "_LoopState", obs_campaign_id: str,
) -> tuple[Any | None, str | None]:
    """Extract obs logger and trace_id from loop state."""
    obs = state.eval_ctx.obs if state.eval_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    return obs, trace_id


def _candidate_summaries(
    candidates: list[dict],
    current_prompt_fields: dict,
) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, c in enumerate(candidates):
        pp_override = c.get("__pipeline_params_override__")
        changed_fields = [
            f for f in PROMPT_STRING_FIELDS
            if c.get(f, "") != current_prompt_fields.get(f, "")
        ]
        summary: dict = {
            "idx": i,
            "changes_description": c.get("changes_description", ""),
            "changed_fields": changed_fields,
        }
        if pp_override:
            summary["pipeline_params_override"] = pp_override
        summaries.append(summary)
    return summaries


def cycle_config_identity(
    config: CycleConfig,
    baseline_rendered: str,
    eval_data: list[dict],
) -> str:
    """Compute a stable identity hash for a feedback cycle configuration.

    Covers optimization-relevant fields only so the same config produces the
    same cycle_id across kernel restarts.  Infrastructure fields (backend_url,
    project_root, etc.) are excluded.
    """
    payload = json.dumps(
        {
            "max_rounds": config.max_rounds,
            "patience": config.patience,
            "n_variants": config.n_variants,
            "creativity": config.creativity,
            "improvement_threshold": config.improvement_threshold,
            "model": config.model,
            "provider": config.provider,
            "temperature": config.temperature,
            "sample_size": config.sample_size,
            "seed": config.seed,
            "baseline_rendered": baseline_rendered,
            "eval_data_pairs": sorted(
                (d.get("query", ""), d.get("ground_truth", ""))
                for d in eval_data
            ),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def _init_obs(
    config: CycleConfig,
    obs_campaign_id: str,
    baseline_accuracy: float,
    eval_data: list[dict],
    langfuse_session_id: str | None,
) -> tuple[Any | None, str | None, dict[str, str] | None]:
    """Create ObsLogger, log campaign start, register dataset.

    Returns:
        (obs, dataset_name, dataset_item_map)
    """
    from api.services.obs.observability_logger import ObsLogger  # lazy: heavy dep

    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        with graceful("Failed to create ObsLogger"):
            obs = ObsLogger(config.project_root, config.backend_id)

    dataset_name: str | None = None
    dataset_item_map: dict[str, str] | None = None
    if not obs:
        return obs, dataset_name, dataset_item_map

    with graceful("ObsLogger.log_campaign_start failed"):
        obs.log_campaign_start(
            campaign_id=obs_campaign_id,
            config=config.model_dump(mode="json"),
            baseline_accuracy=baseline_accuracy,
            session_id=langfuse_session_id,
        )

    try:
        dataset_name = DATASET_NAME
        dataset_item_map = obs.register_dataset(dataset_name, eval_data)
        if dataset_item_map:
            logger.info(
                "Registered %d dataset items for '%s'",
                len(dataset_item_map), dataset_name,
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.warning("Dataset registration failed", exc_info=True)
        dataset_item_map = None

    return obs, dataset_name, dataset_item_map


def _validate_config_match(
    config: CycleConfig,
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
        "n_variants", "creativity",
        "improvement_threshold", "model", "sample_size",
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
            f"or update campaign_config to match.\n"
            + "\n".join(mismatches)
        )


def _resume_or_create_campaign(
    config: CycleConfig,
    eval_data: list[dict],
    current_ps: dict,
    baseline_prompt_state: dict | None,
    baseline_accuracy: float,
    *,
    cycle_id_override: str | None = None,
) -> tuple[Any | None, str | None, int]:
    """Handle campaign resume detection.

    Args:
        cycle_id_override: Explicit campaign ID (skips hash computation).
            Used when the user sets EXPERIMENT_ID in the notebook.
            When set and campaign exists, validates config matches stored.

    Returns:
        (campaign_store, cycle_id, resumed_from_round)
    """
    if not (config.project_root and config.backend_id):
        return None, None, 0

    try:
        from api.services.stores.campaign_store import CampaignStore  # lazy: heavy dep

        store_base = Path(config.project_root)
        campaign_store = CampaignStore(store_base)

        if cycle_id_override:
            cycle_id = cycle_id_override
        else:
            bl_ps = baseline_prompt_state if baseline_prompt_state is not None else current_ps
            bl_osp = OptSearchPoint.from_prompt_fields(bl_ps) if isinstance(bl_ps, dict) else bl_ps
            cycle_id = cycle_config_identity(config, bl_osp.render(), eval_data)
        logger.info("Cycle identity: %s", cycle_id)

        existing = campaign_store.load(config.backend_id, cycle_id)
        if existing is not None:
            # Validate config matches when resuming with explicit ID
            if cycle_id_override:
                stored_cfg = existing.get("config", {})
                if stored_cfg:
                    _validate_config_match(config, stored_cfg, cycle_id)
                    # Update loop-control fields in stored config
                    _LOOP_CONTROL_KEYS = [
                        "max_rounds", "patience", "l2_patience",
                        "l3_patience", "degradation_threshold",
                    ]
                    current_cfg = config.model_dump(mode="json")
                    cfg_updated = False
                    for k in _LOOP_CONTROL_KEYS:
                        if stored_cfg.get(k) != current_cfg.get(k):
                            stored_cfg[k] = current_cfg.get(k)
                            cfg_updated = True
                    if cfg_updated:
                        campaign_store.update(
                            config.backend_id, cycle_id, {"config": stored_cfg},
                        )
                        logger.info("Updated loop-control config for %s", cycle_id)
            resumed_from = len(existing.get("trials", []))
            if resumed_from:
                logger.info(
                    "Resuming cycle %s — %d prior round(s) on disk",
                    cycle_id, resumed_from,
                )
        else:
            resumed_from = 0
            campaign_store.create(config.backend_id, cycle_id, {
                "type": "feedback_cycle",
                "config": config.model_dump(mode="json"),
                "baseline_accuracy": baseline_accuracy,
            })

        return campaign_store, cycle_id, resumed_from
    except ValueError:
        raise  # config mismatch — propagate to user
    except Exception:
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, None, 0
