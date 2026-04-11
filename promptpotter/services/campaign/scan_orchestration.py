"""Scan orchestration — run sensitivity scan, persist, load scan context.

Extracted from ``orchestration.py`` — these functions wire the sensitivity
scanner to campaign state and persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.services.campaign.campaign_setup import SessionEnv
from promptpotter.services.campaign.config import (
    configure_and_apply_pipeline,
)

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.search.scan_results import ScanBrief

logger = logging.getLogger(__name__)

__all__ = [
    "load_scan_brief",
    "run_scan_and_persist",
]


def load_scan_brief(
    session: SessionEnv,
    session_id: str,
    scan_variants: dict,
    baseline_acc: float,
) -> ScanBrief | None:
    """Reconstruct scan context from persisted scan results.

    Shared by CLI and notebook — avoids inlining DataFrame construction
    in each entry point.
    """
    scan_data = session.store.sessions.load_scan_results(
        session.backend_id,
        session_id,
    )
    if not scan_data:
        return None
    import pandas as pd

    from promptpotter.services.search.scan_results import prepare_scan_brief

    scan_df = pd.DataFrame(scan_data["scan_df"])
    axis_profiles = scan_data["axis_profiles"]
    return prepare_scan_brief(scan_df, axis_profiles, scan_variants, baseline_acc)


async def run_scan_and_persist(
    baseline,
    campaign_config: CampaignConfig,
    scan_variants: dict,
    dataset: list,
    *,
    session: SessionEnv,
    scan_sample_size: int = 0,
    experiment_id: str = "",
    session_id: str = "",
    log: Callable[[str], None] = logger.info,
    progress_cb: Callable | None = None,
    on_result: Callable | None = None,
):
    """Decompose scan baseline, run sensitivity scan, persist results.

    Returns ``(scan_baseline_sp, baseline_opt, df, profiles)``.
    """
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.scan_results import (
        decompose_scan_baseline as _decompose_scan_baseline,
    )
    from promptpotter.services.search.sensitivity_scanner import (
        sensitivity_scan as _sensitivity_scan,
    )

    # Configure pipeline (ensures filtered schema is applied with overrides baked in)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log)

    ps = session.pipeline_schema

    # Decompose scan baseline
    llm_client, llm_model = create_llm_client(campaign_config)
    result = await _decompose_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        session=session,
        scan_variants=scan_variants,
        pipeline_schema=ps,
    )
    scan_baseline_sp = result.baseline_jsp
    baseline_opt = result.search_baseline

    # Init backend session
    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)

    # Run scan
    log(f"Running sensitivity scan ({len(scan_variants)} axes) ...")
    scan_kwargs: dict[str, Any] = {
        "sample_size": scan_sample_size,
        "pipeline_schema": ps,
        "experiment_id": experiment_id,
        "scoring_formula": campaign_config.get("scoring"),
    }
    if progress_cb is not None:
        scan_kwargs["progress_cb"] = progress_cb
    if on_result is not None:
        scan_kwargs["on_result"] = on_result

    df, profiles = await _sensitivity_scan(
        scan_baseline_sp,
        scan_variants,
        dataset,
        session,
        baseline_opt=baseline_opt,
        **scan_kwargs,
    )

    if df is None or (hasattr(df, "empty") and df.empty):
        log("Scan returned no results")
        return scan_baseline_sp, baseline_opt, None, []

    log(f"Sensitivity scan complete: {len(df)} variants evaluated")

    # Failure group sensitivity — cross-tabulate scan results with failure groups
    if session.store and session.backend_id:
        from pathlib import Path

        from promptpotter.services.search.failure_group_analysis import (
            failure_group_sensitivity,
        )
        from promptpotter.services.search.search_memory import SearchMemory

        _sm_path = Path(session.store.base_dir) / session.backend_id / "search_memory.json"
        _sm = SearchMemory.load(_sm_path)
        _sm.refresh(session.store, session.backend_id)
        clusters = _sm.failure_clusters()
        if clusters:
            scan_rows = df.to_dict(orient="records")
            fg_result = failure_group_sensitivity(scan_rows, clusters)
            if fg_result.sensitivities:
                _sm.ingest_failure_group_analysis(fg_result)
                _sm.save(_sm_path)
                log(
                    f"Failure group analysis: {len(fg_result.sensitivities)} "
                    f"axis x group correlations ingested into SearchMemory"
                )

    # Persist results
    if session_id and session.store and session.backend_id:
        session.store.sessions.save_scan_results(
            session.backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        log(f"Scan results persisted to session {session_id}")

    return scan_baseline_sp, baseline_opt, df, profiles
