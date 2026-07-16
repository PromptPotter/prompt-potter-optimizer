"""Origin-only measurement driver for the resource matrix.

Scores a dataset's ORIGIN (no optimization) under a per-cell target-model override,
so each (target-model, dataset) cell gets a real, current capability verdict. Reuses
the origin scoring seam (`establish_campaign_origin` → `prepare_scoring_context` →
`score_search_point`) with a `pipeline_overrides.<llm_node>.model` overlay — the same
sanctioned override channel a per-campaign model switch rides. Measurements land in the
shared archive (cached per content-hash, so a re-measure of the same cell is free).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.resource_matrix.matrix import (
    BAND_FLOOR,
    COLLAPSE_EXCESS,
    CellVerdict,
    answer_concentration,
    classify_band,
    constant_answer_floor,
    now_iso,
)
from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig

logger = logging.getLogger("promptpotter.resource_matrix")


def _base_config(session: Session) -> CampaignConfig:
    """Load the dataset's own ``campaign.json`` config (connector profile + file)."""
    from promptpotter.application.config import load_campaign_config
    from promptpotter.application.datasets.authored import read_campaign_config_file

    file_config: dict[str, object] = {}
    if session.dataset_config_dir is not None:
        cfg_path = session.dataset_config_dir / "campaign.json"
        if cfg_path.exists():
            file_config = read_campaign_config_file(cfg_path)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    return load_campaign_config({**profile, **file_config})


async def measure_cells(
    session: Session,
    dataset: str,
    models: list[str],
    provider: str | None,
    n: int,
) -> list[CellVerdict]:
    """Measure origin accuracy for each ``(dataset, model)`` cell; return verdicts.

    One reachable-backend check up front, then per model: overlay the target model
    onto the LLM node, score the origin on ``n`` samples, classify the band. A model
    whose scoring returns no round (unreachable / zero samples) yields an ``error``
    cell so the gap is visible rather than silently dropped.
    """
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.origin import prepare_scoring_context

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return [_error_cell(dataset, m, provider, "backend unreachable") for m in models]

    base_config = _base_config(session)
    node = session.llm_node_name()
    samples = session.samples or []
    out: list[CellVerdict] = []

    for model in models:
        overrides = {k: dict(v) for k, v in base_config.pipeline_overrides.items()}
        node_ov = dict(overrides.get(node, {}))
        node_ov["model"] = model
        if provider:
            node_ov["provider"] = provider
        overrides[node] = node_ov
        cfg = base_config.model_copy(update={"pipeline_overrides": overrides, "sp_budget_ttest": n})
        configure_and_apply_pipeline(session, cfg, log=lambda *_a, **_k: None)
        origin, _ = await prepare_scoring_context(
            samples,
            cfg,
            pipeline_params=session.pipeline_params,
            pipeline_schema=session.pipeline_schema,
            svc=session,
        )
        acc = origin.origin_acc
        hits, total = origin.hits, origin.total
        lo, hi = wilson_ci(hits, total)
        floor = constant_answer_floor(origin.origin_results)
        label, share = answer_concentration(origin.origin_results)
        band = classify_band(acc if total else None, floor, share)
        note = ""
        if band == BAND_FLOOR and share - floor >= COLLAPSE_EXCESS:
            # A cell sits at the floor two ways — the model cannot do the task, or it has
            # stopped trying and emits one label. Only the second is diagnosable from here,
            # and it is the one an operator would otherwise mistake for the first and go
            # shopping for a bigger model. (Measured: a bigger model hedged HARDER.)
            note = f'answers "{label}" on {share:.0%} of samples — collapsed, not reasoning'
        out.append(
            CellVerdict(
                dataset=dataset,
                target_model=model,
                provider=provider,
                origin_accuracy=acc if total else None,
                n=total,
                wilson_lo=lo,
                wilson_hi=hi,
                constant_floor=floor,
                predicted_share=share,
                band=band,
                measured_at=now_iso(),
                note=note,
            )
        )
    return out


def _error_cell(dataset: str, model: str, provider: str | None, note: str) -> CellVerdict:
    return CellVerdict(
        dataset=dataset,
        target_model=model,
        provider=provider,
        origin_accuracy=None,
        n=0,
        wilson_lo=0.0,
        wilson_hi=0.0,
        band="error",
        measured_at=now_iso(),
        note=note,
    )
